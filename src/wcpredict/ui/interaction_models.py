from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wcpredict.names import same_team
from wcpredict.player_markets import is_goalkeeper


@dataclass(frozen=True)
class PlayerTeamContext:
    players: tuple[dict, ...]
    field_players: tuple[dict, ...]
    goalkeepers: tuple[dict, ...]
    roster_rows: tuple[dict, ...]
    opponent_sot_per90: float | None
    goalkeeper_baseline: Any


@dataclass(frozen=True)
class PlayerMatchContext:
    lineups: tuple[dict, ...]
    by_team: dict[str, PlayerTeamContext]


def _per90(value, minutes: int) -> float | None:
    return round(90.0 * float(value or 0) / minutes, 2) if minutes else None


def _roster_row(row: dict) -> dict:
    minutes = int(row.get("minutes") or 0)
    games = max(1, int(row.get("games") or 0))
    starts = int(row.get("starts") or 0)
    result = {
        "Jugador": row.get("player_name"),
        "Posición": row.get("position") or "—",
        "Min": minutes,
        "Partidos": games,
        "Titularidad": f"{min(1.0, starts / games):.0%}",
    }
    if is_goalkeeper(row):
        save_pct = row.get("save_percentage")
        result.update(
            {
                "Save %": round(float(save_pct), 1)
                if save_pct is not None
                else None,
                "Paradas": int(row.get("saves") or 0),
                "GC": int(row.get("goals_conceded") or 0),
                "Intercepciones": row.get("interceptions") or 0,
                "Pases": int(row.get("passes") or 0),
                "Amarillas": int(row.get("yellow_cards") or 0),
                "Rojas": int(row.get("red_cards") or 0),
            }
        )
    else:
        result.update(
            {
                "Goles": int(row.get("goals") or 0),
                "Asist.": int(row.get("assists") or 0),
                "Tiros": int(row.get("shots") or 0),
                "SOT": int(row.get("shots_on_target") or 0),
                "Amarillas": int(row.get("yellow_cards") or 0),
                "Rojas": int(row.get("red_cards") or 0),
                "Pases": int(row.get("passes") or 0),
                "G/90": _per90(row.get("goals"), minutes),
                "A/90": _per90(row.get("assists"), minutes),
                "Tiros/90": _per90(row.get("shots"), minutes),
                "SOT/90": _per90(row.get("shots_on_target"), minutes),
            }
        )
    return result


def prepare_player_match_context(
    team_a: str,
    team_b: str,
    current_players: list[dict],
    lineups: list[dict],
    team_volume_predictions: dict,
    goalkeeper_baselines: dict,
) -> PlayerMatchContext:
    sot = team_volume_predictions.get("shots_on_target", {})
    opponents = {team_a: team_b, team_b: team_a}
    by_team = {}
    for team_name in (team_a, team_b):
        players = tuple(
            sorted(
                (
                    row
                    for row in current_players
                    if same_team(str(row.get("team_name") or ""), team_name)
                    and int(row.get("minutes") or 0) > 0
                ),
                key=lambda row: (
                    -int(row.get("minutes") or 0),
                    str(row.get("player_name") or ""),
                ),
            )
        )
        field_players = tuple(row for row in players if not is_goalkeeper(row))
        goalkeepers = tuple(row for row in players if is_goalkeeper(row))
        by_team[team_name] = PlayerTeamContext(
            players=players,
            field_players=field_players,
            goalkeepers=goalkeepers,
            roster_rows=tuple(_roster_row(row) for row in players),
            opponent_sot_per90=sot.get(opponents[team_name]),
            goalkeeper_baseline=goalkeeper_baselines.get(team_name),
        )
    return PlayerMatchContext(tuple(lineups), by_team)
