"""Convert a measured bias report into safe model corrections.

The corrections use Bayesian shrinkage so the strength applied is proportional
to the confidence in the measurement:

    applied = measured_bias × n / (n + prior_strength)

With 36 samples and a prior_strength of 30, only ~55% of the measured bias is
applied. As more matches are played the weight grows toward 1.0.

Three families of corrections are produced; the user opts in via a UI toggle:
  1. xg_shift: subtracted from each team's expected goals before the Poisson
     score matrix is built. Fixes systematic over/under-estimation of goals.
  2. outcome_logit_shifts: added in log-prob space to the unified 1X2 before
     renormalisation. Fixes systematic over/under-confidence in home/draw/away.
  3. volume_shifts (currently empty): reserved for per-metric corner/card/etc.
     corrections once a per-metric bias signal is wired in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class ModelCorrections:
    xg_shift: float
    outcome_logit_shifts: dict
    volume_shifts: dict = field(default_factory=dict)
    applied_strength: dict = field(default_factory=dict)
    sample_size: int = 0


def _shrink(measured: float | None, n: int, prior_strength: float) -> tuple[float, float]:
    if measured is None or n <= 0:
        return 0.0, 0.0
    weight = n / (n + prior_strength)
    return measured * weight, weight


def derive_corrections(
    report,
    *,
    xg_prior_strength: float = 30.0,
    outcome_prior_strength: float = 30.0,
    min_xg_to_apply: float = 0.10,
    min_outcome_gap_to_apply: float = 0.03,
) -> ModelCorrections:
    """Bayesian-shrunk corrections from a BiasReport."""
    n = int(report.sample_size)
    applied: dict[str, float] = {}

    xg_shift, weight = _shrink(report.xg_bias_per_team, n, xg_prior_strength)
    applied["xg"] = weight
    if abs(xg_shift) < min_xg_to_apply:
        xg_shift = 0.0

    outcome_logit_shifts: dict[str, float] = {}
    pairs = (
        ("home", report.home_predicted_avg, report.home_actual_frequency),
        ("draw", report.draw_predicted_avg, report.draw_actual_frequency),
        ("away", report.away_predicted_avg, report.away_actual_frequency),
    )
    for outcome, predicted, actual in pairs:
        gap = predicted - actual
        if predicted > 0.01 and actual > 0.01 and abs(gap) >= min_outcome_gap_to_apply:
            # Shift in log-prob space: positive shift increases that outcome.
            measured_shift = math.log(actual / predicted)
            shrunk, weight = _shrink(measured_shift, n, outcome_prior_strength)
            outcome_logit_shifts[outcome] = shrunk
            applied[f"outcome_{outcome}"] = weight
        else:
            outcome_logit_shifts[outcome] = 0.0
            applied[f"outcome_{outcome}"] = 0.0

    return ModelCorrections(
        xg_shift=xg_shift,
        outcome_logit_shifts=outcome_logit_shifts,
        volume_shifts={},
        applied_strength=applied,
        sample_size=n,
    )


def apply_outcome_shifts(
    probabilities: dict, shifts: dict
) -> dict:
    """Apply log-space outcome shifts then renormalise.

    Returns a fresh dict with keys home/draw/away. If shifts are all zero, the
    input is returned (renormalised) unchanged.
    """
    adjusted = {}
    for key in ("home", "draw", "away"):
        prob = max(1e-9, float(probabilities.get(key, 0.0)))
        shift = float(shifts.get(key, 0.0))
        adjusted[key] = prob * math.exp(shift)
    total = sum(adjusted.values()) or 1.0
    return {key: value / total for key, value in adjusted.items()}


def describe_corrections(corrections: ModelCorrections) -> str:
    """Human-readable summary of what corrections are doing right now."""
    parts: list[str] = []
    if corrections.xg_shift:
        sign = "−" if corrections.xg_shift > 0 else "+"
        parts.append(f"xG por equipo {sign}{abs(corrections.xg_shift):.2f}")
    for outcome, shift in corrections.outcome_logit_shifts.items():
        if abs(shift) > 0.005:
            direction = "↓" if shift < 0 else "↑"
            label = {"home": "local", "draw": "empate", "away": "visitante"}.get(outcome, outcome)
            parts.append(f"{label} {direction}{abs(shift):.2f} (logit)")
    if not parts:
        return (
            f"Sin corrección aplicable: el sesgo medido está por debajo del umbral "
            f"o la muestra ({corrections.sample_size} partidos) es insuficiente."
        )
    return (
        "Corrección activa (shrinkage bayesiano, "
        f"basada en {corrections.sample_size} partidos auditados): "
        + ", ".join(parts) + "."
    )


def is_active(corrections: ModelCorrections | None) -> bool:
    if corrections is None:
        return False
    if abs(corrections.xg_shift) > 0:
        return True
    return any(abs(value) > 0.005 for value in corrections.outcome_logit_shifts.values())
