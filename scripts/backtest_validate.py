"""Walk-forward validation of Phase 5 team corrections.

For each closed WC2026 match in chronological order:
  1. Compute per-team market shifts FROM the backtest_runs of matches
     played strictly before this one (no leakage).
  2. Re-run the predict pipeline (profiles + GK + deep ML) passing those
     shifts as ``team_corrections``.
  3. Compare against the model without corrections.

Output: tabla comparativa final (Fase 6).
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.advanced_form import build_goalkeeper_baseline, build_xg_form_adjustment, goalkeeper_xg_factor
from wcpredict.outcome_ml_deep import build_deep_features, load_deep_model
from wcpredict.repository import Repository
from wcpredict.services import predict_match_markets
from wcpredict.team_corrections import compute_team_market_shifts
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile

DB = ROOT / "data" / "worldcup.sqlite"
DEEP_MODEL_PATH = ROOT / "data" / "models" / "outcome_ml_deep.joblib"
COMPETITION = "FIFA World Cup 2026"


def _shifts_for_history(history_rows) -> dict[str, dict[str, float]]:
    """{team_name: {'1X2': logit_shift, ...}} from past residuals."""
    shifts = compute_team_market_shifts(history_rows, min_n=2, prior_strength=4)
    by_team: dict[str, dict[str, float]] = {}
    for (team, market), shift in shifts.items():
        if market == "1X2":
            # Negative residual (under-prediction) → positive logit shift.
            by_team.setdefault(team, {})["1X2"] = -shift
    return by_team


def _load_history(repo: Repository, source_label: str, before_kickoff: str):
    """Backtest rows from `source_label` for matches before this kickoff."""
    with sqlite3.connect(repo.path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT br.market, br.selection, br.prob_predicted, br.outcome_observed, "
            "ta.name AS team_a, tb.name AS team_b "
            "FROM backtest_runs br "
            "JOIN matches m ON m.id=br.match_id "
            "JOIN teams ta ON ta.id=m.team_a_id "
            "JOIN teams tb ON tb.id=m.team_b_id "
            "WHERE br.run_label=? AND m.kickoff_utc < ?",
            (source_label, before_kickoff),
        ).fetchall()
    return [dict(r) for r in rows]


def _closed_matches(repo: Repository):
    with sqlite3.connect(repo.path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT m.id, m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
            "mr.goals_a, mr.goals_b "
            "FROM match_results mr JOIN matches m ON m.id=mr.match_id "
            "JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
            "WHERE m.competition=? ORDER BY m.kickoff_utc, m.id",
            (COMPETITION,),
        ).fetchall()
    return [dict(r) for r in rows]


def _hist_results_before(repo: Repository, as_of: datetime):
    return repo.list_historical_results_before(as_of) + repo.list_match_results_before(as_of)


def run() -> int:
    repo = Repository(DB)
    repo.initialize()
    matches = _closed_matches(repo)
    deep_model = load_deep_model(DEEP_MODEL_PATH)

    print(f"Walk-forward sobre {len(matches)} partidos cerrados\n")
    print("Source para residuos: 'fase4-full-v2' (profiles+GK+deepML)\n")

    agg = {
        "n": 0, "hits_1x2_with": 0, "hits_1x2_without": 0,
        "brier_1x2_with": 0.0, "brier_1x2_without": 0.0,
        "shifts_per_match": [],
    }

    for m in matches:
        kickoff = datetime.fromisoformat(m["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        history = _load_history(repo, "fase4-full-v2", as_of.isoformat())
        team_shifts = _shifts_for_history(history)
        active_shifts = sum(1 for t in (m["team_a"], m["team_b"]) if team_shifts.get(t, {}).get("1X2"))
        agg["shifts_per_match"].append(active_shifts)

        # Re-build inputs (same as backtest_replay v2).
        results = _hist_results_before(repo, as_of)
        deep_xg_rows = repo.list_deep_xg_rows_before(as_of)
        advanced = build_xg_form_adjustment(m["team_a"], m["team_b"], deep_xg_rows, as_of)
        deep_rows = repo.list_deep_team_metric_observations_before(as_of)
        profile_a = build_team_profile(m["team_a"], deep_rows, as_of)
        profile_b = build_team_profile(m["team_b"], deep_rows, as_of)
        factor_a, factor_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
        gk_rows = repo.list_deep_goalkeeper_rows_before(as_of)
        gk_a = build_goalkeeper_baseline(m["team_a"], gk_rows, as_of)
        gk_b = build_goalkeeper_baseline(m["team_b"], gk_rows, as_of)
        factor_a *= goalkeeper_xg_factor(gk_b)
        factor_b *= goalkeeper_xg_factor(gk_a)
        if advanced is not None:
            from dataclasses import replace as _replace
            advanced = _replace(
                advanced,
                factor_a=advanced.factor_a * factor_a,
                factor_b=advanced.factor_b * factor_b,
            )

        base = {"rating_diff": 0.0, "form_diff": 0.0, "goal_diff_form": 0.0, "neutral_site": 1}
        deep_feats = build_deep_features(base, profile_a, profile_b)
        deep_probs = deep_model.predict(deep_feats) if deep_model.status == "ready" else None

        def _predict(with_corrections: bool):
            return predict_match_markets(
                m["team_a"], m["team_b"], results, as_of.date(),
                advanced_form=advanced,
                outcome_probabilities=deep_probs,
                outcome_weight=0.55 if deep_probs else 0.80,
                team_corrections=team_shifts if with_corrections else None,
            )

        preds_with = _predict(True)
        preds_without = _predict(False)

        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"

        for label, preds, suffix in (
            ("with", preds_with, "with"),
            ("without", preds_without, "without"),
        ):
            ph = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_a"])
            pd = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == "Draw")
            pa = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_b"])
            outcome = {"home": ph, "draw": pd, "away": pa}
            win_prob = outcome[winner]
            agg[f"brier_1x2_{suffix}"] += sum(
                (p - (1 if winner == k else 0)) ** 2 for k, p in outcome.items()
            )
            pick = max(outcome, key=outcome.get)
            if pick == winner:
                agg[f"hits_1x2_{suffix}"] += 1
        agg["n"] += 1

    n = agg["n"] or 1
    print("=" * 64)
    print(f"  Walk-forward Fase 6 sobre {n} partidos")
    print("=" * 64)
    avg_shifts = sum(agg["shifts_per_match"]) / n
    print(f"  Equipos con correcciones activas (media): {avg_shifts:.2f}/2")
    print(f"  Brier 1X2  sin correcciones: {agg['brier_1x2_without']/n:.4f}")
    print(f"  Brier 1X2  con correcciones: {agg['brier_1x2_with']/n:.4f}")
    print(f"  Hit 1X2    sin correcciones: {agg['hits_1x2_without']/n:.1%} ({agg['hits_1x2_without']}/{n})")
    print(f"  Hit 1X2    con correcciones: {agg['hits_1x2_with']/n:.1%} ({agg['hits_1x2_with']}/{n})")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
