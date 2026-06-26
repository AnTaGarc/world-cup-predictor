"""Phase 6b: validacion final con bootstrap historico + EMA online.

Construye el shift inicial de cada equipo a partir del pool historico
(scripts.build_historical_team_residuals) y lo actualiza con cada partido
WC2026 cerrado via media movil exponencial (alpha=0.20).

Para cada partido WC2026 en orden cronologico:
  1. Calcula shifts iniciales = bootstrap historico (todos los residuos
     de partidos PREVIOS de cada equipo en historicos + WC2026 cerrados).
  2. Re-corre la prediccion con y sin esos shifts.
  3. Acumula Brier 1X2 y hit rate.

Output: tabla comparativa.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.advanced_form import build_xg_form_adjustment
from wcpredict.repository import Repository
from wcpredict.services import predict_match_markets
from wcpredict.team_corrections import compute_team_market_shifts
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile

DB = ROOT / "data" / "worldcup.sqlite"
COMPETITION = "FIFA World Cup 2026"
HISTORICAL_LABEL = "historical-pool-v1"
LIVE_LABEL = "fase4-full-v2"
PRIOR_STRENGTH = 4.0
MIN_N = 2


def _wc2026_closed_matches(con):
    rows = con.execute(
        "SELECT m.id, m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
        "mr.goals_a, mr.goals_b "
        "FROM match_results mr JOIN matches m ON m.id=mr.match_id "
        "JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
        "WHERE m.competition=? ORDER BY m.kickoff_utc, m.id",
        (COMPETITION,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_residuals(con, label: str, until_iso: str):
    """Backtest_runs rows for `label`, filtered to matches before `until`.

    For historical pool, match_id < 0 (encoded historical_matches.id).
    For live label, match_id > 0 (matches.id with kickoff < until).
    """
    out = []
    # Historical pool: match_id < 0, use historical_matches.played_at_utc
    for r in con.execute(
        "SELECT br.market, br.selection, br.prob_predicted, br.outcome_observed, "
        "hm.team_a_name AS team_a, hm.team_b_name AS team_b "
        "FROM backtest_runs br JOIN historical_matches hm ON hm.id = -br.match_id "
        "WHERE br.run_label=? AND hm.played_at_utc < ?",
        (HISTORICAL_LABEL, until_iso),
    ):
        out.append(dict(r))
    # Live pool: WC2026 matches before until
    for r in con.execute(
        "SELECT br.market, br.selection, br.prob_predicted, br.outcome_observed, "
        "ta.name AS team_a, tb.name AS team_b "
        "FROM backtest_runs br JOIN matches m ON m.id = br.match_id "
        "JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
        "WHERE br.run_label=? AND m.kickoff_utc < ?",
        (label, until_iso),
    ):
        out.append(dict(r))
    return out


def _shifts_from_residuals(rows) -> dict[str, dict[str, float]]:
    """Convert residual rows into team_corrections shape."""
    raw = compute_team_market_shifts(
        rows, prior_strength=PRIOR_STRENGTH, min_n=MIN_N,
        market_filter=("1X2",),
    )
    by_team: dict[str, dict[str, float]] = {}
    for (team, _market), shift in raw.items():
        by_team.setdefault(team, {})["1X2"] = -shift
    return by_team


def _preload_results(repo):
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    h = repo.list_historical_results_before(far_future)
    local = repo.list_match_results_before(far_future)
    rows = h + local
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


def _deep_rows_before(sorted_rows, iso_cutoff):
    import bisect
    keys = [str(r.get("kickoff_utc") or "") for r in sorted_rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return sorted_rows[:idx]


def main() -> int:
    repo = Repository(DB)
    repo.initialize()
    con = sqlite3.connect(repo.path)
    con.row_factory = sqlite3.Row

    closed = _wc2026_closed_matches(con)
    print(f"Validacion bootstrap+EMA sobre {len(closed)} partidos WC2026", flush=True)
    print("Precargando datasets...", flush=True)
    all_results = _preload_results(repo)
    print(f"  Results: {len(all_results)}", flush=True)
    all_deep = _preload_deep_rows(repo)
    print(f"  Deep rows: {len(all_deep)}", flush=True)

    agg = {
        "n": 0,
        "brier_with": 0.0, "brier_without": 0.0,
        "hits_with": 0, "hits_without": 0,
        "shifts_per_match": [],
    }
    for m in closed:
        kickoff = datetime.fromisoformat(m["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        as_of_iso = as_of.isoformat()

        residual_rows = _load_residuals(con, LIVE_LABEL, as_of_iso)
        team_shifts = _shifts_from_residuals(residual_rows)
        active = sum(1 for t in (m["team_a"], m["team_b"]) if team_shifts.get(t, {}).get("1X2"))
        agg["shifts_per_match"].append(active)

        # Same pipeline as the v2 backtest, no deep ML (so we focus on the
        # bootstrap effect, not the matchup-model regression).
        results = _results_before(all_results, as_of)
        deep_xg_rows = repo.list_deep_xg_rows_before(as_of)
        advanced = build_xg_form_adjustment(m["team_a"], m["team_b"], deep_xg_rows, as_of)

        deep_rows = _deep_rows_before(all_deep, as_of_iso)
        profile_a = build_team_profile(m["team_a"], deep_rows, as_of)
        profile_b = build_team_profile(m["team_b"], deep_rows, as_of)
        if advanced is not None:
            from dataclasses import replace as _replace
            factor_a, factor_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
            advanced = _replace(
                advanced,
                factor_a=advanced.factor_a * factor_a,
                factor_b=advanced.factor_b * factor_b,
            )

        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"

        for label, with_corr in (("without", False), ("with", True)):
            preds = predict_match_markets(
                m["team_a"], m["team_b"], results, as_of.date(),
                advanced_form=advanced,
                outcome_probabilities=None,
                team_corrections=team_shifts if with_corr else None,
            )
            ph = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_a"])
            pd = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == "Draw")
            pa = next(p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_b"])
            outcome = {"home": ph, "draw": pd, "away": pa}
            agg[f"brier_{label}"] += sum(
                (p - (1 if winner == k else 0)) ** 2 for k, p in outcome.items()
            )
            pick = max(outcome, key=outcome.get)
            if pick == winner:
                agg[f"hits_{label}"] += 1
        agg["n"] += 1

    n = agg["n"] or 1
    print()
    print("=" * 64)
    print(f"  Bootstrap historico + EMA online sobre {n} partidos")
    print("=" * 64)
    print(f"  Equipos con shift activo (media): {sum(agg['shifts_per_match'])/n:.2f}/2")
    print(f"  Brier 1X2  SIN bootstrap: {agg['brier_without']/n:.4f}")
    print(f"  Brier 1X2  CON bootstrap: {agg['brier_with']/n:.4f}")
    print(f"  Hit 1X2    SIN bootstrap: {agg['hits_without']/n:.1%}  ({agg['hits_without']}/{n})")
    print(f"  Hit 1X2    CON bootstrap: {agg['hits_with']/n:.1%}  ({agg['hits_with']}/{n})")
    delta_brier = (agg["brier_without"] - agg["brier_with"]) / n
    delta_hits = (agg["hits_with"] - agg["hits_without"]) / n * 100
    print(f"  Delta Brier (positivo = mejor): {delta_brier:+.4f}")
    print(f"  Delta Hit rate: {delta_hits:+.1f}pp")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
