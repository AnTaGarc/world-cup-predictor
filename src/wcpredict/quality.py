from enum import Enum

from wcpredict.models import MarketFamily


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_ESTIMABLE = "not_estimable"


PLAYER_MARKETS = {
    MarketFamily.PLAYER_GOAL,
    MarketFamily.PLAYER_ASSIST,
    MarketFamily.PLAYER_SHOTS,
    MarketFamily.PLAYER_SHOTS_ON_TARGET,
    MarketFamily.PLAYER_CARDS,
    MarketFamily.PLAYER_PASSES,
}


def assess_market_confidence(
    market_family: MarketFamily,
    sample_size: int,
    missing_fields: list[str],
    lineup_dependent: bool,
    manually_estimated: bool,
) -> Confidence:
    required_missing = {"player", "team", "expected_minutes", "probability_inputs"}
    if required_missing.intersection(missing_fields) and sample_size == 0:
        return Confidence.NOT_ESTIMABLE
    if market_family in PLAYER_MARKETS and ("expected_minutes" in missing_fields or lineup_dependent):
        return Confidence.LOW
    if sample_size >= 12 and not missing_fields and not manually_estimated:
        return Confidence.HIGH
    if sample_size >= 5 and len(missing_fields) <= 1:
        return Confidence.MEDIUM
    return Confidence.LOW


def calibrate_confidence(
    base: Confidence,
    sample_size: int,
    average_brier: float,
    minimum_reliable_sample: int = 20,
) -> Confidence:
    if base == Confidence.NOT_ESTIMABLE:
        return base
    levels = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    index = levels.index(base)
    if sample_size < minimum_reliable_sample:
        return levels[min(index, 1)]
    if average_brier >= 0.30:
        return levels[max(0, index - 1)]
    if average_brier <= 0.18:
        return levels[min(2, index + 1)]
    return base
