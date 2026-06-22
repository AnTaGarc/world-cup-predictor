from dataclasses import dataclass


@dataclass(frozen=True)
class OddsRequestDecision:
    competition: str
    the_odds_sport_key: str | None
    max_credits: int
    exact_status: str


SPORT_KEYS = {"fifa world cup": "soccer_fifa_world_cup"}


def route_world_cup_odds(
    competition: str, max_credits: int
) -> OddsRequestDecision:
    if max_credits < 0:
        raise ValueError("max_credits cannot be negative")
    key = SPORT_KEYS.get(competition.casefold())
    return OddsRequestDecision(
        competition=competition,
        the_odds_sport_key=key,
        max_credits=max_credits,
        exact_status="enabled" if max_credits > 0 else "skipped_zero_budget",
    )
