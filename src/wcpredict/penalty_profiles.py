from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import math
import re
import unicodedata


GLOBAL_CONVERSION = 0.76
GLOBAL_PENALTY_SAVE = 1.0 - GLOBAL_CONVERSION
PRIOR_ATTEMPTS = 12.0
SHOOTOUT_WEIGHT = 1.50
REGULAR_WEIGHT = 1.00
RECENCY_HALF_LIFE_DAYS = 1095.0
VALID_TAKER_OUTCOMES = {"scored", "saved", "missed", "off_target", "woodwork"}


@dataclass(frozen=True)
class PenaltyPlayerProfile:
    player_name: str
    position: str | None
    attempts: int
    shootout_attempts: int
    conversion: float
    low: float
    high: float
    effective_attempts: float
    taker_propensity: float
    confidence: str


@dataclass(frozen=True)
class GoalkeeperPenaltyProfile:
    player_name: str
    penalty_save_rate: float
    faced_penalties: int
    penalty_history_weight: float
    source: str


def _name_key(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def penalty_attempt_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    raw = str(value).strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _confidence(effective_attempts: float) -> str:
    if effective_attempts >= 8.0:
        return "high"
    if effective_attempts >= 3.0:
        return "medium"
    return "low"


def _outcome(row: dict) -> str:
    return str(row.get("outcome") or "").casefold()


def _is_goalkeeper_save(row: dict) -> bool:
    outcome = _outcome(row)
    if outcome == "saved":
        return True
    if outcome != "missed":
        return False
    raw = row.get("raw")
    if raw is None and row.get("raw_json"):
        try:
            raw = json.loads(str(row["raw_json"]))
        except (TypeError, ValueError):
            raw = None
    cells = raw.get("cells", []) if isinstance(raw, dict) else []
    text = " ".join(str(cell) for cell in cells).casefold()
    return "saved" in text


def build_player_profile(
    player_name: str,
    position: str | None,
    attempts: list[dict],
    as_of: date,
) -> PenaltyPlayerProfile:
    player_key = _name_key(player_name)
    relevant = [
        row for row in attempts
        if _name_key(row.get("player_name")) == player_key
        and _outcome(row) in VALID_TAKER_OUTCOMES
    ]
    weighted_successes = 0.0
    weighted_failures = 0.0
    shootout_attempts = 0
    for row in relevant:
        shootout = str(row.get("phase") or "").casefold() == "shootout"
        shootout_attempts += int(shootout)
        phase_weight = SHOOTOUT_WEIGHT if shootout else REGULAR_WEIGHT
        attempted_on = penalty_attempt_date(row.get("attempted_on"))
        age_days = max(0, (as_of - attempted_on).days) if attempted_on else 0
        weight = phase_weight * (0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))
        if _outcome(row) == "scored":
            weighted_successes += weight
        else:
            weighted_failures += weight

    effective_attempts = weighted_successes + weighted_failures
    alpha = GLOBAL_CONVERSION * PRIOR_ATTEMPTS + weighted_successes
    beta = (1.0 - GLOBAL_CONVERSION) * PRIOR_ATTEMPTS + weighted_failures
    total = alpha + beta
    conversion = alpha / total
    variance = (alpha * beta) / ((total ** 2) * (total + 1.0))
    margin = 1.645 * math.sqrt(variance)
    position_key = str(position or "").upper()[:2]
    position_prior = {"FW": 0.45, "MF": 0.30, "DF": 0.15, "GK": 0.03}.get(position_key, 0.20)
    propensity = (
        math.log1p(effective_attempts) + 0.75 * math.log1p(shootout_attempts)
        if relevant else position_prior
    )
    return PenaltyPlayerProfile(
        player_name=player_name,
        position=position,
        attempts=len(relevant),
        shootout_attempts=shootout_attempts,
        conversion=conversion,
        low=max(0.05, conversion - margin),
        high=min(0.98, conversion + margin),
        effective_attempts=effective_attempts,
        taker_propensity=propensity,
        confidence=_confidence(effective_attempts),
    )


def build_player_profiles(
    squad: list[dict], attempts: list[dict], as_of: date
) -> dict[str, PenaltyPlayerProfile]:
    return {
        str(player["player_name"]): build_player_profile(
            str(player["player_name"]), player.get("position"), attempts, as_of
        )
        for player in squad
        if player.get("player_name")
    }


def build_goalkeeper_profile(
    player: dict,
    attempts: list[dict],
    deep_save_rate: float | None = None,
) -> GoalkeeperPenaltyProfile:
    player_name = str(player.get("player_name") or "")
    player_key = _name_key(player_name)
    matching = [
        row for row in attempts
        if _name_key(row.get("goalkeeper_name")) == player_key
    ]
    tournament = [
        row for row in matching
        if str(row.get("source_provider") or "") == "world_cup_2026_manual"
        and _outcome(row) in {"scored", "saved", "off_target", "woodwork"}
    ]
    historical = [row for row in matching if row not in tournament]
    observed_saves = [row for row in historical if _is_goalkeeper_save(row)]
    # Transfermarkt's generic missed-penalty page does not always say whether
    # the goalkeeper saved it or the taker missed. Avoid a one-sided sample of
    # scored penalties unless at least one save is explicitly identified.
    historical_relevant = (
        [row for row in historical if _outcome(row) == "scored" or _is_goalkeeper_save(row)]
        if observed_saves else []
    )
    relevant = historical_relevant + tournament
    saves = sum(_is_goalkeeper_save(row) for row in relevant)
    faced = len(relevant)
    history_weight = faced / (PRIOR_ATTEMPTS + faced)
    penalty_rate = (GLOBAL_PENALTY_SAVE * PRIOR_ATTEMPTS + saves) / (PRIOR_ATTEMPTS + faced)

    general_rate = deep_save_rate
    if general_rate is None and player.get("save_percentage") is not None:
        general_rate = float(player["save_percentage"]) / 100.0
    general_weight = 0.0 if general_rate is None else 0.15 / (1.0 + faced)
    if general_rate is not None:
        general_rate = min(1.0, max(0.0, float(general_rate)))
        penalty_rate = (1.0 - general_weight) * penalty_rate + general_weight * general_rate

    if faced:
        source = "penalty_history"
    elif general_rate is not None:
        source = "general_save_fallback"
    else:
        source = "global_prior"
    return GoalkeeperPenaltyProfile(
        player_name=player_name,
        penalty_save_rate=min(0.60, max(0.05, penalty_rate)),
        faced_penalties=faced,
        penalty_history_weight=history_weight,
        source=source,
    )
