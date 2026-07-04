"""Opponent-strength normalization for volume metrics (corners, cards,
fouls, shots, offsides...).

Same principle as the opponent-adjusted goal ratings: nine corners against
a side that concedes twice the tournament average are worth less than nine
against a side that concedes half of it. Each per-match created value is
scaled by ``tournament_mean / opponent_conceded_rate`` (clamped) before it
enters the team profile average.

``OPPONENT_NORMALIZED_METRICS`` holds the metrics where the A/B gate
(scripts/backtest_volume_normalization.py) demonstrated an improvement;
metrics outside the set keep the raw behaviour.
"""
from __future__ import annotations

from wcpredict.names import canonical_team_name


FACTOR_LOW = 0.60
FACTOR_HIGH = 1.60

# A/B gate results (2026-07-04, 88 closed matches, Brier on O/U lines):
#   shots_total    +0.59%  -> ACTIVE
#   fouls          +0.33%  (below the 0.5% threshold, stays raw)
#   yellow_cards   -0.10%, corners -0.33%, offsides -0.43%,
#   shots_on_target -1.47% -> stay raw.
# The existing lambda blend (own 45% + rival-conceded 30% + tournament 25%)
# already hedges most schedule bias for volume metrics, unlike goals.
OPPONENT_NORMALIZED_METRICS: frozenset[str] = frozenset({
    "resumen_del_partido.tiros_totales",
})


def build_conceded_rates(deep_rows: list[dict]) -> dict[tuple[str, str], float]:
    """Mean value of each metric that every team ALLOWS its rivals.

    For each (match, metric) pair with both teams present, team A's value is
    what team B conceded and vice versa. Simple unweighted mean: this is a
    normalizer, not a predictor, so recency refinements add noise faster
    than signal at tournament sample sizes.
    """
    by_match_metric: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for row in deep_rows:
        metric = str(row.get("metric") or "")
        value = row.get("value_number")
        kickoff = str(row.get("kickoff_utc") or "")
        team = str(row.get("team_name") or "")
        if not metric or value is None or not kickoff or not team:
            continue
        by_match_metric.setdefault((kickoff, metric), []).append((team, float(value)))

    sums: dict[tuple[str, str], list[float]] = {}
    for (kickoff, metric), pair in by_match_metric.items():
        if len(pair) < 2:
            continue
        for (team, value) in pair:
            for (other_team, other_value) in pair:
                if other_team == team:
                    continue
                conceder = canonical_team_name(other_team)
                sums.setdefault((conceder, metric), []).append(value)
                break
    return {
        key: sum(values) / len(values)
        for key, values in sums.items()
        if values
    }


def normalization_factor(
    conceded_rates: dict[tuple[str, str], float],
    tournament_mean: float,
    opponent_name: str | None,
    metric: str,
    normalized_metrics: frozenset[str],
) -> float:
    if metric not in normalized_metrics or not opponent_name or tournament_mean <= 0:
        return 1.0
    rate = conceded_rates.get((canonical_team_name(opponent_name), metric))
    if rate is None or rate <= 0:
        return 1.0
    return max(FACTOR_LOW, min(FACTOR_HIGH, tournament_mean / rate))
