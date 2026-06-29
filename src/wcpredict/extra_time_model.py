from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math


EXTRA_TIME_FRACTION = 0.30
RECENCY_HALF_LIFE_DAYS = 365.0
PRIOR_EQUIVALENT_MATCHES = 8.0
MIN_FACTOR = 0.75
MAX_FACTOR = 1.25
MIN_OBSERVATION_RATIO = 0.50
MAX_OBSERVATION_RATIO = 1.50


@dataclass(frozen=True)
class ExtraTimeAdjustment:
    adjusted_xg: tuple[float, float]
    factor_a: float
    factor_b: float
    sample_a: int
    sample_b: int
    explanation: str


def _name(value: object) -> str:
    return str(value or "").strip().casefold()


def _row_ratio(row: dict) -> float | None:
    regulation_xg = row.get("regulation_xg")
    if regulation_xg is None or float(regulation_xg) <= 0:
        return None
    extra_time_xg = row.get("extra_time_xg")
    goals = row.get("extra_time_goals")
    if extra_time_xg is not None:
        signal = 0.70 * float(extra_time_xg) + 0.30 * float(goals or 0.0)
    elif goals is not None:
        signal = float(goals)
    else:
        return None
    raw = signal / (float(regulation_xg) * EXTRA_TIME_FRACTION)
    return max(MIN_OBSERVATION_RATIO, min(MAX_OBSERVATION_RATIO, raw))


def _recency_weight(row: dict, as_of: datetime) -> float:
    try:
        played = datetime.fromisoformat(str(row.get("kickoff_utc") or "").replace("Z", "+00:00"))
        days = max(0, (as_of - played).days)
    except (TypeError, ValueError):
        days = 0
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def _shrunk_ratio(rows: list[dict], as_of: datetime) -> tuple[float, int]:
    weighted_sum = 0.0
    weight_total = 0.0
    count = 0
    for row in rows:
        ratio = _row_ratio(row)
        if ratio is None:
            continue
        weight = _recency_weight(row, as_of)
        weighted_sum += ratio * weight
        weight_total += weight
        count += 1
    value = (PRIOR_EQUIVALENT_MATCHES + weighted_sum) / (
        PRIOR_EQUIVALENT_MATCHES + weight_total
    )
    return max(MIN_FACTOR, min(MAX_FACTOR, value)), count


def adjust_extra_time_xg(
    team_a: str,
    team_b: str,
    regulation_xg_a: float,
    regulation_xg_b: float,
    rows: list[dict],
    as_of: datetime,
) -> ExtraTimeAdjustment:
    attack_a, attack_count_a = _shrunk_ratio(
        [row for row in rows if _name(row.get("team_name")) == _name(team_a)], as_of
    )
    attack_b, attack_count_b = _shrunk_ratio(
        [row for row in rows if _name(row.get("team_name")) == _name(team_b)], as_of
    )
    defence_a, defence_count_a = _shrunk_ratio(
        [row for row in rows if _name(row.get("opponent_name")) == _name(team_a)], as_of
    )
    defence_b, defence_count_b = _shrunk_ratio(
        [row for row in rows if _name(row.get("opponent_name")) == _name(team_b)], as_of
    )
    factor_a = max(MIN_FACTOR, min(MAX_FACTOR, math.sqrt(attack_a * defence_b)))
    factor_b = max(MIN_FACTOR, min(MAX_FACTOR, math.sqrt(attack_b * defence_a)))
    sample_a = max(attack_count_a, defence_count_a)
    sample_b = max(attack_count_b, defence_count_b)
    adjusted = (
        float(regulation_xg_a) * EXTRA_TIME_FRACTION * factor_a,
        float(regulation_xg_b) * EXTRA_TIME_FRACTION * factor_b,
    )
    explanation = (
        f"Prórroga aislada: factores {factor_a:.3f}/{factor_b:.3f}; "
        f"muestras por selección {sample_a}/{sample_b}; prior equivalente a "
        f"{int(PRIOR_EQUIVALENT_MATCHES)} partidos."
    )
    return ExtraTimeAdjustment(
        adjusted_xg=adjusted,
        factor_a=factor_a,
        factor_b=factor_b,
        sample_a=sample_a,
        sample_b=sample_b,
        explanation=explanation,
    )
