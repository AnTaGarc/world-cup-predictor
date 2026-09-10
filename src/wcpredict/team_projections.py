"""Betting-neutral statistical projections for each team in a match."""
from __future__ import annotations

from dataclasses import dataclass

from wcpredict.team_profile import TeamProfile
from wcpredict.team_volume_markets import MARKET_CATALOG, derive_xg_factors_from_profile


@dataclass(frozen=True)
class TeamStatProjection:
    metric: str
    label: str
    team_name: str
    expected: float
    confidence: str
    sample_size: float


def _confidence_for(sample: float) -> str:
    if sample >= 6:
        return "high"
    if sample >= 2:
        return "medium"
    return "low"


def predict_team_statistics(
    profile_a: TeamProfile,
    profile_b: TeamProfile,
    *,
    own_weight: float = 0.45,
    opp_weight: float = 0.30,
    tournament_weight: float = 0.25,
    card_multiplier: float = 1.0,
) -> list[TeamStatProjection]:
    """Estimate each team's central value using the existing deep-stat blend."""
    projections: list[TeamStatProjection] = []
    for metric_id, spec in MARKET_CATALOG.items():
        metric = str(spec["metric"])
        default = float(spec["tournament_default"])
        for profile, opponent in ((profile_a, profile_b), (profile_b, profile_a)):
            own = profile.get(metric)
            conceded = opponent.conceded(metric)
            opponent_value = opponent.get(metric)
            estimate = profile.metrics.get(metric)
            tournament_mean = estimate.tournament_mean if estimate else default
            if tournament_mean <= 0:
                tournament_mean = default
            expected = (
                own_weight * (own if own is not None else tournament_mean)
                + opp_weight
                * (
                    conceded
                    if conceded is not None
                    else opponent_value if opponent_value is not None else tournament_mean
                )
                + tournament_weight * tournament_mean
            )
            if metric_id == "yellow_cards":
                expected *= card_multiplier
            sample = estimate.sample_size if estimate else 0.0
            projections.append(
                TeamStatProjection(
                    metric=metric_id,
                    label=str(spec["label"]),
                    team_name=profile.team_name,
                    expected=expected,
                    confidence=_confidence_for(sample),
                    sample_size=sample,
                )
            )
    return projections


__all__ = [
    "TeamStatProjection",
    "derive_xg_factors_from_profile",
    "predict_team_statistics",
]
