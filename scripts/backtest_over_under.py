"""Backtest the over/under predictions specifically — this is where
Negative Binomial differs from Poisson (same mean, fatter tail).

For each finished match, for each line in MARKET_CATALOG:
  * Compute P(over) under Poisson and under NB
  * Observe whether the actual count was > line
  * Compute Brier score (squared error) and log loss

A model is better-calibrated if it assigns higher probability to outcomes
that actually happen.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import (
    MARKET_CATALOG, _poisson_over, _neg_binomial_over,
)
from wcpredict.ratings import build_team_ratings


ACTUAL_COL = {
    "resumen_del_partido.saques_de_esquina": "corners",
    "resumen_del_partido.tarjetas_amarillas": "yellow_cards",
    "resumen_del_partido.tiros_totales": "shots",
    "tiros.tiros_a_puerta": "shots_on_target",
}


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    matches = repo.list_matches()
    completed = [m for m in matches if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    print(f"Partidos: {len(completed)}")

    # market_id → {line → [(poisson_p, nb_p, actual_over_bool), ...]}
    per_line: dict[str, dict[float, list[tuple[float, float, int]]]] = {
        m: {line: [] for line in spec["lines"]}
        for m, spec in MARKET_CATALOG.items()
    }

    for match in completed:
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        obs = repo.list_deep_team_metric_observations_before(k)
        ratings = build_team_ratings([], as_of=k.date())  # not needed but signature
        pa = build_team_profile(a, obs, k)
        pb = build_team_profile(b, obs, k)
        actual_stats = {row["team_id"]: row for row in repo.list_team_match_stats(match.id)}
        ta_id = repo.upsert_team(a)
        tb_id = repo.upsert_team(b)
        for market_id, spec in MARKET_CATALOG.items():
            metric = spec["metric"]
            col = ACTUAL_COL.get(metric)
            alpha = float(spec.get("dispersion_prior", 0.0))
            if col is None:
                continue
            tmean = next((est.tournament_mean for est in pa.metrics.values()
                          if est.metric == metric), spec["tournament_default"])
            for profile, other, team_id in ((pa, pb, ta_id), (pb, pa, tb_id)):
                own = profile.get(metric)
                opp = other.conceded(metric)
                if opp is None:
                    opp = other.get(metric)
                if not (own and opp and tmean):
                    continue
                lambd = 0.45 * own + 0.30 * opp + 0.25 * tmean
                actual_row = actual_stats.get(team_id, {})
                actual_val = actual_row.get(col)
                if actual_val is None:
                    continue
                for line in spec["lines"]:
                    p_poi = _poisson_over(lambd, line)
                    p_nb = _neg_binomial_over(lambd, alpha, line)
                    actual_over = 1 if actual_val > line else 0
                    per_line[market_id][line].append((p_poi, p_nb, actual_over))

    print(f"\n{'Mercado':22} {'línea':>6} {'n':>4} {'Brier Poi':>10} {'Brier NB':>10} {'mejora':>8}")
    print("-" * 70)
    total_n = 0
    total_brier_poi = 0.0
    total_brier_nb = 0.0
    for market_id, lines in per_line.items():
        for line, records in lines.items():
            if not records:
                continue
            n = len(records)
            brier_poi = sum((p - a) ** 2 for p, _, a in records) / n
            brier_nb = sum((p - a) ** 2 for _, p, a in records) / n
            impro = (brier_poi - brier_nb) / brier_poi * 100 if brier_poi else 0
            print(f"{market_id:22} {line:>6} {n:>4} {brier_poi:>10.4f} {brier_nb:>10.4f} {impro:>+7.2f}%")
            total_n += n
            total_brier_poi += brier_poi * n
            total_brier_nb += brier_nb * n
    if total_n:
        avg_poi = total_brier_poi / total_n
        avg_nb = total_brier_nb / total_n
        avg_impro = (avg_poi - avg_nb) / avg_poi * 100
        print(f"\nTOTAL: n={total_n} Brier Poisson={avg_poi:.4f} Brier NB={avg_nb:.4f} mejora={avg_impro:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
