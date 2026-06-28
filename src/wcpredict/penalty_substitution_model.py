from __future__ import annotations

from dataclasses import dataclass
from random import Random
import math
import re


ROLES = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")
ATTACK_LEVEL = {"GK": -2, "CB": 0, "FB": 1, "DM": 1, "CM": 2, "AM": 3, "W": 3, "ST": 4}
ADJACENT = {
    "GK": {"GK"},
    "CB": {"CB", "FB", "DM"},
    "FB": {"FB", "CB", "DM", "CM", "W"},
    "DM": {"DM", "CB", "FB", "CM"},
    "CM": {"CM", "DM", "FB", "AM"},
    "AM": {"AM", "CM", "W", "ST"},
    "W": {"W", "FB", "AM", "ST"},
    "ST": {"ST", "AM", "W"},
}


@dataclass(frozen=True)
class SubstitutionConfig:
    regulation_limit: int = 5
    extra_time_additional: int = 1
    windows: tuple[tuple[int, int], ...] = (
        (55, 65), (65, 75), (75, 90), (90, 105), (105, 120)
    )
    change_probability: float = 0.62
    max_per_window: int = 2


@dataclass(frozen=True)
class MatchWindowState:
    minute: int
    score_delta: int


@dataclass(frozen=True)
class ScenarioPlayer:
    player_name: str
    role: str
    starts: int = 0
    games: int = 0
    minutes: int = 0
    card_risk: float = 0.0
    forced_sub_risk: float = 0.0


@dataclass(frozen=True)
class SubstitutionEvent:
    minute: int
    out_player: str
    in_player: str
    out_role: str
    in_role: str
    role_distance: int
    attacking_change: bool


@dataclass(frozen=True)
class EndOfExtraTimeState:
    players: tuple[ScenarioPlayer, ...]
    events: tuple[SubstitutionEvent, ...]
    regulation_substitutions: int
    extra_time_substitutions: int

    @property
    def player_names(self) -> tuple[str, ...]:
        return tuple(player.player_name for player in self.players)


def normalize_role(position: object) -> str:
    value = re.sub(r"[^a-z]+", " ", str(position or "").casefold()).strip()
    if value in {"gk", "goalkeeper", "keeper"} or "goalkeeper" in value:
        return "GK"
    if value in {"cb", "centre back", "center back", "df", "defender"}:
        return "CB"
    if value in {"lb", "rb", "lwb", "rwb", "fb", "full back", "left back", "right back"}:
        return "FB"
    if value in {"dm", "dmf", "defensive midfield", "defensive midfielder"}:
        return "DM"
    if value in {"am", "amf", "attacking midfield", "attacking midfielder"}:
        return "AM"
    if value in {"lw", "rw", "winger", "left winger", "right winger"}:
        return "W"
    if value in {"st", "cf", "fw", "forward", "striker", "centre forward", "center forward"}:
        return "ST"
    if value in {"mf", "cm", "cmf", "midfield", "midfielder", "central midfield"}:
        return "CM"
    if "back" in value:
        return "FB" if any(word in value for word in ("left", "right", "full", "wing")) else "CB"
    if "wing" in value:
        return "W"
    if "forward" in value or "striker" in value:
        return "ST"
    if "midfield" in value:
        return "CM"
    return "CM"


def _available(row: dict) -> bool:
    if row.get("available") is False:
        return False
    status = str(row.get("availability") or row.get("status") or "").casefold()
    return not any(word in status for word in ("injured", "suspended", "unavailable", "out"))


def _scenario_player(row: dict) -> ScenarioPlayer:
    return ScenarioPlayer(
        player_name=str(row.get("player_name") or row.get("name") or "").strip(),
        role=normalize_role(row.get("position") or row.get("role")),
        starts=int(row.get("starts") or 0),
        games=int(row.get("games") or 0),
        minutes=int(row.get("minutes") or 0),
        card_risk=float(row.get("card_risk") or 0.0),
        forced_sub_risk=float(row.get("forced_sub_risk") or 0.0),
    )


def _weighted_pick(items: list, weights: list[float], rng: Random):
    total = sum(max(0.0, weight) for weight in weights)
    if not items or total <= 0:
        return None
    threshold = rng.random() * total
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += max(0.0, weight)
        if threshold <= cumulative:
            return item
    return items[-1]


def _start_weight(player: ScenarioPlayer) -> float:
    average_minutes = player.minutes / max(1, player.games)
    return 1.0 + 2.2 * player.starts + 0.035 * average_minutes + 0.15 * player.games


def build_on_field_profiles(
    squad: list[dict],
    lineup: list[str | dict] | None,
    rng: Random,
) -> tuple[ScenarioPlayer, ...]:
    available = [_scenario_player(row) for row in squad if _available(row)]
    available = [player for player in available if player.player_name]
    by_name = {player.player_name: player for player in available}
    requested_names = [
        str(item.get("player_name") or item.get("name")) if isinstance(item, dict) else str(item)
        for item in (lineup or [])
    ]
    chosen = [by_name[name] for name in requested_names if name in by_name]
    chosen_names = {player.player_name for player in chosen}

    keepers = [player for player in available if player.role == "GK" and player.player_name not in chosen_names]
    current_keepers = [player for player in chosen if player.role == "GK"]
    if not current_keepers and keepers:
        keeper = _weighted_pick(keepers, [_start_weight(player) for player in keepers], rng)
        chosen.append(keeper)
        chosen_names.add(keeper.player_name)
    elif len(current_keepers) > 1:
        retained = max(current_keepers, key=_start_weight)
        chosen = [player for player in chosen if player.role != "GK" or player == retained]
        chosen_names = {player.player_name for player in chosen}

    while len(chosen) < 11:
        candidates = [
            player for player in available
            if player.player_name not in chosen_names and player.role != "GK"
        ]
        selected = _weighted_pick(candidates, [_start_weight(player) for player in candidates], rng)
        if selected is None:
            break
        chosen.append(selected)
        chosen_names.add(selected.player_name)
    return tuple(chosen[:11])


def role_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    frontier = {left}
    visited = {left}
    for distance in range(1, len(ROLES) + 1):
        frontier = {neighbor for role in frontier for neighbor in ADJACENT[role]} - visited
        if right in frontier:
            return distance
        visited.update(frontier)
    return len(ROLES)


def _pair_weight(
    outgoing: ScenarioPlayer,
    incoming: ScenarioPlayer,
    state: MatchWindowState,
) -> float:
    distance = role_distance(outgoing.role, incoming.role)
    if outgoing.role == "GK" or incoming.role == "GK":
        if outgoing.role != incoming.role:
            return 0.0
        return 0.015 if state.minute < 105 else 0.06
    if state.score_delta == 0 and distance > 1:
        return 0.0
    if distance > 2:
        return 0.0
    average_minutes = outgoing.minutes / max(1, outgoing.games)
    fatigue = 0.5 + state.minute / 120.0 + max(0.0, (state.minute - average_minutes) / 90.0)
    risk = 1.0 + 1.8 * outgoing.card_risk + 2.5 * outgoing.forced_sub_risk
    freshness = 1.0 + max(0.0, 100.0 - incoming.minutes / max(1, incoming.games)) / 140.0
    role_fit = math.exp(-1.35 * distance)
    attack_delta = ATTACK_LEVEL[incoming.role] - ATTACK_LEVEL[outgoing.role]
    tactical = math.exp(-0.85 * state.score_delta * attack_delta)
    return fatigue * risk * freshness * role_fit * tactical


def simulate_substitution_path(
    squad: list[dict],
    lineup: list[str | dict] | None,
    window_states: list[MatchWindowState],
    rng: Random,
    config: SubstitutionConfig | None = None,
) -> EndOfExtraTimeState:
    config = config or SubstitutionConfig()
    on_field = list(build_on_field_profiles(squad, lineup, rng))
    all_players = [_scenario_player(row) for row in squad if _available(row)]
    used_names = {player.player_name for player in on_field}
    removed_names: set[str] = set()
    events: list[SubstitutionEvent] = []
    regulation_substitutions = 0
    extra_time_substitutions = 0

    for state in sorted(window_states, key=lambda item: item.minute):
        extra_time = state.minute > 90
        remaining_regulation_quota = config.regulation_limit - regulation_substitutions
        remaining = remaining_regulation_quota
        if extra_time:
            remaining += config.extra_time_additional - extra_time_substitutions
        if remaining <= 0:
            continue
        changes = min(config.max_per_window, remaining)
        for _ in range(changes):
            if rng.random() > config.change_probability:
                continue
            bench = [
                player for player in all_players
                if player.player_name not in used_names and player.player_name not in removed_names
            ]
            pairs: list[tuple[ScenarioPlayer, ScenarioPlayer]] = []
            weights: list[float] = []
            for outgoing in on_field:
                for incoming in bench:
                    weight = _pair_weight(outgoing, incoming, state)
                    if weight > 0:
                        pairs.append((outgoing, incoming))
                        weights.append(weight)
            selected = _weighted_pick(pairs, weights, rng)
            if selected is None:
                break
            outgoing, incoming = selected
            distance = role_distance(outgoing.role, incoming.role)
            on_field[on_field.index(outgoing)] = incoming
            removed_names.add(outgoing.player_name)
            used_names.add(incoming.player_name)
            events.append(SubstitutionEvent(
                minute=state.minute,
                out_player=outgoing.player_name,
                in_player=incoming.player_name,
                out_role=outgoing.role,
                in_role=incoming.role,
                role_distance=distance,
                attacking_change=ATTACK_LEVEL[incoming.role] > ATTACK_LEVEL[outgoing.role],
            ))
            if regulation_substitutions < config.regulation_limit:
                regulation_substitutions += 1
            elif extra_time:
                extra_time_substitutions += 1

    return EndOfExtraTimeState(
        players=tuple(on_field),
        events=tuple(events),
        regulation_substitutions=regulation_substitutions,
        extra_time_substitutions=extra_time_substitutions,
    )
