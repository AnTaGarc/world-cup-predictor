"""Phase 5b: residuos cross-torneo para inicializar team_corrections.

Para cada partido historico (2022-2026, ambos equipos pertenecen al WC2026)
con deep stats y resultado conocido:
  1. Reconstruye xG ajustado por advanced_form + perfil deep historicas
  2. Calcula 1X2 predicho (matriz Dixon-Coles + perfiles).
  3. Persiste residual prob_predicted - outcome en backtest_runs con
     un run_label dedicado, para que compute_team_market_shifts lo
     consume como bootstrap.

Optimizaciones:
  * deep_rows se carga una sola vez (todo el historico) e indexado
    por kickoff_utc para extraer ranges en O(log n).
  * No se llama el ML deep aqui — esto es un baseline reaplicado al
    pasado, no una reevaluacion del pipeline completo.
  * Se procesa solo el mercado 1X2 (que es donde aplicamos shifts).
"""
from __future__ import annotations

import json
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
RUN_LABEL = "historical-pool-v1"
MODEL_VERSION = "hist-pool-profiles-v1"


def _wc2026_teams(con) -> set[str]:
    out = set()
    for r in con.execute(
        "SELECT DISTINCT ta.name FROM matches m "
        "JOIN teams ta ON ta.id IN (m.team_a_id, m.team_b_id) "
        "WHERE m.competition=?",
        (COMPETITION,),
    ):
        out.add(str(r["name"]))
    return out


def _historical_matches(con, wc_teams: set[str], since: str, until: str):
    rows = []
    for r in con.execute(
        "SELECT id, played_at_utc, team_a_name, team_b_name, goals_a, goals_b, tournament "
        "FROM historical_matches "
        "WHERE goals_a IS NOT NULL AND goals_b IS NOT NULL "
        "  AND played_at_utc > ? AND played_at_utc < ? "
        "ORDER BY played_at_utc",
        (since, until),
    ):
        a, b = str(r["team_a_name"]), str(r["team_b_name"])
        if a in wc_teams and b in wc_teams:
            rows.append(dict(r))
    return rows


def _index_deep_rows(repo: Repository, until_iso: str):
    """Returns a list ordered by kickoff_utc for binary-search slicing."""
    until = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    rows = repo.list_deep_team_metric_observations_before(until)
    rows.sort(key=lambda r: str(r.get("kickoff_utc") or ""))
    return rows


def _rows_before(deep_rows, iso_cutoff: str):
    """Binary-search slicing on sorted-by-kickoff rows."""
    import bisect
    keys = [str(r.get("kickoff_utc") or "") for r in deep_rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return deep_rows[:idx]


def _index_deep_rows_by_team(deep_rows):
    """{canonical_team: [rows...]} sorted by kickoff_utc, for O(per-team)
    slicing instead of scanning all 106k rows for every replay match."""
    from wcpredict.names import canonical_team_name
    by_team: dict[str, list[dict]] = {}
    for r in deep_rows:
        team = canonical_team_name(str(r.get("team_name") or ""))
        if team:
            by_team.setdefault(team, []).append(r)
    return by_team


def _team_rows_before(team_index, team_name, iso_cutoff):
    """Slice the per-team list by kickoff_utc."""
    from wcpredict.names import canonical_team_name
    import bisect
    rows = team_index.get(canonical_team_name(team_name), ())
    if not rows:
        return []
    keys = [str(r.get("kickoff_utc") or "") for r in rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return rows[:idx]


def _preload_historical_results(repo: Repository):
    """Cache the entire historical_matches set once, sorted, for in-memory
    slicing. Otherwise list_historical_results_before runs a 50k-row SELECT
    per replay match (442 of them) → process becomes I/O bound."""
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    rows = repo.list_historical_results_before(far_future)
    rows.sort(key=lambda r: r.played_on)
    return rows


def _results_before(sorted_results, as_of):
    import bisect
    keys = [r.played_on for r in sorted_results]
    idx = bisect.bisect_left(keys, as_of.date())
    return sorted_results[:idx]


def _preload_deep_xg_rows(repo: Repository):
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    rows = repo.list_deep_xg_rows_before(far_future)
    rows.sort(key=lambda r: str(r.get("kickoff_utc") or ""))
    return rows


def _xg_rows_before(sorted_rows, iso_cutoff: str):
    import bisect
    keys = [str(r.get("kickoff_utc") or "") for r in sorted_rows]
    idx = bisect.bisect_left(keys, iso_cutoff)
    return sorted_rows[:idx]


def _persist_row(con, match_pseudo_id: int, market: str, selection: str,
                 prob: float, outcome: int, recorded_at: str):
    """match_pseudo_id is the historical_matches.id stored as negative so it
    cannot collide with real matches.id ↔ backtest_runs.match_id."""
    brier = (prob - outcome) ** 2
    import math
    p_clip = max(1e-6, min(1.0 - 1e-6, prob if outcome == 1 else 1.0 - prob))
    log_loss = -math.log(p_clip)
    con.execute(
        "INSERT INTO backtest_runs(run_label, model_version, match_id, market, selection, "
        "prob_predicted, outcome_observed, brier, log_loss, extra_json, recorded_at_utc) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_label, model_version, match_id, market, selection) DO UPDATE SET "
        "prob_predicted=excluded.prob_predicted, outcome_observed=excluded.outcome_observed, "
        "brier=excluded.brier, log_loss=excluded.log_loss",
        (RUN_LABEL, MODEL_VERSION, -match_pseudo_id, market, selection,
         float(prob), int(outcome), float(brier), float(log_loss),
         json.dumps({"source": "historical_matches"}), recorded_at),
    )


def main() -> int:
    repo = Repository(DB)
    repo.initialize()
    con = sqlite3.connect(repo.path)
    con.row_factory = sqlite3.Row

    wc_teams = _wc2026_teams(con)
    print(f"Equipos WC2026: {len(wc_teams)}")

    matches = _historical_matches(con, wc_teams, "2022-06-01", "2026-06-11")
    print(f"Historicos con ambos equipos WC2026: {len(matches)}")

    # Carga deep_rows hasta cierre del rango (2026-06-11).
    deep_all = _index_deep_rows(repo, "2026-06-11T00:00:00+00:00")
    print(f"Deep observations cargadas: {len(deep_all)}", flush=True)

    # Pre-load historical_matches y deep_xg_rows en memoria (slice por fecha).
    all_results = _preload_historical_results(repo)
    print(f"Historical results en memoria: {len(all_results)}", flush=True)
    all_xg_rows = _preload_deep_xg_rows(repo)
    print(f"Deep xG rows en memoria: {len(all_xg_rows)}", flush=True)

    # Per-team index: cada partido del pool solo necesita las filas de SUS
    # 2 equipos para construir el perfil. Esto baja build_team_profile de
    # ~3s (escaneando 106k filas) a ~50ms.
    deep_by_team = _index_deep_rows_by_team(deep_all)
    print(f"Deep index por equipo: {len(deep_by_team)} equipos", flush=True)

    # Muestreo global para que tournament_means siga teniendo cobertura.
    sample_for_tournament = deep_all[::40]  # ~2.5k rows
    print(f"Tournament-mean sample: {len(sample_for_tournament)} rows", flush=True)

    processed = 0
    skipped = 0
    recorded_at = datetime.now(timezone.utc).isoformat()
    for m in matches:
        kickoff_iso = str(m["played_at_utc"])
        if "T" not in kickoff_iso:
            kickoff_iso = kickoff_iso + "T20:00:00"
        kickoff = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        as_of_iso = as_of.isoformat()

        # Historical results before this date for ratings (in-memory slice).
        results = _results_before(all_results, as_of)
        if not results:
            skipped += 1
            continue

        # Only the two teams' rows + a tournament-mean sample. Massive speedup.
        team_a_slice = _team_rows_before(deep_by_team, m["team_a_name"], as_of_iso)
        team_b_slice = _team_rows_before(deep_by_team, m["team_b_name"], as_of_iso)
        sample_slice = _rows_before(sample_for_tournament, as_of_iso)
        deep_rows_slice = team_a_slice + team_b_slice + sample_slice
        profile_a = build_team_profile(m["team_a_name"], deep_rows_slice, as_of)
        profile_b = build_team_profile(m["team_b_name"], deep_rows_slice, as_of)

        # If neither team has sample weight, the profile is just tournament
        # mean and adds no signal. Skip rather than dilute residuals.
        if profile_a.sample_weight + profile_b.sample_weight < 2.0:
            skipped += 1
            continue

        deep_xg_rows = repo.list_deep_xg_rows_before(as_of)
        advanced = build_xg_form_adjustment(
            m["team_a_name"], m["team_b_name"], deep_xg_rows, as_of,
        )
        if advanced is not None:
            factor_a, factor_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
            from dataclasses import replace as _replace
            advanced = _replace(
                advanced,
                factor_a=advanced.factor_a * factor_a,
                factor_b=advanced.factor_b * factor_b,
            )

        preds = predict_match_markets(
            m["team_a_name"], m["team_b_name"], results, as_of.date(),
            advanced_form=advanced,
            outcome_probabilities=None,  # score-only para velocidad y consistencia
        )

        ga, gb = int(m["goals_a"]), int(m["goals_b"])
        winner = "home" if ga > gb else "away" if gb > ga else "draw"
        p_home = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_a_name"]), 0.0)
        p_draw = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == "Draw"), 0.0)
        p_away = next((p.probability for p in preds if p.market_name == "1X2" and p.selection_name == m["team_b_name"]), 0.0)

        for sel, prob, outcome in (
            (m["team_a_name"], p_home, 1 if winner == "home" else 0),
            ("Draw", p_draw, 1 if winner == "draw" else 0),
            (m["team_b_name"], p_away, 1 if winner == "away" else 0),
        ):
            _persist_row(con, int(m["id"]), "1X2", sel, prob, outcome, recorded_at)

        processed += 1
        if processed % 25 == 0:
            con.commit()
            print(f"  {processed}/{len(matches)} processed (skipped={skipped})", flush=True)

    con.commit()
    con.close()
    print(f"\nDone. Processed={processed}, skipped={skipped}")
    print(f"backtest_runs label='{RUN_LABEL}' insertados con prob/outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
