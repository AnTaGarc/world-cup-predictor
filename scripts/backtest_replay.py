"""Replay the prediction pipeline against the 60 closed WC2026 matches.

Baseline path (no ML ensemble, no deep ML, no corrections):
  * Build ratings from historical results strictly before each match's
    kickoff (so the model sees only what it could have seen).
  * Optionally enrich with ``build_xg_form_adjustment`` for the deep-form
    factor (already part of the live pipeline).
  * Compute the score matrix via ``predict_match_markets`` with
    ``outcome_probabilities=None`` so we measure the *xG / Dixon-Coles*
    baseline alone — the same path the UI calls ``score_only_predictions``.

Stored in the ``backtest_runs`` table (one row per match × market) so later
phases can re-run with the enhanced pipeline and compare side-by-side.

Usage:
    python scripts/backtest_replay.py --label baseline-pre-fase2

Prints the headline metrics (Brier 1X2, log loss, top-1 / top-3 marcador,
BTTS, O/U 2.5) at the end.
"""
from __future__ import annotations

import argparse
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

DB = ROOT / "data" / "worldcup.sqlite"
COMPETITION = "FIFA World Cup 2026"
MODEL_VERSION_BASELINE = "baseline-score-only-v1"


def _hist_results_before(repo: Repository, as_of: datetime):
    """Combine match_results (WC2026 closed matches before this kickoff)
    and historical_matches into the unified MatchResult list the rating
    engine expects."""
    local = repo.list_match_results_before(as_of)
    historic = repo.list_historical_results_before(as_of)
    return historic + local


def _closed_matches(repo: Repository) -> list[dict]:
    with sqlite3.connect(repo.path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT m.id, m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
            "  mr.goals_a, mr.goals_b "
            "FROM match_results mr "
            "JOIN matches m ON m.id=mr.match_id "
            "JOIN teams ta ON ta.id=m.team_a_id "
            "JOIN teams tb ON tb.id=m.team_b_id "
            "WHERE m.competition=? "
            "ORDER BY m.kickoff_utc, m.id",
            (COMPETITION,),
        ).fetchall()
    return [dict(r) for r in rows]


def _log_loss(p: float) -> float:
    p = max(1e-6, min(1.0 - 1e-6, p))
    return -math.log(p)


def _brier(p: float, hit: int) -> float:
    return (p - hit) ** 2


def _persist_run(
    repo: Repository,
    run_label: str,
    model_version: str,
    match_id: int,
    market: str,
    selection: str | None,
    prob: float,
    outcome: int,
    extra: dict | None = None,
):
    brier = _brier(prob, outcome)
    log_loss = _log_loss(prob if outcome == 1 else 1.0 - prob)
    with sqlite3.connect(repo.path) as con:
        con.execute(
            "INSERT INTO backtest_runs(run_label, model_version, match_id, market, selection, "
            "prob_predicted, outcome_observed, brier, log_loss, extra_json, recorded_at_utc) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_label, model_version, match_id, market, selection) DO UPDATE SET "
            "prob_predicted=excluded.prob_predicted, outcome_observed=excluded.outcome_observed, "
            "brier=excluded.brier, log_loss=excluded.log_loss, extra_json=excluded.extra_json, "
            "recorded_at_utc=excluded.recorded_at_utc",
            (
                run_label, model_version, int(match_id), market, selection,
                float(prob), int(outcome), float(brier), float(log_loss),
                __import__("json").dumps(extra or {}, ensure_ascii=False, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()


def replay(run_label: str = "baseline-fase1") -> dict:
    repo = Repository(DB)
    repo.initialize()
    matches = _closed_matches(repo)
    print(f"Partidos cerrados WC2026 a replicar: {len(matches)}")

    aggregate = {
        "brier_1x2": 0.0, "logloss_1x2": 0.0, "n_1x2": 0,
        "hits_1x2": 0,
        "brier_btts": 0.0, "hits_btts": 0, "n_btts": 0,
        "brier_ou25": 0.0, "hits_ou25": 0, "n_ou25": 0,
        "top1_exact": 0, "top3_exact": 0, "n_exact": 0,
    }
    for m in matches:
        kickoff = datetime.fromisoformat(m["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        results = _hist_results_before(repo, as_of)
        # Deep-form adjustment uses the same data-as-of cut-off (no leakage).
        deep_xg_rows = repo.list_deep_xg_rows_before(as_of)
        advanced = build_xg_form_adjustment(m["team_a"], m["team_b"], deep_xg_rows, as_of)

        predictions = predict_match_markets(
            m["team_a"], m["team_b"], results, as_of.date(),
            advanced_form=advanced,
            outcome_probabilities=None,        # baseline: score-only, no ML
            deep_outcome_probabilities=None,
        )

        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"

        # 1X2 metrics
        prob_home = next((p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == m["team_a"]), 0.0)
        prob_draw = next((p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == "Draw"), 0.0)
        prob_away = next((p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == m["team_b"]), 0.0)
        outcome_map = {"home": prob_home, "draw": prob_draw, "away": prob_away}
        winning_prob = outcome_map[winner]
        aggregate["logloss_1x2"] += _log_loss(winning_prob)
        aggregate["brier_1x2"] += sum(
            (p - (1 if winner == key else 0)) ** 2
            for key, p in outcome_map.items()
        )
        aggregate["n_1x2"] += 1
        predicted_pick = max(outcome_map, key=outcome_map.get)
        if predicted_pick == winner:
            aggregate["hits_1x2"] += 1
        for sel, prob, outcome in (
            (m["team_a"], prob_home, 1 if winner == "home" else 0),
            ("Draw", prob_draw, 1 if winner == "draw" else 0),
            (m["team_b"], prob_away, 1 if winner == "away" else 0),
        ):
            _persist_run(repo, run_label, MODEL_VERSION_BASELINE, m["id"], "1X2", sel, prob, outcome)

        # Over/Under 2.5
        prob_over25 = next((p.probability for p in predictions if p.market_name == "Over/Under 2.5" and "Over" in p.selection_name), 0.0)
        over25_hit = 1 if (ga + gb) > 2.5 else 0
        aggregate["brier_ou25"] += _brier(prob_over25, over25_hit)
        if (prob_over25 >= 0.5 and over25_hit == 1) or (prob_over25 < 0.5 and over25_hit == 0):
            aggregate["hits_ou25"] += 1
        aggregate["n_ou25"] += 1
        _persist_run(repo, run_label, MODEL_VERSION_BASELINE, m["id"], "Over/Under 2.5", "Over 2.5", prob_over25, over25_hit)

        # BTTS
        prob_btts = next((p.probability for p in predictions if p.market_name == "Both Teams To Score" and p.selection_name == "Yes"), 0.0)
        btts_hit = 1 if (ga > 0 and gb > 0) else 0
        aggregate["brier_btts"] += _brier(prob_btts, btts_hit)
        if (prob_btts >= 0.5 and btts_hit == 1) or (prob_btts < 0.5 and btts_hit == 0):
            aggregate["hits_btts"] += 1
        aggregate["n_btts"] += 1
        _persist_run(repo, run_label, MODEL_VERSION_BASELINE, m["id"], "BTTS", "Yes", prob_btts, btts_hit)

        # Marcadores exactos: top-1 y top-3
        exact_rows = [(p.selection_name, p.probability) for p in predictions
                      if p.market_name in ("Exact Score", "Exact Score (alt)")]
        # Order by probability desc
        exact_rows.sort(key=lambda x: -x[1])
        true_label = f"{ga}-{gb}"
        if exact_rows:
            top1 = exact_rows[0][0].split(" ")[0]
            if top1 == true_label:
                aggregate["top1_exact"] += 1
            top3 = [r[0].split(" ")[0] for r in exact_rows[:3]]
            if true_label in top3:
                aggregate["top3_exact"] += 1
            aggregate["n_exact"] += 1
            _persist_run(repo, run_label, MODEL_VERSION_BASELINE, m["id"],
                         "Exact Score Top1", top1, 1.0 if top1 == true_label else 0.0,
                         1 if top1 == true_label else 0, extra={"true": true_label})
            _persist_run(repo, run_label, MODEL_VERSION_BASELINE, m["id"],
                         "Exact Score Top3", "|".join(top3), 1.0 if true_label in top3 else 0.0,
                         1 if true_label in top3 else 0, extra={"true": true_label})

    return aggregate


def _print_summary(agg: dict):
    n = agg["n_1x2"] or 1
    print()
    print("=" * 64)
    print(f"  Baseline ({MODEL_VERSION_BASELINE}) sobre {n} partidos cerrados")
    print("=" * 64)
    print(f"  Brier 1X2      : {agg['brier_1x2']/n:.4f}")
    print(f"  Log loss 1X2   : {agg['logloss_1x2']/n:.4f}")
    print(f"  Hit rate 1X2   : {agg['hits_1x2']/n:.1%}  ({agg['hits_1x2']}/{n})")
    print(f"  Brier O/U 2.5  : {agg['brier_ou25']/(agg['n_ou25'] or 1):.4f}")
    print(f"  Hit O/U 2.5    : {agg['hits_ou25']/(agg['n_ou25'] or 1):.1%}  ({agg['hits_ou25']}/{agg['n_ou25']})")
    print(f"  Brier BTTS     : {agg['brier_btts']/(agg['n_btts'] or 1):.4f}")
    print(f"  Hit BTTS       : {agg['hits_btts']/(agg['n_btts'] or 1):.1%}  ({agg['hits_btts']}/{agg['n_btts']})")
    print(f"  Marcador top-1 : {agg['top1_exact']/(agg['n_exact'] or 1):.1%}  ({agg['top1_exact']}/{agg['n_exact']})")
    print(f"  Marcador top-3 : {agg['top3_exact']/(agg['n_exact'] or 1):.1%}  ({agg['top3_exact']}/{agg['n_exact']})")
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="baseline-fase1",
                        help="Tag stored in backtest_runs.run_label")
    args = parser.parse_args()
    agg = replay(args.label)
    _print_summary(agg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
