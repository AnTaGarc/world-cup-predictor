from dataclasses import dataclass
from datetime import date
import math

from wcpredict.advanced_form import XgFormAdjustment
from wcpredict.model_corrections import ModelCorrections, apply_outcome_shifts, is_active
from wcpredict.models import MarketFamily
from wcpredict.names import canonical_team_name
from wcpredict.player_impact import adjust_expected_goals, build_team_player_adjustment
from wcpredict.poisson import (
    ExactScore,
    expected_score,
    most_probable_score,
    score_matrix,
    score_matrix_negative_binomial,
    summarize_score_matrix,
    top_n_scores,
)
from wcpredict.quality import Confidence, calibrate_confidence
from wcpredict.ratings import MatchResult, build_team_ratings, expected_goals_for_match


# Tournament-calibrated defaults. The base xG per side was 1.25 (under-shoots the
# observed ~1.35-1.45 group-stage average); Dixon-Coles rho < 0 lightly inflates
# the low-score draws relative to independent Poisson; NB dispersion gives a
# slightly fatter goal tail than pure Poisson, so 2-1/3-1/3-2 outcomes get the
# weight the data actually supports when xG is high.
DEFAULT_BASE_GOALS_PER_TEAM = 1.35
DEFAULT_DIXON_COLES_RHO = -0.10
DEFAULT_NB_DISPERSION = 0.08


@dataclass(frozen=True)
class MarketPrediction:
    market_family: MarketFamily
    market_name: str
    selection_name: str
    line: float | None
    probability: float
    confidence: Confidence
    explanation: str
    low_probability: float = 0.0
    high_probability: float = 1.0
    sample_size: float = 0.0
    data_origin: str = "baseline"


def _normalize_1x2(probabilities: dict[str, float]) -> dict[str, float]:
    values = {
        key: max(0.0, float(probabilities.get(key, 0.0)))
        for key in ("home", "draw", "away")
    }
    total = sum(values.values())
    if total <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {key: value / total for key, value in values.items()}


def _tilt_home_away(probabilities: dict[str, float], logit_delta: float) -> dict[str, float]:
    base = _normalize_1x2(probabilities)
    if abs(logit_delta) < 1e-12:
        return base
    return _normalize_1x2(
        {
            "home": base["home"] * math.exp(logit_delta),
            "draw": base["draw"],
            "away": base["away"] * math.exp(-logit_delta),
        }
    )


def _safe_ratio(numerator: float, denominator: float) -> float:
    return max(0.20, min(5.0, numerator / denominator if denominator else 1.0))


def _aligned_score_distribution(
    matrix: list[list[float]],
    source_1x2: dict[str, float],
    target_1x2: dict[str, float],
) -> tuple[list[tuple[float, int, int]], float]:
    multipliers = {
        key: target_1x2[key] / source_1x2[key] if source_1x2[key] > 0 else 0.0
        for key in ("home", "draw", "away")
    }
    adjusted: list[tuple[float, int, int]] = []
    total_mass = 0.0
    for a_goals, row in enumerate(matrix):
        for b_goals, probability in enumerate(row):
            bucket = "home" if a_goals > b_goals else "draw" if a_goals == b_goals else "away"
            value = probability * multipliers[bucket]
            adjusted.append((value, a_goals, b_goals))
            total_mass += value
    return adjusted, total_mass


def _most_probable_score_aligned_to_1x2(
    matrix: list[list[float]],
    source_1x2: dict[str, float],
    target_1x2: dict[str, float],
) -> ExactScore:
    adjusted, total_mass = _aligned_score_distribution(matrix, source_1x2, target_1x2)
    if total_mass <= 0:
        return most_probable_score(matrix)
    probability, a_goals, b_goals = max(adjusted)
    return ExactScore(a_goals, b_goals, probability / total_mass)


def _top_n_aligned_scores(
    matrix: list[list[float]],
    source_1x2: dict[str, float],
    target_1x2: dict[str, float],
    n: int = 4,
) -> list[ExactScore]:
    adjusted, total_mass = _aligned_score_distribution(matrix, source_1x2, target_1x2)
    if total_mass <= 0:
        return top_n_scores(matrix, n=n)
    adjusted.sort(reverse=True)
    return [
        ExactScore(a_goals, b_goals, value / total_mass)
        for value, a_goals, b_goals in adjusted[: max(1, n)]
    ]


def _aligned_expected_score(
    matrix: list[list[float]],
    source_1x2: dict[str, float],
    target_1x2: dict[str, float],
) -> tuple[float, float]:
    adjusted, total_mass = _aligned_score_distribution(matrix, source_1x2, target_1x2)
    if total_mass <= 0:
        return expected_score(matrix)
    expected_a = sum(value * a for value, a, _ in adjusted) / total_mass
    expected_b = sum(value * b for value, _, b in adjusted) / total_mass
    return expected_a, expected_b


def predict_match_markets(
    team_a: str,
    team_b: str,
    results: list[MatchResult],
    as_of: date,
    calibration_by_family: dict[str, dict] | None = None,
    player_context: list[dict] | None = None,
    advanced_form: XgFormAdjustment | None = None,
    outcome_probabilities: dict[str, float] | None = None,
    outcome_weight: float = 0.80,
    deep_outcome_probabilities: dict[str, float] | None = None,
    deep_outcome_weight: float = 0.40,
    host_factor_a: float = 1.0,
    host_factor_b: float = 1.0,
    corrections: ModelCorrections | None = None,
) -> list[MarketPrediction]:
    ratings = build_team_ratings(results, as_of=as_of)
    xg_a, xg_b = expected_goals_for_match(
        team_a, team_b, ratings, base_goals_per_team=DEFAULT_BASE_GOALS_PER_TEAM
    )

    player_note = ""
    if player_context is not None:
        adjustment_a = build_team_player_adjustment(player_context, team_a)
        adjustment_b = build_team_player_adjustment(player_context, team_b)
        xg_a, xg_b, player_note = adjust_expected_goals(xg_a, xg_b, adjustment_a, adjustment_b)

    advanced_note = ""
    if advanced_form is not None:
        xg_a = max(0.05, xg_a * advanced_form.factor_a)
        xg_b = max(0.05, xg_b * advanced_form.factor_b)
        advanced_note = advanced_form.explanation

    host_note = ""
    if host_factor_a != 1.0 or host_factor_b != 1.0:
        xg_a = max(0.05, xg_a * host_factor_a)
        xg_b = max(0.05, xg_b * host_factor_b)
        host_note = f"Ajuste de localía/sede: {team_a} x{host_factor_a:.2f}; {team_b} x{host_factor_b:.2f}."

    # Bias-derived xG correction (Bayesian shrinkage). Subtracted from both
    # teams' expected goals before the score matrix is built.
    correction_note = ""
    if corrections is not None and corrections.xg_shift != 0.0:
        xg_a = max(0.05, xg_a - corrections.xg_shift)
        xg_b = max(0.05, xg_b - corrections.xg_shift)
        correction_note = (
            f"Corrección de calibración (xG): −{corrections.xg_shift:+.2f} por equipo "
            f"sobre {corrections.sample_size} partidos auditados."
        )

    matrix = score_matrix_negative_binomial(
        xg_a, xg_b,
        dispersion=DEFAULT_NB_DISPERSION,
        max_goals=10,
        rho=DEFAULT_DIXON_COLES_RHO,
    )
    summary = summarize_score_matrix(matrix, total_line=2.5)
    exact_score = most_probable_score(matrix)
    score_1x2 = {
        "home": summary.team_a_win,
        "draw": summary.draw,
        "away": summary.team_b_win,
    }

    unified_1x2 = score_1x2
    unified_note = ""
    if outcome_probabilities is not None:
        ml_1x2 = _normalize_1x2(outcome_probabilities)
        process_delta = 0.0
        if advanced_form is not None:
            process_delta += 1.25 * math.log(_safe_ratio(advanced_form.factor_a, advanced_form.factor_b))
        if host_factor_a != 1.0 or host_factor_b != 1.0:
            host_ratio = max(0.50, min(2.0, host_factor_a / host_factor_b if host_factor_b else 1.0))
            process_delta += 1.50 * math.log(host_ratio)
        adjusted_ml_1x2 = _tilt_home_away(ml_1x2, process_delta)
        # If the deep-stats classifier produced probabilities, fold them into
        # the ML side via a weighted ensemble of the two classifiers. Caller
        # controls deep_outcome_weight (0 = ignore deep, 1 = use deep only).
        if deep_outcome_probabilities is not None:
            deep_1x2 = _normalize_1x2(deep_outcome_probabilities)
            dw = max(0.0, min(1.0, deep_outcome_weight))
            adjusted_ml_1x2 = _normalize_1x2({
                key: (1.0 - dw) * adjusted_ml_1x2[key] + dw * deep_1x2[key]
                for key in ("home", "draw", "away")
            })
        weight = max(0.0, min(1.0, outcome_weight))
        blended = {
            key: weight * adjusted_ml_1x2[key] + (1.0 - weight) * score_1x2[key]
            for key in ("home", "draw", "away")
        }
        unified_1x2 = _normalize_1x2(blended)
        unified_note = (
            f" Modelo unificado 1X2: ML cronológico {weight:.0%} + matriz de goles {1.0 - weight:.0%}; "
            "el ML se ajusta por proceso profundo y localía cuando hay evidencia; el marcador exacto se repondera "
            "para respetar el 1X2 unificado y los mercados de goles siguen usando la matriz."
        )
        if (
            corrections is not None
            and any(abs(value) > 0.005 for value in corrections.outcome_logit_shifts.values())
        ):
            unified_1x2 = apply_outcome_shifts(unified_1x2, corrections.outcome_logit_shifts)
            unified_note += (
                " Corrección de calibración 1X2 aplicada en log-prob "
                f"({corrections.sample_size} partidos)."
            )
        exact_score = _most_probable_score_aligned_to_1x2(matrix, score_1x2, unified_1x2)

    team_a_key = canonical_team_name(team_a)
    team_b_key = canonical_team_name(team_b)
    sample_size = min(
        ratings.get(team_a_key).sample_weight if team_a_key in ratings else 0.0,
        ratings.get(team_b_key).sample_weight if team_b_key in ratings else 0.0,
    )
    confidence = Confidence.HIGH if sample_size >= 8 else Confidence.MEDIUM if sample_size >= 3 else Confidence.LOW
    data_origin = "observed_form" if sample_size > 0 else "baseline"
    explanation = (
        f"Poisson con goles esperados {team_a} {xg_a:.2f} y {team_b} {xg_b:.2f}. "
        + (f"Forma ponderada equivalente a {sample_size:.1f} partidos." if sample_size else "Sin historial suficiente: se usa una base neutral.")
        + (f" {player_note}" if player_note else "")
        + (f" {advanced_note}" if advanced_note else "")
        + (f" {host_note}" if host_note else "")
        + (f" {correction_note}" if correction_note else "")
    )
    result_explanation = explanation + unified_note

    def prediction(family, market, selection, line, probability, row_explanation=None):
        row_confidence = confidence
        calibration = (calibration_by_family or {}).get(family.value)
        calibration_note = ""
        if calibration:
            row_confidence = calibrate_confidence(
                confidence, int(calibration["count"]), float(calibration["avg_brier"])
            )
            calibration_note = f" Calibración {calibration['count']} casos, Brier {calibration['avg_brier']:.3f}."
        row_width = 0.04 if row_confidence == Confidence.HIGH else 0.08 if row_confidence == Confidence.MEDIUM else 0.14
        data_origin_row = (
            "unified_model"
            if outcome_probabilities is not None and family in {MarketFamily.MATCH_RESULT, MarketFamily.DOUBLE_CHANCE, MarketFamily.DRAW_NO_BET}
            else "player_adjusted" if player_context else data_origin
        )
        return MarketPrediction(
            family, market, selection, line, probability, row_confidence,
            (row_explanation or explanation) + calibration_note,
            max(0.0, probability - row_width), min(1.0, probability + row_width), sample_size,
            data_origin_row,
        )

    rows = [
        prediction(MarketFamily.MATCH_RESULT, "1X2", team_a, None, unified_1x2["home"], result_explanation),
        prediction(MarketFamily.MATCH_RESULT, "1X2", "Draw", None, unified_1x2["draw"], result_explanation),
        prediction(MarketFamily.MATCH_RESULT, "1X2", team_b, None, unified_1x2["away"], result_explanation),
        prediction(MarketFamily.DOUBLE_CHANCE, "Double Chance", f"{team_a} or Draw", None, unified_1x2["home"] + unified_1x2["draw"], result_explanation),
        prediction(MarketFamily.DOUBLE_CHANCE, "Double Chance", f"{team_b} or Draw", None, unified_1x2["away"] + unified_1x2["draw"], result_explanation),
        prediction(MarketFamily.DRAW_NO_BET, "Draw No Bet", team_a, None, unified_1x2["home"] / (unified_1x2["home"] + unified_1x2["away"]), result_explanation),
        prediction(MarketFamily.DRAW_NO_BET, "Draw No Bet", team_b, None, unified_1x2["away"] / (unified_1x2["home"] + unified_1x2["away"]), result_explanation),
        prediction(
            MarketFamily.GOALS, "Exact Score",
            f"{exact_score.team_a_goals}-{exact_score.team_b_goals}", None,
            exact_score.probability,
        ),
    ]
    # Top alternative scorelines and expected score: provide context next to the
    # mode-based "Exact Score" so the UI can show 2-1/3-1 alternatives instead of
    # the single low-bias mode.
    if outcome_probabilities is not None:
        aligned_top = _top_n_aligned_scores(matrix, score_1x2, unified_1x2, n=4)
        expected_a, expected_b = _aligned_expected_score(matrix, score_1x2, unified_1x2)
    else:
        aligned_top = top_n_scores(matrix, n=4)
        expected_a, expected_b = expected_score(matrix)
    for rank, alt in enumerate(aligned_top[1:4], start=2):
        rows.append(
            prediction(
                MarketFamily.GOALS, "Exact Score (alt)",
                f"{alt.team_a_goals}-{alt.team_b_goals} (#{rank})",
                None, alt.probability,
                f"Marcador alternativo #{rank} dentro de la distribución conjunta.",
            )
        )
    rows.append(
        prediction(
            MarketFamily.GOALS, "Expected Score",
            f"{expected_a:.2f}-{expected_b:.2f}",
            None, 1.0,
            f"Goles esperados según la distribución conjunta: {team_a} {expected_a:.2f}, {team_b} {expected_b:.2f}.",
        )
    )
    for line in (1.5, 2.5, 3.5, 4.5):
        total = summarize_score_matrix(matrix, total_line=line)
        rows.extend(
            [
                prediction(MarketFamily.GOALS, f"Over/Under {line}", f"Over {line}", line, total.over_total),
                prediction(MarketFamily.GOALS, f"Over/Under {line}", f"Under {line}", line, total.under_total),
            ]
        )
    rows.extend(
        [
            prediction(MarketFamily.BTTS, "Both Teams To Score", "Yes", None, summary.both_teams_to_score),
            prediction(MarketFamily.BTTS, "Both Teams To Score", "No", None, 1.0 - summary.both_teams_to_score),
        ]
    )
    return rows
