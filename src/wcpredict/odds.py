from dataclasses import dataclass
from datetime import datetime
import csv
import io

from wcpredict.market_math import (
    expected_value,
    expected_value_with_push,
    fair_odds,
    fair_odds_with_push,
    implied_probability,
)
from wcpredict.models import MarketFamily


@dataclass(frozen=True)
class ManualOdds:
    match_id: int
    market_family: MarketFamily
    market_name: str
    selection_name: str
    line: float | None
    decimal_odds: float
    bookmaker: str
    captured_at_utc: datetime


@dataclass(frozen=True)
class OddsComparison:
    market_family: MarketFamily
    market_name: str
    selection_name: str
    probability: float
    decimal_odds: float
    implied_probability: float
    fair_odds: float
    expected_value: float
    confidence: str


def _parse_optional_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def parse_odds_csv(csv_text: str, match_id: int, captured_at_utc: datetime) -> list[ManualOdds]:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = ["market_family", "market_name", "selection_name", "line", "decimal_odds", "bookmaker"]
    if reader.fieldnames != required:
        raise ValueError("odds CSV must have market_family,market_name,selection_name,line,decimal_odds,bookmaker")
    odds: list[ManualOdds] = []
    for row in reader:
        odds.append(
            ManualOdds(
                match_id=match_id,
                market_family=MarketFamily(row["market_family"]),
                market_name=row["market_name"],
                selection_name=row["selection_name"],
                line=_parse_optional_float(row["line"]),
                decimal_odds=float(row["decimal_odds"]),
                bookmaker=row["bookmaker"],
                captured_at_utc=captured_at_utc,
            )
        )
    return odds


def compare_odds_to_probability(
    probability: float,
    decimal_odds: float,
    market_family: MarketFamily,
    market_name: str,
    selection_name: str,
    confidence: str,
    push_probability: float = 0.0,
) -> OddsComparison:
    row_fair_odds = (
        fair_odds_with_push(probability, push_probability)
        if push_probability > 0
        else fair_odds(probability)
    )
    row_expected_value = (
        expected_value_with_push(probability, push_probability, decimal_odds)
        if push_probability > 0
        else expected_value(probability, decimal_odds)
    )
    return OddsComparison(
        market_family=market_family,
        market_name=market_name,
        selection_name=selection_name,
        probability=probability,
        decimal_odds=decimal_odds,
        implied_probability=implied_probability(decimal_odds),
        fair_odds=row_fair_odds,
        expected_value=row_expected_value,
        confidence=confidence,
    )
