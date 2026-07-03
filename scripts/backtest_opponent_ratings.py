"""A/B gate for opponent-adjusted ratings (spec 2026-07-03).

Reconstructs pre-kickoff 1X2 probabilities for every closed WC2026 match
through the same pipeline as backfill_live_residuals.py, once with legacy
ratings (iterations=1) and once with opponent-adjusted ratings
(iterations=3), and compares aggregate log-loss. Also prints the
Spain-Portugal 90' probabilities under both variants.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from wcpredict.advanced_form import build_xg_form_adjustment
from wcpredict.ratings import build_team_ratings
from wcpredict.repository import Repository
from wcpredict.services import predict_match_markets
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile

from backfill_live_residuals import (
    _closed_matches,
    _index_by_team,
    _preload_deep_rows,
    _preload_results,
    _results_before,
    _sample_slice,
    _team_slice,
)


def _predict(repo, m, all_results, deep_by_team, sample, iterations):
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
    ratings = build_team_ratings(results, as_of=as_of.date(), iterations=iterations)
    preds = predict_match_markets(
        m["team_a"], m["team_b"], results, as_of.date(),
        advanced_form=advanced,
        outcome_probabilities=None,
        precomputed_ratings=ratings,
    )
    p_home = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_a"]), 0.0)
    p_draw = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == "Draw"), 0.0)
    p_away = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_b"]), 0.0)
    return {"home": p_home, "draw": p_draw, "away": p_away}


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()
    con = sqlite3.connect(repo.path)
    con.row_factory = sqlite3.Row
    closed = _closed_matches(con)
    print(f"Partidos cerrados: {len(closed)}", flush=True)
    all_results = _preload_results(repo)
    all_deep = _preload_deep_rows(repo)
    deep_by_team = _index_by_team(all_deep)
    sample = all_deep[::40]

    losses = {1: 0.0, 3: 0.0}
    hits = {1: 0, 3: 0}
    for index, m in enumerate(closed, 1):
        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"
        for iterations in (1, 3):
            probs = _predict(repo, m, all_results, deep_by_team, sample, iterations)
            losses[iterations] -= math.log(max(1e-9, probs[winner]))
            best = max(probs, key=probs.get)
            hits[iterations] += int(best == winner)
        if index % 20 == 0:
            print(f"  ...{index}/{len(closed)}", flush=True)

    n = len(closed)
    ll1, ll3 = losses[1] / n, losses[3] / n
    print(f"\nlegacy    (iter=1): log-loss {ll1:.5f} | aciertos {hits[1]}/{n}")
    print(f"ajustado  (iter=3): log-loss {ll3:.5f} | aciertos {hits[3]}/{n}")
    print(f"mejora relativa: {100.0 * (ll1 - ll3) / ll1:.3f}%")

    spain_portugal = next(
        (m for m in closed if {m["team_a"], m["team_b"]} == {"Spain", "Portugal"}), None
    )
    # Not closed yet: build a synthetic fixture for the diagnostic.
    fixture = spain_portugal or {
        "team_a": "Portugal", "team_b": "Spain",
        "kickoff_utc": "2026-07-05T17:00:00+00:00", "goals_a": 0, "goals_b": 0,
    }
    for iterations in (1, 3):
        probs = _predict(repo, fixture, all_results, deep_by_team, sample, iterations)
        print(
            f"Spain-Portugal iter={iterations}: "
            f"{fixture['team_a']} {probs['home']:.3f} / X {probs['draw']:.3f} / "
            f"{fixture['team_b']} {probs['away']:.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
