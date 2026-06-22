from dataclasses import dataclass
import math

from wcpredict.names import same_team
from wcpredict.count_models import over_probability


@dataclass(frozen=True)
class VolumeEstimate:
    metric: str
    line: float
    expected_total: float | None
    over_probability: float | None
    low_probability: float | None
    high_probability: float | None
    confidence: str
    sample_size: int
    explanation: str
    model_family: str = "poisson"
    expected_team_a: float | None = None
    expected_team_b: float | None = None


def _poisson_over(rate: float, line: float) -> float:
    threshold = math.floor(line)
    cumulative = sum(
        math.exp(-rate) * rate**value / math.factorial(value)
        for value in range(threshold + 1)
    )
    return max(0.0, min(1.0, 1.0 - cumulative))


def _rate(rows: list[dict], team: str, metric: str) -> tuple[float, int] | None:
    candidates = [
        row
        for row in rows
        if same_team(str(row.get("subject_name") or ""), team)
        and str(row.get("metric")) == metric
        and row.get("value_number") is not None
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda row: int(row.get("sample_size") or 0))
    return float(best["value_number"]), int(best.get("sample_size") or 0)


def estimate_total_market(
    team_a: str,
    team_b: str,
    observations: list[dict],
    metric: str,
    line: float,
    dispersion: float | None = None,
) -> VolumeEstimate:
    a_for = _rate(observations, team_a, f"{metric}_for_avg")
    a_against = _rate(observations, team_a, f"{metric}_against_avg")
    b_for = _rate(observations, team_b, f"{metric}_for_avg")
    b_against = _rate(observations, team_b, f"{metric}_against_avg")
    if not all((a_for, a_against, b_for, b_against)):
        return VolumeEstimate(
            metric, line, None, None, None, None, "not_estimable", 0,
            "No hay tasas observadas completas para ambos equipos.",
        )
    expected_a = (a_for[0] + b_against[0]) / 2.0
    expected_b = (b_for[0] + a_against[0]) / 2.0
    expected_total = expected_a + expected_b
    sample_size = min(a_for[1], a_against[1], b_for[1], b_against[1])
    confidence = "high" if sample_size >= 12 else "medium" if sample_size >= 5 else "low"
    uncertainty = 0.12 if confidence == "high" else 0.20 if confidence == "medium" else 0.30
    distribution = "negative_binomial" if dispersion is not None and dispersion > 0 else "poisson"
    alpha = float(dispersion or 0.0)
    base = over_probability(expected_total, line, distribution=distribution, dispersion=alpha)
    low = over_probability(expected_total * (1.0 - uncertainty), line, distribution=distribution, dispersion=alpha)
    high = over_probability(expected_total * (1.0 + uncertainty), line, distribution=distribution, dispersion=alpha)
    return VolumeEstimate(
        metric=metric,
        line=line,
        expected_total=expected_total,
        over_probability=base,
        low_probability=low,
        high_probability=high,
        confidence=confidence,
        sample_size=sample_size,
        explanation=(
            f"Tasa total {expected_total:.2f}: promedio de produccion propia y concesion rival "
            f"con muestra minima de {sample_size} partidos. "
            + (f"Binomial negativa con sobredispersion {alpha:.3f}." if distribution == "negative_binomial" else "Fallback Poisson: dispersión no estimada.")
        ),
        model_family=distribution,
        expected_team_a=expected_a,
        expected_team_b=expected_b,
    )
