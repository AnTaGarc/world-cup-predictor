from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Callable, Iterable

from wcpredict.penalty_profiles import (
    GLOBAL_CONVERSION,
    GLOBAL_PENALTY_SAVE,
    GoalkeeperPenaltyProfile,
    PenaltyPlayerProfile,
)


@dataclass(frozen=True)
class ShootoutResult:
    winner: str
    team_a_goals: int
    team_b_goals: int
    team_a_takers: tuple[str, ...]
    team_b_takers: tuple[str, ...]

    @property
    def team_a_kicks(self) -> int:
        return len(self.team_a_takers)

    @property
    def team_b_kicks(self) -> int:
        return len(self.team_b_takers)

    @property
    def total_kicks(self) -> int:
        return self.team_a_kicks + self.team_b_kicks

    @property
    def team_a_unique_takers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.team_a_takers))

    @property
    def team_b_unique_takers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.team_b_takers))


def _player_name(player: object) -> str:
    if isinstance(player, dict):
        return str(player.get("player_name") or player.get("name") or "")
    return str(getattr(player, "player_name", player))


def _eligible_players(state_or_players: object) -> list[object]:
    players = getattr(state_or_players, "players", state_or_players)
    return list(players)


def _weighted_permutation(
    players: list[object],
    profiles: dict[str, PenaltyPlayerProfile],
    rng: Random,
) -> list[str]:
    remaining = [_player_name(player) for player in players]
    order: list[str] = []
    while remaining:
        weights = [max(0.02, profiles.get(name).taker_propensity if name in profiles else 0.20) for name in remaining]
        threshold = rng.random() * sum(weights)
        cumulative = 0.0
        selected_index = len(remaining) - 1
        for index, weight in enumerate(weights):
            cumulative += weight
            if threshold <= cumulative:
                selected_index = index
                break
        order.append(remaining.pop(selected_index))
    return order


class _TakerQueue:
    def __init__(self, players, profiles, rng):
        self.players = players
        self.profiles = profiles
        self.rng = rng
        self.current: list[str] = []

    def next(self) -> str:
        if not self.current:
            self.current = _weighted_permutation(self.players, self.profiles, self.rng)
        return self.current.pop(0)


def kick_conversion_probability(
    taker: PenaltyPlayerProfile | None,
    opposing_keeper: GoalkeeperPenaltyProfile | None,
) -> float:
    taker_rate = taker.conversion if taker is not None else GLOBAL_CONVERSION
    keeper_rate = opposing_keeper.penalty_save_rate if opposing_keeper is not None else GLOBAL_PENALTY_SAVE
    probability = GLOBAL_CONVERSION + (taker_rate - GLOBAL_CONVERSION) - (keeper_rate - GLOBAL_PENALTY_SAVE)
    return min(0.95, max(0.35, probability))


def _run_shootout(
    next_a: Callable[[], tuple[str, bool]],
    next_b: Callable[[], tuple[str, bool]],
) -> ShootoutResult:
    goals_a = goals_b = 0
    takers_a: list[str] = []
    takers_b: list[str] = []

    for index in range(5):
        taker, scored = next_a()
        takers_a.append(taker)
        goals_a += int(scored)
        remaining_a = 4 - index
        remaining_b = 5 - index
        if goals_a > goals_b + remaining_b:
            return ShootoutResult("A", goals_a, goals_b, tuple(takers_a), tuple(takers_b))
        if goals_b > goals_a + remaining_a:
            return ShootoutResult("B", goals_a, goals_b, tuple(takers_a), tuple(takers_b))

        taker, scored = next_b()
        takers_b.append(taker)
        goals_b += int(scored)
        remaining = 4 - index
        if goals_a > goals_b + remaining:
            return ShootoutResult("A", goals_a, goals_b, tuple(takers_a), tuple(takers_b))
        if goals_b > goals_a + remaining:
            return ShootoutResult("B", goals_a, goals_b, tuple(takers_a), tuple(takers_b))

    for _ in range(200):
        taker_a, scored_a = next_a()
        takers_a.append(taker_a)
        goals_a += int(scored_a)
        taker_b, scored_b = next_b()
        takers_b.append(taker_b)
        goals_b += int(scored_b)
        if scored_a != scored_b:
            winner = "A" if scored_a else "B"
            return ShootoutResult(winner, goals_a, goals_b, tuple(takers_a), tuple(takers_b))
    raise RuntimeError("Shootout did not resolve after 200 sudden-death rounds")


def simulate_shootout(
    team_a_state: object,
    team_b_state: object,
    team_a_profiles: dict[str, PenaltyPlayerProfile],
    team_b_profiles: dict[str, PenaltyPlayerProfile],
    team_a_goalkeeper: GoalkeeperPenaltyProfile | None,
    team_b_goalkeeper: GoalkeeperPenaltyProfile | None,
    rng: Random,
) -> ShootoutResult:
    players_a = _eligible_players(team_a_state)
    players_b = _eligible_players(team_b_state)
    if not players_a or not players_b:
        raise ValueError("Both teams need eligible players for a shootout")
    queue_a = _TakerQueue(players_a, team_a_profiles, rng)
    queue_b = _TakerQueue(players_b, team_b_profiles, rng)

    def next_a() -> tuple[str, bool]:
        name = queue_a.next()
        probability = kick_conversion_probability(team_a_profiles.get(name), team_b_goalkeeper)
        return name, rng.random() < probability

    def next_b() -> tuple[str, bool]:
        name = queue_b.next()
        probability = kick_conversion_probability(team_b_profiles.get(name), team_a_goalkeeper)
        return name, rng.random() < probability

    return _run_shootout(next_a, next_b)


def simulate_scripted_shootout(
    team_a_outcomes: Iterable[int | bool],
    team_b_outcomes: Iterable[int | bool],
    eligible_per_team: int = 11,
) -> ShootoutResult:
    outcomes_a = iter(team_a_outcomes)
    outcomes_b = iter(team_b_outcomes)
    kick_a = kick_b = 0

    def next_a() -> tuple[str, bool]:
        nonlocal kick_a
        name = f"A{kick_a % eligible_per_team + 1}"
        kick_a += 1
        return name, bool(next(outcomes_a))

    def next_b() -> tuple[str, bool]:
        nonlocal kick_b
        name = f"B{kick_b % eligible_per_team + 1}"
        kick_b += 1
        return name, bool(next(outcomes_b))

    return _run_shootout(next_a, next_b)
