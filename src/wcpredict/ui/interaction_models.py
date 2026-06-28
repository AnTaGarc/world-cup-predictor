from __future__ import annotations

from dataclasses import dataclass

from wcpredict.market_catalog import default_market_rows, normalize_market_rows
from wcpredict.odds import OddsComparison, compare_odds_to_probability
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
