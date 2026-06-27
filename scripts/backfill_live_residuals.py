"""Retro-fill the live-wc2026-v1 residual pool for every WC2026 match
that was settled BEFORE the EMA hook was wired into settle_match_versioned.

For each closed WC2026 match without a corresponding live residual row,
this script:

  1. Reconstructs the prediction the model would have produced just
     before kickoff (uses team_profile + advanced_form, score-only —
     same pipeline the snapshot would have captured).
  2. Computes the 1X2 probabilities and the actual outcome.
  3. Persists three rows into ``backtest_runs`` with
     ``run_label='live-wc2026-v1'`` and ``model_version='unified-live-backfill'``.

The hook continues to write fresh residuals going forward; this script
just bootstraps the past.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.advanced_form import build_xg_form_adjustment
from wcpredict.repository import Repository
from wcpredict.services import predict_match_markets
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile

DB = ROOT / "data" / "worldcup.sqlite"
COMPETITION = "FIFA World Cup 2026"
RUN_LABEL = "live-wc2026-v1"
MODEL_VERSION = "unified-live-backfill"


def _closed_matches(con):
    rows = con.execute(
        "SELECT m.id, m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
        "mr.goals_a, mr.goals_b "
        "FROM match_results mr JOIN matches m ON m.id=mr.match_id "
        "JOIN teams ta ON ta.id=m.team_a_id "
        "JOIN teams tb ON tb.id=m.team_b_id "
        "WHERE m.competition=? ORDER BY m.kickoff_utc, m.id",
        (COMPETITION,),
    ).fetchall()
    return [dict(r) for r in rows]


def _existing_match_ids(con) -> set[int]:
    return {
        int(r[0]) for r in con.execute(
            "SELECT DISTINCT match_id FROM backtest_runs WHERE run_label=?",
            (RUN_LABEL,),
        )
    }


def _preload_results(repo):
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    rows = repo.list_historical_results_before(far_future) + repo.list_match_results_before(far_future)
    rows.sort(key=lambda r: r.played_on)
    return rows


def _results_before(sorted_results, as_of):
    import bisect
    keys = [r.played_on for r in sorted_results]
    idx = bisect.bisect_left(keys, as_of.date())
    return sorted_results[:idx]


def _preload_deep_rows(repo):
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    rows = repo.list_deep_team_metric_observations_before(far_future)
    rows.sort(key=lambda r: str(r.get("kickoff_utc") or ""))
    return rows


def _index_by_team(deep_rows):
    from wcpredict.names import canonical_team_name
    by_team: dict[str, list[dict]] = {}
    for r in deep_rows:
        team = canonical_team_name(str(r.get("team_name") or ""))
        if team:
            by_team.setdefault(team, []).append(r)
    return by_team


def _team_slice(team_index, team, iso_cutoff):
    import bisect
    from wcpredict.names import canonical_team_name
    rows = team_index.get(canonical_team_name(team), ())
    if not rows:
        return []
    keys = [str(r.get("kickoff_utc") or "") for r in rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return rows[:idx]


def _sample_slice(sorted_rows, iso_cutoff):
    import bisect
    keys = [str(r.get("kickoff_utc") or "") for r in sorted_rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return sorted_rows[:idx]


def _persist(con, match_id, selection, prob, outcome_observed, recorded_at):
    brier = (prob - outcome_observed) ** 2
    p_clip = max(1e-6, min(1.0 - 1e-6, prob if outcome_observed == 1 else 1.0 - prob))
    log_loss = -math.log(p_clip)
    con.execute(
        "INSERT INTO backtest_runs(run_label, model_version, match_id, market, selection, "
        "prob_predicted, outcome_observed, brier, log_loss, extra_json, recorded_at_utc) "
        "VALUES(?, ?, ?, '1X2', ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_label, model_version, match_id, market, selection) DO UPDATE SET "
        "prob_predicted=excluded.prob_predicted, outcome_observed=excluded.outcome_observed, "
        "brier=excluded.brier, log_loss=excluded.log_loss, recorded_at_utc=excluded.recorded_at_utc",
        (RUN_LABEL, MODEL_VERSION, int(match_id), selection,
         float(prob), int(outcome_observed), float(brier), float(log_loss),
         '{"source":"backfill_live_residuals"}', recorded_at),
    )


def main() -> int:
    repo = Repository(DB)
    repo.initialize()
    con = sqlite3.connect(repo.path)
    con.row_factory = sqlite3.Row

    closed = _closed_matches(con)
    existing = _existing_match_ids(con)
    pending = [m for m in closed if int(m["id"]) not in existing]
    print(f"Cerrados WC2026: {len(closed)}", flush=True)
    print(f"Ya con residuo live: {len(existing)}", flush=True)
    print(f"A rellenar:           {len(pending)}", flush=True)
    if not pending:
        print("Nada que hacer.", flush=True)
        return 0

    print("Precargando datasets...", flush=True)
    all_results = _preload_results(repo)
    all_deep = _preload_deep_rows(repo)
    deep_by_team = _index_by_team(all_deep)
    sample = all_deep[::40]
    print(f"  results: {len(all_results)}, deep: {len(all_deep)}, sample: {len(sample)}", flush=True)

    recorded_at = datetime.now(timezone.utc).isoformat()
    processed = 0
    for m in pending:
        kickoff = datetime.fromisoformat(m["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        as_of_iso = as_of.isoformat()

        results = _results_before(all_results, as_of)
        deep_xg_rows = repo.list_deep_xg_rows_before(as_of)
        advanced = build_xg_form_adjustment(m["team_a"], m["team_b"], deep_xg_rows, as_of)

        rel_deep = (
            _team_slice(deep_by_team, m["team_a"], as_of_iso)
            + _team_slice(deep_by_team, m["team_b"], as_of_iso)
            + _sample_slice(sample, as_of_iso)
        )
        profile_a = build_team_profile(m["team_a"], rel_deep, as_of)
        profile_b = build_team_profile(m["team_b"], rel_deep, as_of)
        factor_a, factor_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
        if advanced is not None:
            from dataclasses import replace as _replace
            advanced = _replace(
                advanced,
                factor_a=advanced.factor_a * factor_a,
                factor_b=advanced.factor_b * factor_b,
            )

        preds = predict_match_markets(
            m["team_a"], m["team_b"], results, as_of.date(),
            advanced_form=advanced,
            outcome_probabilities=None,
        )
        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"
        p_home = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_a"]), 0.0)
        p_draw = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == "Draw"), 0.0)
        p_away = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_b"]), 0.0)
        for sel, prob, observed in (
            (m["team_a"], p_home, 1 if winner == "home" else 0),
            ("Draw", p_draw, 1 if winner == "draw" else 0),
            (m["team_b"], p_away, 1 if winner == "away" else 0),
        ):
            _persist(con, m["id"], sel, prob, observed, recorded_at)
        processed += 1
        if processed % 10 == 0:
            con.commit()
            print(f"  {processed}/{len(pending)} processed", flush=True)
    con.commit()
    con.close()
    print(f"\nDone. Backfilled {processed} matches.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
