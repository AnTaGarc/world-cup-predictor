"""Per-match weighting for deep stats reused across competitions.

A single observation gets ``weight = w_recency × w_competition × w_opponent``.
The Phase 2 design splits the recency curve by metric family so tactical
signals (possession, pass volume) keep more of their value across years,
while production metrics (xG, shots) decay faster.

A composite ``compute_match_weight`` is the single entry point used by
:mod:`wcpredict.team_profile`; everything else here is supporting building
blocks kept module-level so they can be unit-tested in isolation.

Roster overlap is a TODO: we don't yet store lineups for historical
matches, so the current fallback is a no-op (factor=1.0). When player
appearance histories are imported this can become a real multiplier.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from wcpredict.team_profile import METRIC_CATALOG


# Metric family → half-life in days. Tactical/style traits change slowly
# (a team's possession-passing identity tends to persist across cycles),
# while raw production and discipline are noisier and need a tighter window.
FAMILY_HALF_LIFE_DAYS: dict[str, float] = {
    "tactical": 900.0,
    "offense": 540.0,
    "defense": 540.0,
    "goalkeeper": 540.0,
    "discipline": 360.0,
}
DEFAULT_HALF_LIFE_DAYS = 540.0

# Metrics inside METRIC_CATALOG that should fall into "discipline" or
# "tactical" instead of their broader catalog dimension ("style").
_DISCIPLINE_METRIC_TOKENS = (
    "tarjetas", "faltas", "fueras_de_juego", "perdidas",
)


def metric_family(metric_name: str) -> str:
    """Map a deep metric to the family that drives its recency curve."""
    dim, _ = METRIC_CATALOG.get(metric_name, ("style", False))
    if dim != "style":
        return dim
    lowered = metric_name.lower()
    if any(token in lowered for token in _DISCIPLINE_METRIC_TOKENS):
        return "discipline"
    return "tactical"


def recency_weight(
    played_at_utc: datetime,
    as_of_utc: datetime,
    *,
    family: str | None = None,
    half_life_days: float | None = None,
) -> float:
    """Exponential decay with family-aware half-life.

    Either ``family`` or ``half_life_days`` may be supplied; the second
    takes precedence so callers can override per-metric defaults when
    needed (used by backtests that pin a constant for reproducibility).
    """
    if played_at_utc.tzinfo is None:
        played_at_utc = played_at_utc.replace(tzinfo=timezone.utc)
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    if played_at_utc >= as_of_utc:
        return 0.0
    hl = (
        half_life_days
        if half_life_days is not None
        else FAMILY_HALF_LIFE_DAYS.get(family or "", DEFAULT_HALF_LIFE_DAYS)
    )
    age_days = (as_of_utc - played_at_utc).total_seconds() / 86400.0
    return math.pow(0.5, age_days / max(hl, 1.0))


# Substring → weight. Order matters: the first match wins so list the
# strongest matches (current WC, recent specific tournaments) before the
# generic family tokens (qualifiers, friendlies).
_COMPETITION_RULES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("fifa world cup 2026", "world cup 2026"), 3.00),
    (("euro 2024", "uefa euro 2024"), 1.30),
    (("copa américa 2024", "copa america 2024"), 1.30),
    (("euro 2020",), 1.10),
    (("copa américa 2021", "copa america 2021"), 1.10),
    (("fifa world cup 2022", "world cup 2022"), 1.40),
    (("fifa world cup 2018", "world cup 2018"), 1.10),
    (("fifa world cup 2014", "world cup 2014"), 0.90),
    (("africa cup of nations 2024", "afcon 2024"), 1.10),
    (("africa cup of nations 2023", "afcon 2023"), 1.10),
    (("asian cup 2024", "afc asian cup 2024"), 1.10),
    (("wc qualification", "world cup qualific", "wcq"), 1.00),
    (("euro qualification", "uefa euro qualific"), 0.95),
    (("nations league a", "uefa nations league a"), 0.95),
    (("nations league b", "uefa nations league b"), 0.75),
    (("nations league c", "uefa nations league c"), 0.65),
    (("nations league d",), 0.55),
    (("nations league",), 0.85),
    (("uefa euro", "fifa world cup", "copa américa", "copa america",
      "africa cup of nations", "afcon", "asian cup",
      "gold cup", "confederations cup"), 1.00),
    (("qualif",), 0.85),
)


def competition_weight(competition: str, as_of_utc: datetime | None = None) -> float:
    """Weight per competition with explicit boosts for recent flagship tournaments.

    Friendlies played in the same calendar year as the live World Cup get
    a small extra boost (0.55 vs 0.35) because they often feature the
    actual squad about to play the tournament.
    """
    if not competition:
        return 0.70
    c = competition.lower().strip()
    for tokens, value in _COMPETITION_RULES:
        if any(token in c for token in tokens):
            return value
    if "friendly" in c or "amistos" in c:
        if as_of_utc is not None and as_of_utc.year == 2026:
            return 0.55
        return 0.35
    return 0.70


def opponent_weight(
    opponent_strength: float | None,
    mean_strength: float,
    *,
    floor: float = 0.4,
    ceiling: float = 2.5,
) -> float:
    """Multiplier so stats against a strong rival count more than weak ones.

    Returns 1.0 when ``opponent_strength`` is unknown or the mean is zero.
    Capped to ``[floor, ceiling]`` to avoid letting a single Brazil or
    San Marino fixture overwhelm the profile.
    """
    if opponent_strength is None or mean_strength <= 0:
        return 1.0
    raw = opponent_strength / mean_strength
    return max(floor, min(ceiling, raw))


def roster_overlap_weight(*_args, **_kwargs) -> float:
    """Placeholder: full implementation needs lineup history per match.

    Returns 1.0 until we have a persistent map of `(match_id → starting XI)`
    for historical matches. The composite function calls it so plumbing
    is in place; flipping it on later is a one-function change.
    """
    return 1.0


LOW_INTENSITY_FACTOR = 0.30


def compute_match_weight(
    metric: str,
    played_at_utc: datetime,
    as_of_utc: datetime,
    *,
    competition: str = "",
    opponent_strength: float | None = None,
    mean_strength: float = 1.0,
    low_intensity: bool = False,
) -> float:
    """One-stop combined weight for a single (metric, match, team) cell.

    Returns 0.0 if the match is in the future relative to ``as_of_utc``.
    ``low_intensity=True`` (used for MD3 dead-rubber matches with rotated
    line-ups) reduces the contribution by 70%.
    """
    w_recency = recency_weight(played_at_utc, as_of_utc, family=metric_family(metric))
    if w_recency <= 0:
        return 0.0
    w_comp = competition_weight(competition, as_of_utc=as_of_utc)
    w_opp = opponent_weight(opponent_strength, mean_strength)
    w_roster = roster_overlap_weight()
    w_intensity = LOW_INTENSITY_FACTOR if low_intensity else 1.0
    return w_recency * w_comp * w_opp * w_roster * w_intensity
