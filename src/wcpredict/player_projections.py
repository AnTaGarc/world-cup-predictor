"""Betting-neutral projections derived from observed player statistics."""
from dataclasses import dataclass

from wcpredict.models import MarketFamily
from wcpredict.player_markets import (
    GOALKEEPER_MARKETS,
    PLAYER_MARKET_METRICS,
    DerivedPlayerAssumption,
    PlayerAssumption,
    derive_player_assumption,
    estimate_player_market_probability,
    is_goalkeeper,
)
from wcpredict.quality import Confidence


@dataclass(frozen=True)
class PlayerProjection:
    metric: MarketFamily
    expected_count: float
    threshold: float
    probability: float | None
    confidence: Confidence
    sample_size: int
    explanation: str


def estimate_player_projection(
    assumption: PlayerAssumption,
    metric: MarketFamily,
    threshold: float,
    sample_size: int,
) -> PlayerProjection:
    expected_count = (
        assumption.per90_rate
        * float(assumption.expected_minutes or 0)
        / 90.0
        * assumption.opponent_adjustment
    )
    estimate = estimate_player_market_probability(
        assumption, metric, threshold, sample_size
    )
    return PlayerProjection(
        metric=metric,
        expected_count=expected_count,
        threshold=threshold,
        probability=estimate.probability,
        confidence=estimate.confidence,
        sample_size=sample_size,
        explanation=estimate.explanation,
    )


__all__ = [
    "GOALKEEPER_MARKETS",
    "PLAYER_MARKET_METRICS",
    "DerivedPlayerAssumption",
    "PlayerAssumption",
    "PlayerProjection",
    "derive_player_assumption",
    "estimate_player_projection",
    "is_goalkeeper",
]
