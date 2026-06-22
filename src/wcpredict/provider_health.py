from enum import Enum


class FailureKind(str, Enum):
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    BLOCKED = "blocked_by_provider"
    UNSUPPORTED = "unsupported_event_or_market"
    MATCHING = "event_matching"
    SCHEMA = "provider_schema"
    NETWORK = "network"


CREDENTIALS = {
    "api_sports_football": "API_SPORTS_KEY",
    "apifootball": "APIFOOTBALL_API_KEY",
    "football_data": "FOOTBALL_DATA_API_KEY",
    "oddspapi_winamax": "ODDSPAPI_API_KEY",
    "the_odds_api": "THE_ODDS_API_KEY",
    "thesportsdb": "THESPORTSDB_API_KEY",
    "sportmonks": "SPORTMONKS_API_TOKEN",
}


def classify_provider_failure(detail: str) -> FailureKind:
    text = detail.casefold()
    if "401" in text or "unauthorized" in text or "authentication" in text:
        return FailureKind.AUTHENTICATION
    if "rate limit" in text or "quota" in text or "429" in text:
        return FailureKind.QUOTA
    if "403" in text or "forbidden" in text:
        return FailureKind.BLOCKED
    if "422" in text or "unsupported" in text:
        return FailureKind.UNSUPPORTED
    if "matching" in text or "no compatible event" in text:
        return FailureKind.MATCHING
    if "schema" in text or "must be a list" in text:
        return FailureKind.SCHEMA
    return FailureKind.NETWORK


def credential_matrix(environment: dict[str, str]) -> dict[str, dict]:
    return {
        provider: {
            "credential_name": name,
            "configured": bool(environment.get(name, "").strip()),
        }
        for provider, name in CREDENTIALS.items()
    }
