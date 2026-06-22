"""Entrenar el HistGBM 1X2 con features deep-stats.

Build training rows by walking the historical_matches table in chronological
order. For each match we:
  1. Build a TeamProfile for both teams using all deep observations BEFORE
     that match (no leak).
  2. Build the Elo-based features the way outcome_ml._replay does.
  3. Combine via outcome_ml_deep.build_deep_features.
  4. Label with the actual outcome.

We only keep rows where BOTH teams have at least min_sample effective
matches in their profile — otherwise the deep features are essentially
imputed from tournament means and add no signal.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from collections import defaultdict, deque

from wcpredict.names import canonical_team_name
from wcpredict.outcome_ml_deep import (
    DEEP_FEATURES, build_deep_features, save_deep_model, train_deep_outcome_model,
)
from wcpredict.outcome_ml import deduplicate_historical_rows
from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile


def _outcome_label(goals_a: int, goals_b: int) -> str:
    if goals_a > goals_b:
        return "home"
    if goals_a < goals_b:
        return "away"
    return "draw"


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()

    # 1. All historical matches in chronological order (used both for the Elo
    # replay and as the training set).
    raw = repo.list_historical_rows_before(datetime(2100, 1, 1, tzinfo=timezone.utc))
    print(f"Historical rows: {len(raw)}")

    # Walk raw rows ourselves so we keep team names alongside features.
    deduped = deduplicate_historical_rows(raw)
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    points: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
    goal_diffs: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))

    def _outcome(ga: int, gb: int) -> str:
        return "home" if ga > gb else "away" if gb > ga else "draw"

    base_rows = []
    for match in deduped:
        team_a, team_b = str(match["team_a"]), str(match["team_b"])
        feat = {
            "team_a": team_a, "team_b": team_b,
            "rating_diff": (ratings[team_a] - ratings[team_b]) / 400.0,
            "form_diff": (sum(points[team_a]) / len(points[team_a]) if points[team_a] else 1.0) - (sum(points[team_b]) / len(points[team_b]) if points[team_b] else 1.0),
            "goal_diff_form": (sum(goal_diffs[team_a]) / len(goal_diffs[team_a]) if goal_diffs[team_a] else 0.0) - (sum(goal_diffs[team_b]) / len(goal_diffs[team_b]) if goal_diffs[team_b] else 0.0),
            "neutral_site": int(bool(match.get("neutral_site", True))),
        }
        ga, gb = int(match["goals_a"]), int(match["goals_b"])
        result = _outcome(ga, gb)
        feat["outcome"] = result
        feat["played_at_utc"] = str(match["played_at_utc"])
        base_rows.append(feat)
        # Update Elo state for the next match
        actual_a = 1.0 if result == "home" else 0.5 if result == "draw" else 0.0
        expected_a = 1 / (1 + 10 ** ((ratings[team_b] - ratings[team_a]) / 400))
        delta = 24 * (actual_a - expected_a)
        ratings[team_a] += delta
        ratings[team_b] -= delta
        pa, pb = (3, 0) if result == "home" else (0, 3) if result == "away" else (1, 1)
        points[team_a].append(pa); points[team_b].append(pb)
        goal_diffs[team_a].append(ga - gb); goal_diffs[team_b].append(gb - ga)

    print(f"Base feature rows (with team names): {len(base_rows)}")

    # 2. Pull all deep observations once and index BY TEAM for fast per-match
    # profile construction. The naive approach would pass all 110k obs into
    # build_team_profile for every match (~5B ops); by pre-indexing per team
    # we pass only the few hundred relevant rows + a small sample for the
    # tournament-mean prior.
    deep_obs_all = repo.list_deep_team_metric_observations_before(
        datetime(2100, 1, 1, tzinfo=timezone.utc)
    )
    deep_obs_sorted = sorted(deep_obs_all, key=lambda r: str(r.get("kickoff_utc") or ""))
    obs_by_team: dict[str, list[dict]] = {}
    for r in deep_obs_sorted:
        team_key = canonical_team_name(str(r.get("team_name") or ""))
        obs_by_team.setdefault(team_key, []).append(r)
    print(f"Deep observations: {len(deep_obs_sorted)} across {len(obs_by_team)} teams")

    # Precompute a global "tournament-mean reference" set (every 50th obs) so
    # build_team_profile can compute per-metric tournament means without us
    # having to pass all 110k rows. The mean drifts slightly over time but
    # it's stable enough after the first few hundred matches that this is OK.
    sample_obs_for_tmean = deep_obs_sorted[::50]
    print(f"Tournament-mean sample size: {len(sample_obs_for_tmean)}")

    # 3. Pre-count observations per team to skip matches where either side
    # has fewer than the minimum required raw observations (which is a fast
    # upper bound on the effective sample weight). This avoids running the
    # full build_team_profile aggregation for matches that would be skipped
    # anyway — the naive O(N × K) loop was 5B ops; with the pre-filter we
    # build a profile only a few thousand times.
    min_raw_obs = 5  # at least 5 raw observations to attempt building a profile
    cutoff_date = "2018-01-01"  # before this there are essentially no deep stats
    # Per-team running count of available deep observations as we sweep time.
    obs_count: dict[str, int] = {}
    cursor = 0
    training_rows = []
    skipped_no_profile = 0
    skipped_early = 0
    for row in base_rows:
        row_date = str(row["played_at_utc"])
        if row_date < cutoff_date:
            # Even update obs_count would do nothing — there are no obs yet.
            skipped_early += 1
            continue
        while cursor < len(deep_obs_sorted) and str(deep_obs_sorted[cursor]["kickoff_utc"]) < row_date:
            team = canonical_team_name(str(deep_obs_sorted[cursor].get("team_name") or ""))
            obs_count[team] = obs_count.get(team, 0) + 1
            cursor += 1
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        ca = obs_count.get(canonical_team_name(team_a), 0)
        cb = obs_count.get(canonical_team_name(team_b), 0)
        # Cheap pre-filter: need at least min_raw_obs deep rows per side. Each
        # match contributes 50+ obs rows so this is a *very* permissive gate.
        if ca < min_raw_obs * 30 or cb < min_raw_obs * 30:
            skipped_no_profile += 1
            continue
        kickoff_dt = datetime.fromisoformat(row_date.replace("Z", "+00:00"))
        if kickoff_dt.tzinfo is None:
            kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
        # Build the per-team observation slice using only the rows for those
        # two teams (cheap) + the global sampled set for the tournament mean.
        team_a_key = canonical_team_name(team_a)
        team_b_key = canonical_team_name(team_b)
        rel_obs = (
            [r for r in obs_by_team.get(team_a_key, []) if str(r["kickoff_utc"]) < row_date]
            + [r for r in obs_by_team.get(team_b_key, []) if str(r["kickoff_utc"]) < row_date]
            + [r for r in sample_obs_for_tmean if str(r["kickoff_utc"]) < row_date]
        )
        profile_a = build_team_profile(team_a, rel_obs, kickoff_dt)
        profile_b = build_team_profile(team_b, rel_obs, kickoff_dt)
        if min(profile_a.sample_weight, profile_b.sample_weight) < 3:
            skipped_no_profile += 1
            continue
        features = build_deep_features(row, profile_a, profile_b)
        features["outcome"] = row["outcome"]
        features["played_at_utc"] = row_date
        training_rows.append(features)

    print(f"Training rows (both teams >=3 effective deep matches): {len(training_rows)}")
    print(f"Skipped — pre-2018: {skipped_early}")
    print(f"Skipped — insufficient profile sample: {skipped_no_profile}")
    if not training_rows:
        print("No training rows. Run the StatsBomb/eatpizzanot back-fills first.")
        return 1

    fitted = train_deep_outcome_model(training_rows, minimum_matches=150)
    if fitted.status != "ready":
        print(f"Model status={fitted.status}, reason={fitted.reason}")
        return 2
    save_deep_model(fitted, ROOT / "data" / "models" / "outcome_ml_deep.joblib")
    print(f"\nModel ready. n={fitted.sample_size}  validation_brier={fitted.validation_brier:.4f}")
    print(f"Training cutoff: {fitted.training_cutoff_utc}")
    print(f"Validation cutoff: {fitted.validation_cutoff_utc}")
    print(f"Features: {DEEP_FEATURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
