"""Probability of advancing in a knockout tie.

Pipeline given xG estimates for the 90 minutes:

    1. Regulation matrix         → P(home_win_90), P(draw_90), P(away_win_90)
    2. Extra-time matrix         → P(home_ET), P(draw_ET), P(away_ET)
       (xG scaled by 30/90 = 1/3, ratings unchanged)
    3. Penalty shoot-out         → ~50/50 by default, tilted by a goalkeeper
                                   strength differential when available

    P(home advances) = P(home_win_90)
                      + P(draw_90) · [ P(home_ET)
                                       + P(draw_ET) · P(home_wins_penalties) ]

The function returns both the overall advance probabilities and the
method-of-victory breakdown so the UI can show "Avanza en 90' / ET /
penaltis" as separate markets.
"""
from __future__ import annotations

from dataclasses import dataclass

from wcpredict.poisson import score_matrix_negative_binomial, summarize_score_matrix


# 30/90 = 1/3 of regulation. We use slightly less (0.30) because xG rate
# typically drops a touch in extra-time due to fatigue / cautious play.
EXTRA_TIME_FRACTION = 0.30
# Default symmetric penalty win probability when no GK signal is available.
PENALTY_BASE = 0.50
# How strongly a 1-unit GK rating gap moves penalty win prob (cap at ±0.10).
PENALTY_GK_SENSITIVITY = 0.05
PENALTY_GK_MAX_SHIFT = 0.10


@dataclass(frozen=True)
class KnockoutPrediction:
    # Probabilities of advancing from this tie.
    home_advances: float
    away_advances: float
    # Method breakdown (sum to 1.0 across the six outcomes).
    home_wins_90: float
    away_wins_90: float
    home_wins_et: float           # 0-0/1-1/etc at 90', resolved in ET
    away_wins_et: float
    home_wins_penalties: float    # decided on shoot-out
    away_wins_penalties: float
    # Intermediate diagnostics.
    p_draw_90: float
    p_draw_after_et: float


def predict_knockout_match(
    team_a_xg: float,
    team_b_xg: float,
    *,
    dispersion: float = 0.0,
    rho: float = 0.0,
    home_gk_rating: float | None = None,
    away_gk_rating: float | None = None,
) -> KnockoutPrediction:
    """Compute advance probabilities for a single knockout tie.

    `team_a_xg` and `team_b_xg` are the regulation-time xG already adjusted
    for opponent, host factor, and player corrections — same numbers the
    group-stage pipeline feeds to `score_matrix_negative_binomial`.
    """
    matrix_90 = score_matrix_negative_binomial(
        team_a_xg, team_b_xg, dispersion=dispersion, max_goals=10, rho=rho,
    )
    summary_90 = summarize_score_matrix(matrix_90, total_line=2.5)
    matrix_et = score_matrix_negative_binomial(
        team_a_xg * EXTRA_TIME_FRACTION,
        team_b_xg * EXTRA_TIME_FRACTION,
        dispersion=dispersion, max_goals=8, rho=rho,
    )
    summary_et = summarize_score_matrix(matrix_et, total_line=0.5)

    p_home_penalty = _penalty_win_probability(home_gk_rating, away_gk_rating)
    p_away_penalty = 1.0 - p_home_penalty

    home_wins_90 = summary_90.team_a_win
    away_wins_90 = summary_90.team_b_win
    p_draw_90 = summary_90.draw
    home_wins_et = p_draw_90 * summary_et.team_a_win
    away_wins_et = p_draw_90 * summary_et.team_b_win
    p_draw_after_et = p_draw_90 * summary_et.draw
    home_wins_pen = p_draw_after_et * p_home_penalty
    away_wins_pen = p_draw_after_et * p_away_penalty

    return KnockoutPrediction(
        home_advances=home_wins_90 + home_wins_et + home_wins_pen,
        away_advances=away_wins_90 + away_wins_et + away_wins_pen,
        home_wins_90=home_wins_90,
        away_wins_90=away_wins_90,
        home_wins_et=home_wins_et,
        away_wins_et=away_wins_et,
        home_wins_penalties=home_wins_pen,
        away_wins_penalties=away_wins_pen,
        p_draw_90=p_draw_90,
        p_draw_after_et=p_draw_after_et,
    )


def _penalty_win_probability(
    home_gk: float | None, away_gk: float | None
) -> float:
    """Symmetric 50/50 baseline, tilted by goalkeeper save-rate differential.

    `home_gk`/`away_gk` are expected to be the save-percentage estimates
    we already store for each starting keeper (0-1 scale). Difference
    multiplied by `PENALTY_GK_SENSITIVITY` and capped at ±10%.
    """
    if home_gk is None or away_gk is None:
        return PENALTY_BASE
    delta = float(home_gk) - float(away_gk)
    shift = max(-PENALTY_GK_MAX_SHIFT,
                min(PENALTY_GK_MAX_SHIFT, delta * PENALTY_GK_SENSITIVITY * 10))
    return max(0.05, min(0.95, PENALTY_BASE + shift))


def advance_market_rows(
    team_a: str, team_b: str, prediction: KnockoutPrediction
) -> list[dict]:
    """Render the knockout-specific market list (used by the UI)."""
    return [
        {"market": "To Advance", "selection": team_a, "probability": prediction.home_advances},
        {"market": "To Advance", "selection": team_b, "probability": prediction.away_advances},
        {"market": "Method", "selection": f"{team_a} en 90'", "probability": prediction.home_wins_90},
        {"market": "Method", "selection": f"{team_b} en 90'", "probability": prediction.away_wins_90},
        {"market": "Method", "selection": f"{team_a} en prórroga", "probability": prediction.home_wins_et},
        {"market": "Method", "selection": f"{team_b} en prórroga", "probability": prediction.away_wins_et},
        {"market": "Method", "selection": f"{team_a} en penaltis", "probability": prediction.home_wins_penalties},
        {"market": "Method", "selection": f"{team_b} en penaltis", "probability": prediction.away_wins_penalties},
    ]
