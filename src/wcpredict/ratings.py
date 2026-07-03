from dataclasses import dataclass
from datetime import date

from wcpredict.names import canonical_team_name


@dataclass(frozen=True)
class MatchResult:
    played_on: date
    team_a: str
    team_b: str
    goals_a: int
    goals_b: int
    match_type: str


@dataclass(frozen=True)
class TeamRating:
    attack: float
    defense: float
    sample_weight: float


@dataclass(frozen=True)
class FormContribution:
    played_on: date
    opponent: str
    goals_for: int
    goals_against: int
    match_type: str
    recency_weight: float
    type_weight: float
    opponent_factor: float
    total_weight: float
    weighted_goal_difference: float
    explanation: str


def deduplicate_results(results: list[MatchResult]) -> list[MatchResult]:
    """Collapse provider duplicates and aliases into one chronological result."""
    unique: dict[tuple, MatchResult] = {}
    for result in results:
        team_a = canonical_team_name(result.team_a)
        team_b = canonical_team_name(result.team_b)
        if team_a <= team_b:
            key = (result.played_on, team_a, team_b, result.goals_a, result.goals_b)
            normalized = MatchResult(result.played_on, team_a, team_b, result.goals_a, result.goals_b, result.match_type)
        else:
            key = (result.played_on, team_b, team_a, result.goals_b, result.goals_a)
            normalized = MatchResult(result.played_on, team_a, team_b, result.goals_a, result.goals_b, result.match_type)
        previous = unique.get(key)
        if previous is None or _type_weight(normalized.match_type) > _type_weight(previous.match_type):
            unique[key] = normalized
    return sorted(unique.values(), key=lambda row: (row.played_on, row.team_a, row.team_b))


def _recency_weight(played_on: date, as_of: date, half_life_days: float = 450.0) -> float:
    age_days = max(0, (as_of - played_on).days)
    return 0.5 ** (age_days / half_life_days)


def _type_weight(match_type: str) -> float:
    if match_type == "world_cup":
        return 1.35
    if match_type == "friendly":
        return 0.45
    return 1.0


_OPPONENT_CLAMP_LOW = 0.60
_OPPONENT_CLAMP_HIGH = 1.60


def _opponent_clamp(value: float) -> float:
    return max(_OPPONENT_CLAMP_LOW, min(_OPPONENT_CLAMP_HIGH, value))


def build_team_ratings(
    results: list[MatchResult], as_of: date, iterations: int = 3
) -> dict[str, TeamRating]:
    """Goal-rate ratings with iterative strength-of-schedule normalization.

    Pass 1 uses raw goals (legacy behaviour, reachable with iterations=1).
    Later passes re-weigh every match by the opponent ratings of the
    previous pass: goals against a leaky defense earn less attack, clean
    sheets against toothless attacks earn less defense credit.
    """
    eligible = [
        result for result in deduplicate_results(results)
        if result.played_on < as_of
    ]
    ratings: dict[str, TeamRating] = {}
    for iteration in range(max(1, iterations)):
        totals: dict[str, dict[str, float]] = {}

        def _accumulate(team: str, opponent: str, gf: int, ga: int, weight: float) -> None:
            slot = totals.setdefault(
                team, {"gf": 0.0, "ga": 0.0, "w_att": 0.0, "w_def": 0.0, "w": 0.0}
            )
            if iteration == 0 or opponent not in ratings:
                opp_defense = opp_attack = 1.0
            else:
                opp_defense = _opponent_clamp(ratings[opponent].defense)
                opp_attack = _opponent_clamp(ratings[opponent].attack)
            # Scoring against a leaky defense (defense > 1) is discounted;
            # conceding against a strong attack is excusable.
            slot["gf"] += (gf / opp_defense) * weight * _opponent_clamp(1.0 / opp_defense)
            slot["ga"] += (ga / opp_attack) * weight * _opponent_clamp(opp_attack)
            slot["w_att"] += weight * _opponent_clamp(1.0 / opp_defense)
            slot["w_def"] += weight * _opponent_clamp(opp_attack)
            slot["w"] += weight

        for result in eligible:
            weight = _recency_weight(result.played_on, as_of) * _type_weight(result.match_type)
            _accumulate(result.team_a, result.team_b, result.goals_a, result.goals_b, weight)
            _accumulate(result.team_b, result.team_a, result.goals_b, result.goals_a, weight)

        all_gf = sum(team["gf"] for team in totals.values())
        all_w_att = sum(team["w_att"] for team in totals.values())
        all_ga = sum(team["ga"] for team in totals.values())
        all_w_def = sum(team["w_def"] for team in totals.values())
        average_scored = all_gf / all_w_att if all_w_att else 1.25
        average_conceded = all_ga / all_w_def if all_w_def else 1.25

        ratings = {}
        for team, values in totals.items():
            sample = values["w"]
            scored_rate = values["gf"] / values["w_att"] if values["w_att"] else average_scored
            conceded_rate = values["ga"] / values["w_def"] if values["w_def"] else average_conceded
            # Shrink uses the opponent-weighted sample: a clean sheet against
            # a toothless attack contributes little defensive certainty, so the
            # rating stays closer to average.
            shrink_att = min(1.0, values["w_att"] / 8.0)
            shrink_def = min(1.0, values["w_def"] / 8.0)
            attack = max(0.35, 1.0 + shrink_att * ((scored_rate / average_scored) - 1.0))
            defense = max(0.35, 1.0 + shrink_def * ((conceded_rate / average_conceded) - 1.0))
            ratings[team] = TeamRating(
                attack=attack,
                defense=defense,
                sample_weight=sample,
            )
    return ratings


def explain_team_form(
    team: str,
    results: list[MatchResult],
    as_of: date,
) -> list[FormContribution]:
    team_key = canonical_team_name(team)
    eligible = [row for row in deduplicate_results(results) if row.played_on < as_of]
    ratings = build_team_ratings(eligible, as_of)
    ledger = []
    for result in sorted(eligible, key=lambda row: row.played_on):
        if team_key == result.team_a:
            opponent, goals_for, goals_against = result.team_b, result.goals_a, result.goals_b
        elif team_key == result.team_b:
            opponent, goals_for, goals_against = result.team_a, result.goals_b, result.goals_a
        else:
            continue
        recency = _recency_weight(result.played_on, as_of)
        kind = _type_weight(result.match_type)
        opponent_rating = ratings.get(opponent, TeamRating(1.0, 1.0, 0.0))
        opponent_factor = max(0.90, min(1.10, opponent_rating.attack / opponent_rating.defense))
        total_weight = recency * kind * opponent_factor
        goal_difference = goals_for - goals_against
        outcome = "victoria" if goal_difference > 0 else "empate" if goal_difference == 0 else "derrota"
        age = (as_of - result.played_on).days
        explanation = (
            f"{outcome.capitalize()} {goals_for}-{goals_against} ante {opponent}; "
            f"hace {age} días, peso {result.match_type} {kind:.2f} y ajuste rival {opponent_factor:.2f}."
        )
        ledger.append(
            FormContribution(
                result.played_on, opponent, goals_for, goals_against, result.match_type,
                recency, kind, opponent_factor, total_weight,
                goal_difference * total_weight, explanation,
            )
        )
    return ledger


def expected_goals_for_match(
    team_a: str,
    team_b: str,
    ratings: dict[str, TeamRating],
    base_goals_per_team: float = 1.25,
    location_factor_a: float = 1.0,
) -> tuple[float, float]:
    team_a_key = canonical_team_name(team_a)
    team_b_key = canonical_team_name(team_b)
    rating_a = ratings.get(team_a_key, TeamRating(1.0, 1.0, 0.0))
    rating_b = ratings.get(team_b_key, TeamRating(1.0, 1.0, 0.0))
    xg_a = base_goals_per_team * rating_a.attack * rating_b.defense * location_factor_a
    xg_b = base_goals_per_team * rating_b.attack * rating_a.defense
    return max(0.05, xg_a), max(0.05, xg_b)
