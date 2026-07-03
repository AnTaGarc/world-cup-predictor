"""Tournament-form adjustment layer (Phase 1 of the github_wc2026 rollout).

Shifts the 1X2 log-odds of the base models according to how each team is
performing *in this World Cup* (xG delta, late-game dominance, goal timing,
fatigue, numeric inferiority, momentum and discipline), all derived from the
gh_* tables. A single global alpha is calibrated against the pre-match
prediction snapshots already stored in the database; alpha <= 0 disables the
layer entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any


SHRINKAGE_K = 3.0
FORM_FEATURE_NAMES = (
    "xg_delta",
    "late_dominance",
    "goal_timing",
    "fatigue",
    "inferiority",
    "momentum",
    "discipline",
)


@dataclass(frozen=True)
class FormFeatures:
    team_name: str
    matches_played: int
    values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Adjustment:
    score: float
    weight: float
    matches_min: int = 0
    detail: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Calibration:
    alpha: float
    sample_size: int
    log_loss_base: float
    log_loss_adjusted: float


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _gh_kickoff_iso(row: dict) -> str:
    """The dataset stores kickoff_time_utc as a bare time ("21:00"); combine
    it with the date column into a sortable ISO timestamp. A full ISO value
    in kickoff_time_utc is used as-is."""
    time_part = str(row.get("kickoff_time_utc") or "")
    date_part = str(row.get("date") or "")
    if "T" in time_part or "-" in time_part:
        return time_part
    if date_part and time_part:
        return f"{date_part}T{time_part}"
    return date_part


def _team_gh_matches(con, team_name: str, before_kickoff_iso: str) -> list[dict]:
    cutoff = str(before_kickoff_iso)
    rows = con.execute(
        "SELECT gh.*, t.name AS resolved_name FROM gh_matches gh "
        "JOIN teams t ON t.id IN (gh.home_team_id, gh.away_team_id) "
        "WHERE lower(t.name) = lower(?) "
        "AND gh.home_score IS NOT NULL AND gh.away_score IS NOT NULL",
        (team_name,),
    ).fetchall()
    output = []
    for row in rows:
        data = dict(row)
        kickoff = _gh_kickoff_iso(data)
        if not kickoff or kickoff >= cutoff:
            continue
        data["kickoff_iso"] = kickoff
        home = bool(
            con.execute(
                "SELECT 1 FROM teams WHERE id=? AND lower(name)=lower(?)",
                (data["home_team_id"], team_name),
            ).fetchone()
        )
        data["is_home"] = home
        data["gf"] = data["home_score"] if home else data["away_score"]
        data["ga"] = data["away_score"] if home else data["home_score"]
        data["xg_for"] = data["home_xg"] if home else data["away_xg"]
        data["xg_against"] = data["away_xg"] if home else data["home_xg"]
        data["own_team_id"] = data["home_team_id"] if home else data["away_team_id"]
        output.append(data)
    output.sort(key=lambda item: item["kickoff_iso"])
    return output


def build_form_features(repo, team_name: str, before_kickoff_iso: str) -> FormFeatures:
    with repo.session() as con:
        matches = _team_gh_matches(con, team_name, before_kickoff_iso)
        if not matches:
            return FormFeatures(team_name=team_name, matches_played=0)
        n = len(matches)
        match_ids = tuple(m["external_match_id"] for m in matches)
        placeholders = ", ".join("?" for _ in match_ids)
        events = [
            dict(row) for row in con.execute(
                f"SELECT * FROM gh_match_events WHERE external_match_id IN ({placeholders})",
                match_ids,
            ).fetchall()
        ]
        tournament_cards_by_team: dict[int, int] = {}
        tournament_matches_by_team: dict[int, int] = {}
        for row in con.execute(
            "SELECT gh.home_team_id AS tid, COUNT(*) AS n FROM gh_matches gh "
            "WHERE gh.home_score IS NOT NULL GROUP BY gh.home_team_id"
        ):
            tournament_matches_by_team[row["tid"]] = row["n"]
        for row in con.execute(
            "SELECT gh.away_team_id AS tid, COUNT(*) AS n FROM gh_matches gh "
            "WHERE gh.home_score IS NOT NULL GROUP BY gh.away_team_id"
        ):
            tournament_matches_by_team[row["tid"]] = (
                tournament_matches_by_team.get(row["tid"], 0) + row["n"]
            )
        for row in con.execute(
            "SELECT e.team_id AS tid, COUNT(*) AS n FROM gh_match_events e "
            "WHERE e.event_type IN ('Yellow Card', 'Red Card') AND e.team_id IS NOT NULL "
            "GROUP BY e.team_id"
        ):
            tournament_cards_by_team[row["tid"]] = row["n"]

    own_team_id = matches[0]["own_team_id"]
    by_match: dict[int, list[dict]] = {}
    for event in events:
        by_match.setdefault(event["external_match_id"], []).append(event)

    xg_samples = [
        float(m["xg_for"]) - float(m["xg_against"])
        for m in matches
        if m["xg_for"] is not None and m["xg_against"] is not None
    ]
    values: dict[str, float] = {}
    if xg_samples:
        values["xg_delta"] = _clip((sum(xg_samples) / len(xg_samples)) / 1.5)

    late_for = late_against = 0
    scoring_minutes: list[float] = []
    inferiority_minutes = 0.0
    for m in matches:
        match_events = by_match.get(m["external_match_id"], [])
        max_minute = max((e["minute"] for e in match_events), default=90)
        # Events between 91' and 105' are usually long stoppage time, not
        # extra time (the dataset stores absolute minutes without periods).
        horizon = 120 if max_minute > 105 else max(90, max_minute)
        for e in match_events:
            own = e.get("team_id") == own_team_id
            if e["event_type"] == "Goal":
                if own:
                    scoring_minutes.append(float(e["minute"]))
                if 75 <= e["minute"] <= 90:
                    late_for += int(own)
                    late_against += int(not own)
            elif e["event_type"] == "Red Card" and own:
                inferiority_minutes += max(0, horizon - e["minute"])
    values["late_dominance"] = _clip((late_for - late_against) / max(1, n))
    if scoring_minutes:
        values["goal_timing"] = _clip(
            -((sum(scoring_minutes) / len(scoring_minutes)) - 60.0) / 60.0
        )
    values["inferiority"] = _clip(-(inferiority_minutes / max(1, n)) / 45.0)

    last = matches[-1]
    last_kickoff = str(last.get("kickoff_iso") or "")
    days_rest = 7.0
    try:
        last_dt = datetime.fromisoformat(last_kickoff.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        target = datetime.fromisoformat(str(before_kickoff_iso).replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        days_rest = max(0.0, (target - last_dt).total_seconds() / 86400.0)
    except ValueError:
        pass
    et_minutes_14d = 0.0
    for m in matches:
        match_events = by_match.get(m["external_match_id"], [])
        max_minute = max((e["minute"] for e in match_events), default=90)
        if max_minute <= 105:
            continue
        kick = str(m.get("kickoff_iso") or "")
        try:
            kick_dt = datetime.fromisoformat(kick.replace("Z", "+00:00"))
            if kick_dt.tzinfo is None:
                kick_dt = kick_dt.replace(tzinfo=timezone.utc)
            target = datetime.fromisoformat(str(before_kickoff_iso).replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            if (target - kick_dt).total_seconds() <= 14 * 86400:
                et_minutes_14d += max_minute - 90
        except ValueError:
            continue
    values["fatigue"] = _clip(
        0.5 * min(days_rest, 7.0) / 7.0 - 0.5 * (et_minutes_14d / 30.0)
    )

    values["momentum"] = 1.0 if last["gf"] > last["ga"] else (-1.0 if last["gf"] < last["ga"] else 0.0)

    own_cards = tournament_cards_by_team.get(own_team_id, 0)
    total_cards = sum(tournament_cards_by_team.values())
    total_team_matches = sum(tournament_matches_by_team.values())
    tournament_card_mean = (total_cards / total_team_matches) if total_team_matches else 0.0
    values["discipline"] = _clip(-((own_cards / n) - tournament_card_mean) / 3.0)

    return FormFeatures(team_name=team_name, matches_played=n, values=values)


def build_match_adjustment(repo, team_a: str, team_b: str, kickoff_iso: str) -> Adjustment:
    features_a = build_form_features(repo, team_a, kickoff_iso)
    features_b = build_form_features(repo, team_b, kickoff_iso)
    if features_a.matches_played == 0 or features_b.matches_played == 0:
        return Adjustment(score=0.0, weight=0.0)
    deltas: dict[str, float] = {}
    for name in FORM_FEATURE_NAMES:
        value_a = features_a.values.get(name)
        value_b = features_b.values.get(name)
        if value_a is None or value_b is None:
            continue
        deltas[name] = _clip(value_a - value_b, -2.0, 2.0) / 2.0
    if not deltas:
        return Adjustment(score=0.0, weight=0.0)
    score = _clip(sum(deltas.values()) / len(deltas))
    smallest = min(features_a.matches_played, features_b.matches_played)
    weight = smallest / (smallest + SHRINKAGE_K)
    return Adjustment(score=score, weight=weight, matches_min=smallest, detail=deltas)


def apply_form_adjustment(
    probabilities: dict[str, float], alpha: float, score: float, weight: float
) -> dict[str, float]:
    if alpha <= 0.0 or weight <= 0.0 or score == 0.0:
        return dict(probabilities)
    shift = alpha * weight * score
    logits = {
        outcome: math.log(max(1e-9, probabilities.get(outcome, 1e-9)))
        for outcome in ("home", "draw", "away")
    }
    logits["home"] += shift
    logits["away"] -= shift
    peak = max(logits.values())
    exponentials = {k: math.exp(v - peak) for k, v in logits.items()}
    total = sum(exponentials.values())
    return {k: v / total for k, v in exponentials.items()}


def _log_loss(samples: list[dict], alpha: float) -> float:
    total = 0.0
    for sample in samples:
        probabilities = apply_form_adjustment(
            sample["probs"], alpha, sample["score"], sample["weight"]
        )
        total -= math.log(max(1e-9, probabilities[sample["outcome"]]))
    return total / max(1, len(samples))


def calibrate_alpha(
    samples: list[dict], *, ridge: float = 0.01, grid_max: float = 1.5, grid_step: float = 0.05
) -> Calibration:
    if not samples:
        return Calibration(alpha=0.0, sample_size=0, log_loss_base=0.0, log_loss_adjusted=0.0)
    base_loss = _log_loss(samples, 0.0)
    best_alpha = 0.0
    best_objective = base_loss
    alpha = grid_step
    while alpha <= grid_max + 1e-9:
        objective = _log_loss(samples, alpha) + ridge * alpha * alpha
        if objective < best_objective:
            best_objective = objective
            best_alpha = alpha
        alpha += grid_step
    adjusted_loss = _log_loss(samples, best_alpha)
    if adjusted_loss >= base_loss:
        best_alpha = 0.0
        adjusted_loss = base_loss
    return Calibration(
        alpha=round(best_alpha, 4),
        sample_size=len(samples),
        log_loss_base=base_loss,
        log_loss_adjusted=adjusted_loss,
    )


def build_calibration_samples(repo) -> list[dict[str, Any]]:
    """Pair each closed WC2026 match having stored pre-match probabilities
    with its tournament-form adjustment (computed strictly before kickoff)
    and the real outcome.

    Two honest bases are combined, preferring the first when both exist:
    1. `prediction_snapshots`: the earliest snapshot per match, generated
       before kickoff by the app itself.
    2. `backtest_runs` pool `live-wc2026-v1`: live rows written by the
       settlement hook plus the pre-kickoff reconstruction produced by
       scripts/backfill_live_residuals.py (same pipeline, temporal cutoff
       at kickoff). This is the pool the phase-5b team residuals already
       rely on, extending coverage to the whole group stage.
    """
    import json

    base_probs: dict[int, dict[str, float]] = {}
    match_meta: dict[int, dict[str, Any]] = {}
    with repo.session() as con:
        closed = con.execute(
            "SELECT m.id AS match_id, m.kickoff_utc, ta.name AS team_a, "
            "tb.name AS team_b, mr.goals_a, mr.goals_b "
            "FROM matches m "
            "JOIN teams ta ON ta.id = m.team_a_id "
            "JOIN teams tb ON tb.id = m.team_b_id "
            "JOIN match_results mr ON mr.match_id = m.id "
            "WHERE m.competition = 'FIFA World Cup 2026'"
        ).fetchall()
        for row in closed:
            match_meta[int(row["match_id"])] = dict(row)

        # Base 2 first (lower precedence): live-wc2026-v1 backtest pool.
        pool = con.execute(
            "SELECT match_id, selection, prob_predicted FROM backtest_runs "
            "WHERE run_label = 'live-wc2026-v1' AND market = '1X2'"
        ).fetchall()
        by_match: dict[int, dict[str, float]] = {}
        for row in pool:
            by_match.setdefault(int(row["match_id"]), {})[str(row["selection"])] = float(
                row["prob_predicted"]
            )
        for match_id, selections in by_match.items():
            meta = match_meta.get(match_id)
            if meta is None:
                continue
            home_p = selections.get(str(meta["team_a"]))
            away_p = selections.get(str(meta["team_b"]))
            draw_p = selections.get("Draw") or selections.get("Empate")
            if home_p is None or away_p is None or draw_p is None:
                continue
            base_probs[match_id] = {"home": home_p, "draw": draw_p, "away": away_p}

        # Base 1 (higher precedence): earliest pre-match snapshot.
        snapshots = con.execute(
            "SELECT ps.match_id, ps.payload_json FROM prediction_snapshots ps "
            "WHERE ps.id IN ("
            "  SELECT MIN(ps2.id) FROM prediction_snapshots ps2 GROUP BY ps2.match_id"
            ")"
        ).fetchall()
    for row in snapshots:
        match_id = int(row["match_id"])
        meta = match_meta.get(match_id)
        if meta is None:
            continue
        payload = json.loads(row["payload_json"])
        one_x_two = {
            str(p.get("selection_name")): float(p.get("probability"))
            for p in payload.get("predictions", [])
            if p.get("market_name") == "1X2" and p.get("probability") is not None
        }
        home_p = one_x_two.get(str(meta["team_a"]))
        away_p = one_x_two.get(str(meta["team_b"]))
        draw_p = one_x_two.get("Draw") or one_x_two.get("Empate")
        if home_p is None or away_p is None or draw_p is None:
            continue
        base_probs[match_id] = {"home": home_p, "draw": draw_p, "away": away_p}

    samples: list[dict[str, Any]] = []
    for match_id, probs in sorted(base_probs.items()):
        meta = match_meta[match_id]
        adjustment = build_match_adjustment(
            repo, str(meta["team_a"]), str(meta["team_b"]), str(meta["kickoff_utc"])
        )
        goals_a, goals_b = int(meta["goals_a"]), int(meta["goals_b"])
        outcome = "home" if goals_a > goals_b else ("away" if goals_a < goals_b else "draw")
        samples.append({
            "match_id": match_id,
            "probs": probs,
            "score": adjustment.score,
            "weight": adjustment.weight,
            "matches_min": adjustment.matches_min,
            "outcome": outcome,
        })
    return samples


# Buckets for the stratified alpha: teams' minimum tournament matches played.
# Below 2 matches the layer stays off (weight is negligible and no stratum
# has shown signal there); each bucket calibrates and activates on its own.
ALPHA_BUCKETS = ("2", "3plus")
MIN_BUCKET_IMPROVEMENT = 0.03


def bucket_for_matches(matches_min: int) -> str | None:
    if matches_min >= 3:
        return "3plus"
    if matches_min == 2:
        return "2"
    return None


def calibrate_stratified(samples: list[dict]) -> dict[str, dict]:
    """Calibrate one alpha per matches-played bucket, activating each bucket
    only when its own improvement clears MIN_BUCKET_IMPROVEMENT."""
    report: dict[str, dict] = {}
    for bucket in ALPHA_BUCKETS:
        subset = [
            s for s in samples
            if bucket_for_matches(int(s.get("matches_min", 0))) == bucket
        ]
        calibration = calibrate_alpha(subset)
        alpha = calibration.alpha
        improvement = (
            (calibration.log_loss_base - calibration.log_loss_adjusted)
            / calibration.log_loss_base
            if calibration.log_loss_base > 0 else 0.0
        )
        if improvement < MIN_BUCKET_IMPROVEMENT:
            alpha = 0.0
        report[bucket] = {
            "alpha": alpha,
            "alpha_raw": calibration.alpha,
            "sample_size": calibration.sample_size,
            "log_loss_base": calibration.log_loss_base,
            "log_loss_adjusted": calibration.log_loss_adjusted,
            "improvement": improvement,
        }
    return report


def alpha_for_match(alphas: dict[str, float], matches_min: int) -> float:
    if matches_min < 2:
        return 0.0
    if "global" in alphas:
        return float(alphas["global"])
    bucket = bucket_for_matches(matches_min)
    if bucket is None:
        return 0.0
    return float(alphas.get(bucket, 0.0))
