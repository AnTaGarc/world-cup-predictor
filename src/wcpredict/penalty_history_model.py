from __future__ import annotations

from dataclasses import dataclass
from datetime import date


GLOBAL_PENALTY_CONVERSION = 0.76
PRIOR_ATTEMPTS = 12.0
RECENT_BLEND = 0.35
MAX_SHOOTOUT_SHIFT = 0.14


@dataclass(frozen=True)
class PenaltyTeamProfile:
    team_name: str
    attempts: int
    scored: int
    conversion: float
    recent_attempts: int
    recent_conversion: float | None


@dataclass(frozen=True)
class PenaltyMatchContext:
    team_a: PenaltyTeamProfile
    team_b: PenaltyTeamProfile
    team_a_shootout_win_probability: float
    explanation: str


def build_penalty_team_profile(team_name: str, attempts: list[dict]) -> PenaltyTeamProfile:
    team_rows = [row for row in attempts if str(row.get("team_name") or "") == team_name]
    scored = sum(1 for row in team_rows if str(row.get("outcome") or "").lower() == "scored")
    total = len(team_rows)
    bayes = (scored + GLOBAL_PENALTY_CONVERSION * PRIOR_ATTEMPTS) / (total + PRIOR_ATTEMPTS)
    recent_rows = sorted(team_rows, key=_attempt_sort_key, reverse=True)[:6]
    recent_conversion = None
    if len(recent_rows) >= 3:
        recent_scored = sum(1 for row in recent_rows if str(row.get("outcome") or "").lower() == "scored")
        recent_conversion = recent_scored / len(recent_rows)
        conversion = (1.0 - RECENT_BLEND) * bayes + RECENT_BLEND * recent_conversion
    else:
        conversion = bayes
    return PenaltyTeamProfile(
        team_name=team_name,
        attempts=total,
        scored=scored,
        conversion=max(0.45, min(0.95, conversion)),
        recent_attempts=len(recent_rows),
        recent_conversion=recent_conversion,
    )


def build_penalty_match_context(
    team_a: str,
    team_b: str,
    attempts: list[dict],
) -> PenaltyMatchContext:
    profile_a = build_penalty_team_profile(team_a, attempts)
    profile_b = build_penalty_team_profile(team_b, attempts)
    sample_factor = min(1.0, (profile_a.attempts + profile_b.attempts) / 30.0)
    raw_shift = (profile_a.conversion - profile_b.conversion) * 0.9 * sample_factor
    shift = max(-MAX_SHOOTOUT_SHIFT, min(MAX_SHOOTOUT_SHIFT, raw_shift))
    p_a = max(0.36, min(0.64, 0.5 + shift))
    explanation = (
        f"Penaltis históricos guardados: {team_a} {profile_a.scored}/{profile_a.attempts} "
        f"({profile_a.conversion:.0%} ajustado), {team_b} {profile_b.scored}/{profile_b.attempts} "
        f"({profile_b.conversion:.0%} ajustado). "
    )
    if profile_a.attempts + profile_b.attempts == 0:
        explanation += "Sin penalty_history: tanda simétrica 50/50."
    else:
        explanation += f"Probabilidad de tanda para {team_a}: {p_a:.1%}."
    return PenaltyMatchContext(
        team_a=profile_a,
        team_b=profile_b,
        team_a_shootout_win_probability=p_a,
        explanation=explanation,
    )


def _attempt_sort_key(row: dict) -> tuple[date, str]:
    raw = str(row.get("attempted_on") or "")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        parsed = date.min
    return parsed, str(row.get("source_row_key") or "")
