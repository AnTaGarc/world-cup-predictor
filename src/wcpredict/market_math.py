def _validate_probability(probability: float) -> None:
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between 0 and 1")


def _validate_decimal_odds(decimal_odds: float) -> None:
    if decimal_odds <= 1.0:
        raise ValueError("decimal_odds must be greater than 1")


def expected_value(probability: float, decimal_odds: float) -> float:
    _validate_probability(probability)
    _validate_decimal_odds(decimal_odds)
    return probability * decimal_odds - 1.0


def implied_probability(decimal_odds: float) -> float:
    _validate_decimal_odds(decimal_odds)
    return 1.0 / decimal_odds


def fair_odds(probability: float) -> float:
    _validate_probability(probability)
    if probability == 0.0:
        raise ValueError("fair odds are undefined for zero probability")
    return 1.0 / probability
