from __future__ import annotations

import math


def count_variance(mean: float, dispersion: float = 0.0) -> float:
    if mean < 0 or dispersion < 0:
        raise ValueError("mean and dispersion must be non-negative")
    return mean + dispersion * mean * mean


def _poisson_pmf(value: int, mean: float) -> float:
    if mean == 0:
        return 1.0 if value == 0 else 0.0
    return math.exp(-mean + value * math.log(mean) - math.lgamma(value + 1))


def _negative_binomial_pmf(value: int, mean: float, dispersion: float) -> float:
    if dispersion <= 1e-12:
        return _poisson_pmf(value, mean)
    size = 1.0 / dispersion
    success = size / (size + mean)
    log_probability = (
        math.lgamma(value + size)
        - math.lgamma(size)
        - math.lgamma(value + 1)
        + size * math.log(success)
        + value * math.log1p(-success)
    )
    return math.exp(log_probability)


def over_probability(
    mean: float,
    line: float,
    *,
    distribution: str = "poisson",
    dispersion: float = 0.0,
) -> float:
    if mean < 0:
        raise ValueError("mean must be non-negative")
    threshold = math.floor(line)
    if distribution not in {"poisson", "negative_binomial"}:
        raise ValueError(f"unsupported count distribution: {distribution}")
    pmf = _poisson_pmf if distribution == "poisson" else lambda value, rate: _negative_binomial_pmf(value, rate, dispersion)
    cumulative = sum(pmf(value, mean) for value in range(threshold + 1))
    return max(0.0, min(1.0, 1.0 - cumulative))
