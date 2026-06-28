from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wcpredict.market_catalog import default_market_rows, normalize_market_rows
from wcpredict.names import same_team
from wcpredict.odds import OddsComparison, compare_odds_to_probability
from wcpredict.player_markets import is_goalkeeper
from wcpredict.services import MarketPrediction
from wcpredict.ui.translations import (
    canonical_market,
    canonical_market_family,
    canonical_selection,
    localize_market,
    localize_market_family,
    localize_selection,
)


@dataclass(frozen=True)
class OddsEvaluation:
    entered: tuple[dict, ...]
    comparisons: tuple[OddsComparison, ...]


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


def localized_default_odds_rows(team_a: str, team_b: str) -> list[dict]:
    rows = [dict(row) for row in default_market_rows(team_a, team_b)]
    for row in rows:
        row["market_family"] = localize_market_family(row["market_family"])
        row["market_name"] = localize_market(row["market_name"])
        row["selection_name"] = localize_selection(row["selection_name"])
    return rows


def evaluate_odds_rows(
    predictions: list[MarketPrediction],
    edited_rows: list[dict],
) -> OddsEvaluation:
    canonical_rows = []
    for source in edited_rows:
        row = dict(source)
        row["market_family"] = canonical_market_family(
            str(row.get("market_family") or "")
        )
        row["market_name"] = canonical_market(str(row.get("market_name") or ""))
        row["selection_name"] = canonical_selection(
            str(row.get("selection_name") or "")
        )
        canonical_rows.append(row)

    entered = normalize_market_rows(canonical_rows)
    index = {
        (prediction.market_name, prediction.selection_name): prediction
        for prediction in predictions
    }
    comparisons = []
    for row in entered:
        model = index.get((row["market_name"], row["selection_name"]))
        if model is None:
            continue
        push_probability = 0.0
        model_probability = model.probability
        if model.market_name == "Draw No Bet":
            draw_model = index.get(("1X2", "Draw"))
            push_probability = draw_model.probability if draw_model else 0.0
            model_probability *= max(0.0, 1.0 - push_probability)
        comparisons.append(
            compare_odds_to_probability(
                model_probability,
                row["decimal_odds"],
                model.market_family,
                model.market_name,
                model.selection_name,
                model.confidence.value,
                push_probability=push_probability,
            )
        )
    return OddsEvaluation(tuple(entered), tuple(comparisons))


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
