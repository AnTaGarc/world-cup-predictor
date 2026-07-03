from __future__ import annotations

from typing import Any


GH_TEAM_METRIC_MAP: dict[str, str] = {
    "possession_pct": "resumen_del_partido.posesion_de_balon_pct",
    "total_shots": "tiros.tiros_totales",
    "shots_on_target": "tiros.tiros_a_puerta",
    "corners": "resumen_del_partido.saques_de_esquina",
    "fouls": "resumen_del_partido.faltas",
    "offsides": "ataque.fueras_de_juego",
    "saves": "porteria.paradas",
}


def build_synthetic_penalty_attempts(
    player_rows: list[dict[str, Any]], before_kickoff_iso: str
) -> list[dict[str, Any]]:
    """Turn tournament penalty goals into synthetic scored attempts.

    Only successes are visible upstream (the dataset does not record missed
    in-match penalties), so callers must keep the regular-phase weight; the
    Bayesian prior absorbs the one-sided-sample bias.
    """
    cutoff_date = str(before_kickoff_iso)[:10]
    output: list[dict[str, Any]] = []
    for row in player_rows:
        goals = row.get("penalty_goals")
        verified = row.get("last_verified")
        if not goals or not verified:
            continue
        if str(verified)[:10] > cutoff_date:
            continue
        for index in range(int(goals)):
            output.append({
                "player_name": row.get("player_name"),
                "team_name": row.get("team_name"),
                "phase": "regular",
                "outcome": "scored",
                "attempted_on": str(verified)[:10],
                "goalkeeper_name": None,
                "source_provider": "github_wc2026",
                "source_url": None,
                "source_row_key": f"ghps:{row.get('external_player_id')}:{index}",
            })
    return output


def goalkeeper_tournament_save_rate(row: dict[str, Any]) -> float | None:
    saves = row.get("saves")
    conceded = row.get("goals_conceded")
    if saves is None or conceded is None:
        return None
    total = int(saves) + int(conceded)
    if total < 3:
        return None
    return int(saves) / total
