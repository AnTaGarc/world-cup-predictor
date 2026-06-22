from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ScoreSummary:
    team_a_win: float
    draw: float
    team_b_win: float
    over_total: float
    under_total: float
    both_teams_to_score: float


@dataclass(frozen=True)
class ExactScore:
    team_a_goals: int
    team_b_goals: int
    probability: float


def poisson_probability(goals: int, expected_goals: float) -> float:
    if goals < 0:
        raise ValueError("goals must be non-negative")
    if expected_goals < 0:
        raise ValueError("expected_goals must be non-negative")
    return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)


def _dixon_coles_tau(
    a_goals: int, b_goals: int, xg_a: float, xg_b: float, rho: float
) -> float:
    """Dixon-Coles low-score correction.

    With rho < 0, the joint distribution slightly inflates the four lowest
    scorelines (0-0, 0-1, 1-0, 1-1) relative to independent Poisson, capturing
    the well-documented "draws are more frequent than independence predicts"
    effect in football. Returns 1.0 for cells where the correction does not
    apply.
    """
    if rho == 0.0:
        return 1.0
    if a_goals == 0 and b_goals == 0:
        return 1.0 - xg_a * xg_b * rho
    if a_goals == 0 and b_goals == 1:
        return 1.0 + xg_a * rho
    if a_goals == 1 and b_goals == 0:
        return 1.0 + xg_b * rho
    if a_goals == 1 and b_goals == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    team_a_xg: float,
    team_b_xg: float,
    max_goals: int = 10,
    rho: float = 0.0,
) -> list[list[float]]:
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    base = [
        [
            poisson_probability(a_goals, team_a_xg) * poisson_probability(b_goals, team_b_xg)
            for b_goals in range(max_goals + 1)
        ]
        for a_goals in range(max_goals + 1)
    ]
    if rho == 0.0:
        return base
    return [
        [
            max(0.0, base[a][b] * _dixon_coles_tau(a, b, team_a_xg, team_b_xg, rho))
            for b in range(max_goals + 1)
        ]
        for a in range(max_goals + 1)
    ]


def _negative_binomial_pmf(k: int, mean: float, dispersion: float) -> float:
    """NB parameterised by mean and dispersion (variance = mean + dispersion * mean^2).

    dispersion = 0 reduces exactly to Poisson(mean).
    """
    if k < 0:
        raise ValueError("k must be non-negative")
    if mean < 0:
        raise ValueError("mean must be non-negative")
    if dispersion <= 0:
        return poisson_probability(k, mean)
    r = 1.0 / dispersion
    p = r / (r + mean)
    # P(K=k) = gamma(k+r)/(k! gamma(r)) * p^r * (1-p)^k
    log_coeff = math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
    log_prob = log_coeff + r * math.log(p) + k * math.log(1.0 - p)
    return math.exp(log_prob)


def score_matrix_negative_binomial(
    team_a_xg: float,
    team_b_xg: float,
    dispersion: float = 0.0,
    max_goals: int = 10,
    rho: float = 0.0,
) -> list[list[float]]:
    """Bivariate-ish score matrix using Negative Binomial marginals.

    Negative Binomial has a fatter tail than Poisson (variance > mean), which
    redistributes mass from 0/1-goal cells toward 2+/3+ scorelines. The same
    Dixon-Coles low-score correction is applied when rho != 0.
    """
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    pa = [_negative_binomial_pmf(k, team_a_xg, dispersion) for k in range(max_goals + 1)]
    pb = [_negative_binomial_pmf(k, team_b_xg, dispersion) for k in range(max_goals + 1)]
    base = [[pa[a] * pb[b] for b in range(max_goals + 1)] for a in range(max_goals + 1)]
    if rho == 0.0:
        return base
    return [
        [
            max(0.0, base[a][b] * _dixon_coles_tau(a, b, team_a_xg, team_b_xg, rho))
            for b in range(max_goals + 1)
        ]
        for a in range(max_goals + 1)
    ]


def top_n_scores(matrix: list[list[float]], n: int = 3) -> list[ExactScore]:
    total_mass = sum(sum(row) for row in matrix)
    if total_mass <= 0:
        raise ValueError("score matrix has no probability mass")
    items = sorted(
        (
            (value / total_mass, a, b)
            for a, row in enumerate(matrix)
            for b, value in enumerate(row)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return [ExactScore(a, b, probability) for probability, a, b in items[: max(1, n)]]


def expected_score(matrix: list[list[float]]) -> tuple[float, float]:
    total_mass = sum(sum(row) for row in matrix)
    if total_mass <= 0:
        raise ValueError("score matrix has no probability mass")
    expected_a = sum(
        a * sum(row) / total_mass for a, row in enumerate(matrix)
    )
    width = len(matrix[0]) if matrix else 0
    expected_b = sum(
        b * sum(matrix[a][b] for a in range(len(matrix))) / total_mass
        for b in range(width)
    )
    return expected_a, expected_b


def summarize_score_matrix(matrix: list[list[float]], total_line: float = 2.5) -> ScoreSummary:
    team_a_win = 0.0
    draw = 0.0
    team_b_win = 0.0
    over_total = 0.0
    btts = 0.0
    total_mass = sum(sum(row) for row in matrix)
    for a_goals, row in enumerate(matrix):
        for b_goals, probability in enumerate(row):
            if a_goals > b_goals:
                team_a_win += probability
            elif a_goals == b_goals:
                draw += probability
            else:
                team_b_win += probability
            if a_goals + b_goals > total_line:
                over_total += probability
            if a_goals > 0 and b_goals > 0:
                btts += probability
    if total_mass <= 0:
        raise ValueError("score matrix has no probability mass")
    return ScoreSummary(
        team_a_win=team_a_win / total_mass,
        draw=draw / total_mass,
        team_b_win=team_b_win / total_mass,
        over_total=over_total / total_mass,
        under_total=1.0 - (over_total / total_mass),
        both_teams_to_score=btts / total_mass,
    )


def most_probable_score(matrix: list[list[float]]) -> ExactScore:
    total_mass = sum(sum(row) for row in matrix)
    if total_mass <= 0:
        raise ValueError("score matrix has no probability mass")
    probability, a_goals, b_goals = max(
        (value, a, b)
        for a, row in enumerate(matrix)
        for b, value in enumerate(row)
    )
    return ExactScore(a_goals, b_goals, probability / total_mass)
