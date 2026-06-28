from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import math
from random import Random

from wcpredict.names import canonical_team_name
from wcpredict.penalty_profiles import (
    GoalkeeperPenaltyProfile,
    PenaltyPlayerProfile,
    build_goalkeeper_profile,
    build_player_profiles,
)
from wcpredict.penalty_shootout_simulator import simulate_shootout
from wcpredict.penalty_substitution_model import (
    MatchWindowState,
    ScenarioPlayer,
    SubstitutionConfig,
    normalize_role,
    simulate_substitution_path,
)


GLOBAL_PENALTY_CONVERSION = 0.76
PRIOR_ATTEMPTS = 12.0
RECENT_BLEND = 0.35
MAX_SHOOTOUT_SHIFT = 0.14
DEFAULT_SIMULATIONS = 25_000
MAX_SUBSTITUTION_PATH_PAIRS = 1_024
PENALTY_MODEL_VERSION = "path-monte-carlo-v1"


@dataclass(frozen=True)
class PenaltyTeamProfile:
    team_name: str
    attempts: int
    scored: int
    conversion: float
    recent_attempts: int
    recent_conversion: float | None


@dataclass(frozen=True)
class PenaltyPlayerContribution:
    player_name: str
    team_name: str
    role: str
    on_field_probability: float
    first_five_probability: float
    any_kick_probability: float
    conversion: float
    attempts: int
    confidence: str


@dataclass(frozen=True)
class PenaltyCoverage:
    squad_players: int
    players_with_history: int
    attempts: int
    team_a_squad_players: int
    team_b_squad_players: int
    team_a_players_with_history: int
    team_b_players_with_history: int


@dataclass(frozen=True)
class PenaltyMatchContext:
    team_a: PenaltyTeamProfile
    team_b: PenaltyTeamProfile
    team_a_shootout_win_probability: float
    explanation: str
    team_b_shootout_win_probability: float = 0.5
    player_rows: tuple[PenaltyPlayerContribution, ...] = ()
    coverage: PenaltyCoverage = PenaltyCoverage(0, 0, 0, 0, 0, 0, 0)
    simulations: int = 0
    standard_error: float = 0.0


def _team_rows(team_name: str, attempts: list[dict]) -> list[dict]:
    canonical = canonical_team_name(team_name)
    return [
        row for row in attempts
        if canonical_team_name(str(row.get("team_name") or "")) == canonical
    ]


def build_penalty_team_profile(team_name: str, attempts: list[dict]) -> PenaltyTeamProfile:
    team_rows = _team_rows(team_name, attempts)
    valid_rows = [
        row for row in team_rows
        if str(row.get("outcome") or "").casefold() in {"scored", "saved", "missed"}
    ]
    scored = sum(str(row.get("outcome") or "").casefold() == "scored" for row in valid_rows)
    total = len(valid_rows)
    bayes = (scored + GLOBAL_PENALTY_CONVERSION * PRIOR_ATTEMPTS) / (total + PRIOR_ATTEMPTS)
    recent_rows = sorted(valid_rows, key=_attempt_sort_key, reverse=True)[:6]
    recent_conversion = None
    if len(recent_rows) >= 3:
        recent_scored = sum(str(row.get("outcome") or "").casefold() == "scored" for row in recent_rows)
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


def _legacy_context(
    team_a: str,
    team_b: str,
    attempts: list[dict],
    profile_a: PenaltyTeamProfile,
    profile_b: PenaltyTeamProfile,
) -> PenaltyMatchContext:
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
    coverage = PenaltyCoverage(
        squad_players=0,
        players_with_history=0,
        attempts=profile_a.attempts + profile_b.attempts,
        team_a_squad_players=0,
        team_b_squad_players=0,
        team_a_players_with_history=0,
        team_b_players_with_history=0,
    )
    return PenaltyMatchContext(
        team_a=profile_a,
        team_b=profile_b,
        team_a_shootout_win_probability=p_a,
        team_b_shootout_win_probability=1.0 - p_a,
        coverage=coverage,
        explanation=explanation,
    )


def _mapping_value(mapping: dict | None, team_name: str, default):
    if not mapping:
        return default
    wanted = canonical_team_name(team_name)
    for key, value in mapping.items():
        if canonical_team_name(str(key)) == wanted:
            return value
    return default


def _score_path(rng: Random) -> list[MatchWindowState]:
    delta = 0
    states: list[MatchWindowState] = []
    for minute in (60, 70, 82, 98, 112):
        draw_weight = 0.58 if minute < 90 else 0.70
        roll = rng.random()
        if roll < draw_weight:
            delta = 0
        elif roll < draw_weight + (1.0 - draw_weight) / 2.0:
            delta = 1
        else:
            delta = -1
        states.append(MatchWindowState(minute=minute, score_delta=delta))
    return states


def _keeper_for_state(
    team_name: str,
    state_players: tuple[ScenarioPlayer, ...],
    squad_by_name: dict[str, dict],
    attempts: list[dict],
    supplied: dict[str, GoalkeeperPenaltyProfile] | None,
    deep_rates: dict[str, float] | None,
) -> GoalkeeperPenaltyProfile | None:
    supplied_profile = _mapping_value(supplied, team_name, None)
    if supplied_profile is not None:
        return supplied_profile
    keeper = next((player for player in state_players if player.role == "GK"), None)
    if keeper is None:
        return None
    deep_rate = (deep_rates or {}).get(keeper.player_name)
    return build_goalkeeper_profile(
        squad_by_name.get(keeper.player_name, {"player_name": keeper.player_name}),
        attempts,
        deep_rate,
    )


def build_penalty_match_context(
    team_a: str,
    team_b: str,
    attempts: list[dict],
    *,
    squads: dict[str, list[dict]] | None = None,
    lineups: dict[str, list[str | dict]] | None = None,
    goalkeeper_profiles: dict[str, GoalkeeperPenaltyProfile] | None = None,
    deep_goalkeeper_rates: dict[str, float] | None = None,
    as_of: date | None = None,
    seed: int = 0,
    simulations: int = DEFAULT_SIMULATIONS,
    substitution_config: SubstitutionConfig | None = None,
) -> PenaltyMatchContext:
    profile_a = build_penalty_team_profile(team_a, attempts)
    profile_b = build_penalty_team_profile(team_b, attempts)
    squad_a = _mapping_value(squads, team_a, None)
    squad_b = _mapping_value(squads, team_b, None)
    if not squad_a or not squad_b:
        return _legacy_context(team_a, team_b, attempts, profile_a, profile_b)
    if simulations <= 0:
        raise ValueError("simulations must be positive")

    as_of = as_of or date.today()
    attempts_a = _team_rows(team_a, attempts)
    attempts_b = _team_rows(team_b, attempts)
    player_profiles_a = build_player_profiles(squad_a, attempts_a, as_of)
    player_profiles_b = build_player_profiles(squad_b, attempts_b, as_of)
    lineup_a = _mapping_value(lineups, team_a, None)
    lineup_b = _mapping_value(lineups, team_b, None)
    squad_a_by_name = {str(row.get("player_name")): row for row in squad_a}
    squad_b_by_name = {str(row.get("player_name")): row for row in squad_b}
    rng = Random(seed)
    config = substitution_config or SubstitutionConfig()
    wins_a = 0
    on_field = Counter()
    first_five = Counter()
    any_kick = Counter()

    path_pair_count = min(
        simulations,
        MAX_SUBSTITUTION_PATH_PAIRS,
        max(128, round(math.sqrt(simulations) * 6.5)),
    )
    path_bank = []
    for _ in range(path_pair_count):
        path_a = _score_path(rng)
        path_b = [MatchWindowState(item.minute, -item.score_delta) for item in path_a]
        state_a = simulate_substitution_path(squad_a, lineup_a, path_a, rng, config)
        state_b = simulate_substitution_path(squad_b, lineup_b, path_b, rng, config)
        path_bank.append((state_a, state_b))

    keeper_cache_a: dict[str, GoalkeeperPenaltyProfile | None] = {}
    keeper_cache_b: dict[str, GoalkeeperPenaltyProfile | None] = {}
    for _ in range(simulations):
        state_a, state_b = path_bank[rng.randrange(path_pair_count)]
        for player in state_a.players:
            on_field[(team_a, player.player_name)] += 1
        for player in state_b.players:
            on_field[(team_b, player.player_name)] += 1
        keeper_name_a = next((player.player_name for player in state_a.players if player.role == "GK"), "")
        keeper_name_b = next((player.player_name for player in state_b.players if player.role == "GK"), "")
        if keeper_name_a not in keeper_cache_a:
            keeper_cache_a[keeper_name_a] = _keeper_for_state(
                team_a, state_a.players, squad_a_by_name, attempts,
                goalkeeper_profiles, deep_goalkeeper_rates,
            )
        if keeper_name_b not in keeper_cache_b:
            keeper_cache_b[keeper_name_b] = _keeper_for_state(
                team_b, state_b.players, squad_b_by_name, attempts,
                goalkeeper_profiles, deep_goalkeeper_rates,
            )
        keeper_a = keeper_cache_a[keeper_name_a]
        keeper_b = keeper_cache_b[keeper_name_b]
        result = simulate_shootout(
            state_a, state_b, player_profiles_a, player_profiles_b,
            keeper_a, keeper_b, rng,
        )
        wins_a += result.winner == "A"
        for name in result.team_a_takers[:5]:
            first_five[(team_a, name)] += 1
        for name in result.team_b_takers[:5]:
            first_five[(team_b, name)] += 1
        for name in set(result.team_a_takers):
            any_kick[(team_a, name)] += 1
        for name in set(result.team_b_takers):
            any_kick[(team_b, name)] += 1

    player_rows: list[PenaltyPlayerContribution] = []
    for team_name, squad, profiles in (
        (team_a, squad_a, player_profiles_a),
        (team_b, squad_b, player_profiles_b),
    ):
        for player in squad:
            name = str(player.get("player_name") or "")
            if not name or name not in profiles:
                continue
            profile: PenaltyPlayerProfile = profiles[name]
            player_rows.append(PenaltyPlayerContribution(
                player_name=name,
                team_name=team_name,
                role=normalize_role(player.get("position")),
                on_field_probability=on_field[(team_name, name)] / simulations,
                first_five_probability=first_five[(team_name, name)] / simulations,
                any_kick_probability=any_kick[(team_name, name)] / simulations,
                conversion=profile.conversion,
                attempts=profile.attempts,
                confidence=profile.confidence,
            ))
    player_rows.sort(key=lambda row: (row.team_name, -row.first_five_probability, row.player_name))
    with_history_a = sum(profile.attempts > 0 for profile in player_profiles_a.values())
    with_history_b = sum(profile.attempts > 0 for profile in player_profiles_b.values())
    coverage = PenaltyCoverage(
        squad_players=len(player_profiles_a) + len(player_profiles_b),
        players_with_history=with_history_a + with_history_b,
        attempts=profile_a.attempts + profile_b.attempts,
        team_a_squad_players=len(player_profiles_a),
        team_b_squad_players=len(player_profiles_b),
        team_a_players_with_history=with_history_a,
        team_b_players_with_history=with_history_b,
    )
    p_a = wins_a / simulations
    standard_error = math.sqrt(p_a * (1.0 - p_a) / simulations)
    explanation = (
        f"{simulations:,} escenarios prepartido con cambios por rol y marcador; "
        f"probabilidad de tanda para {team_a}: {p_a:.1%}. "
        f"Cobertura penalty_history: {coverage.players_with_history}/{coverage.squad_players} jugadores."
    )
    return PenaltyMatchContext(
        team_a=profile_a,
        team_b=profile_b,
        team_a_shootout_win_probability=p_a,
        team_b_shootout_win_probability=1.0 - p_a,
        player_rows=tuple(player_rows),
        coverage=coverage,
        simulations=simulations,
        standard_error=standard_error,
        explanation=explanation,
    )


def _attempt_sort_key(row: dict) -> tuple[date, str]:
    raw = str(row.get("attempted_on") or "")
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        parsed = date.min
    return parsed, str(row.get("source_row_key") or "")
