from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import re

from wcpredict.models import Match
from wcpredict.names import canonical_team_name, same_team
from wcpredict.ratings import MatchResult


DRAW_INCENTIVE_LOGIT_BOOST = 0.22


@dataclass(frozen=True)
class DrawIncentiveContext:
    active: bool
    logit_boost: float = 0.0
    explanation: str = ""


def _group_key(stage: str | None) -> str | None:
    if not stage:
        return None
    match = re.search(r"Group stage\s*-\s*Group\s+([A-L])\b", stage, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _result_for_match(match: Match, results: list[MatchResult]) -> tuple[int, int] | None:
    for result in results:
        if result.played_on != match.kickoff_utc.date():
            continue
        if same_team(result.team_a, match.team_a.name) and same_team(result.team_b, match.team_b.name):
            return int(result.goals_a), int(result.goals_b)
        if same_team(result.team_a, match.team_b.name) and same_team(result.team_b, match.team_a.name):
            return int(result.goals_b), int(result.goals_a)
    return None


def _add_points(points: dict[str, int], team_a: str, team_b: str, goals_a: int, goals_b: int) -> None:
    if goals_a > goals_b:
        points[team_a] += 3
    elif goals_b > goals_a:
        points[team_b] += 3
    else:
        points[team_a] += 1
        points[team_b] += 1


def _guaranteed_top_two_by_points(team: str, points: dict[str, int]) -> bool:
    team_points = points[team]
    threats = sum(1 for other, value in points.items() if other != team and value >= team_points)
    return threats <= 1


def draw_incentive_for_match(
    match: Match,
    group_matches: list[Match],
    results: list[MatchResult],
) -> DrawIncentiveContext:
    group = _group_key(match.stage)
    if group is None or match.status == "finished":
        return DrawIncentiveContext(False)

    fixtures = [item for item in group_matches if _group_key(item.stage) == group]
    if len(fixtures) != 6 or all(item.id != match.id for item in fixtures):
        return DrawIncentiveContext(False)

    team_a = canonical_team_name(match.team_a.name)
    team_b = canonical_team_name(match.team_b.name)
    teams = {
        canonical_team_name(item.team_a.name)
        for item in fixtures
    } | {
        canonical_team_name(item.team_b.name)
        for item in fixtures
    }
    if team_a not in teams or team_b not in teams or len(teams) != 4:
        return DrawIncentiveContext(False)

    base_points = {team: 0 for team in teams}
    remaining: list[tuple[str, str]] = []
    for fixture in fixtures:
        home = canonical_team_name(fixture.team_a.name)
        away = canonical_team_name(fixture.team_b.name)
        if fixture.id == match.id:
            continue
        result = _result_for_match(fixture, results)
        if result is None:
            remaining.append((home, away))
            continue
        _add_points(base_points, home, away, result[0], result[1])

    draw_points = dict(base_points)
    draw_points[team_a] += 1
    draw_points[team_b] += 1

    for outcomes in product(("home", "draw", "away"), repeat=len(remaining)):
        scenario_points = dict(draw_points)
        for (home, away), outcome in zip(remaining, outcomes):
            if outcome == "home":
                scenario_points[home] += 3
            elif outcome == "away":
                scenario_points[away] += 3
            else:
                scenario_points[home] += 1
                scenario_points[away] += 1
        if not (
            _guaranteed_top_two_by_points(team_a, scenario_points)
            and _guaranteed_top_two_by_points(team_b, scenario_points)
        ):
            return DrawIncentiveContext(False)

    return DrawIncentiveContext(
        True,
        DRAW_INCENTIVE_LOGIT_BOOST,
        "Incentivo competitivo: el empate deja a ambos equipos clasificados por puntos en todos los escenarios restantes del grupo.",
    )
