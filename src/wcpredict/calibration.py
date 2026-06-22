"""Global model calibration.

Walks the database of finished matches with deep stats, reconstructs the
prediction the model would have produced before each kickoff (using only data
from prior matches), and compares it with what actually happened. The output
is a set of bias indicators that highlight where the model is systematically
off — the analyst can read these to decide whether to apply corrections.

This module is intentionally read-only: it does NOT mutate the model. Its job
is to *measure* systematic deviation. Any auto-correction layer lives elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean

from wcpredict.advanced_form import build_xg_form_adjustment
from wcpredict.names import canonical_team_name, same_team
from wcpredict.ratings import MatchResult
from wcpredict.services import predict_match_markets


@dataclass(frozen=True)
class CalibrationSample:
    match_id: int
    team_a: str
    team_b: str
    kickoff_utc: datetime
    predicted_1x2: dict  # {home, draw, away}
    actual_outcome: str  # "home" | "draw" | "away"
    predicted_xg_a: float | None
    predicted_xg_b: float | None
    actual_xg_a: float | None
    actual_xg_b: float | None
    predicted_total_goals: float | None
    actual_total_goals: int


@dataclass(frozen=True)
class BiasReport:
    sample_size: int
    # 1X2 bias: average predicted probability for each outcome vs frequency it occurred.
    home_predicted_avg: float
    home_actual_frequency: float
    draw_predicted_avg: float
    draw_actual_frequency: float
    away_predicted_avg: float
    away_actual_frequency: float
    # xG bias: signed residual (predicted - actual). Positive => model overshoots.
    xg_bias_per_team: float | None
    xg_mean_absolute_error: float | None
    # Total goals bias.
    total_goals_bias: float | None
    total_goals_mae: float | None
    # Outcome accuracy.
    outcome_accuracy: float
    favourites_calibration: dict  # {"high": (predicted_avg, actual_freq), ...}
    notes: list[str] = field(default_factory=list)


def _outcome_from_score(goals_a: int, goals_b: int) -> str:
    if goals_a > goals_b:
        return "home"
    if goals_a < goals_b:
        return "away"
    return "draw"


def _favourite_bucket(probability: float) -> str:
    if probability >= 0.55:
        return "fuerte (>=55%)"
    if probability >= 0.40:
        return "ligero (40-55%)"
    return "duda (<40%)"


def build_calibration_samples(
    *,
    finished_matches: list[dict],
    historical_results: list[MatchResult],
    deep_rows: list[dict],
    team_match_stats_by_match: dict[int, dict[str, dict]],
    match_results_by_match: dict[int, dict],
) -> list[CalibrationSample]:
    """For each finished match with team_match_stats, reconstruct a leave-one-out
    prediction using only matches that finished BEFORE its kickoff, and emit a
    CalibrationSample comparing model vs reality."""
    samples: list[CalibrationSample] = []
    chronological = sorted(
        finished_matches, key=lambda row: row["kickoff_utc"],
    )
    for row in chronological:
        match_id = int(row["id"])
        kickoff_utc = row["kickoff_utc"]
        if isinstance(kickoff_utc, str):
            kickoff_utc = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        result = match_results_by_match.get(match_id)
        if result is None:
            continue
        team_a = str(row["team_a"])
        team_b = str(row["team_b"])
        prior_results = [r for r in historical_results if r.played_on < kickoff_utc.date()]
        prior_deep = [r for r in deep_rows
                      if datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00")) < kickoff_utc]
        xg_form = build_xg_form_adjustment(team_a, team_b, prior_deep, kickoff_utc)
        predictions = predict_match_markets(
            team_a, team_b, prior_results, kickoff_utc.date(),
            advanced_form=xg_form,
        )
        try:
            pred_home = next(p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == team_a)
            pred_draw = next(p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == "Draw")
            pred_away = next(p.probability for p in predictions if p.market_name == "1X2" and p.selection_name == team_b)
        except StopIteration:
            continue
        expected_row = next((p for p in predictions if p.market_name == "Expected Score"), None)
        pred_xg_a = pred_xg_b = pred_total = None
        if expected_row is not None:
            try:
                pred_xg_a, pred_xg_b = (float(v) for v in expected_row.selection_name.split("-"))
                pred_total = pred_xg_a + pred_xg_b
            except (ValueError, AttributeError):
                pass
        stats = team_match_stats_by_match.get(match_id, {})
        actual_xg_a = stats.get(team_a, {}).get("xg")
        actual_xg_b = stats.get(team_b, {}).get("xg")
        goals_a = int(result["goals_a"])
        goals_b = int(result["goals_b"])
        samples.append(CalibrationSample(
            match_id=match_id,
            team_a=team_a, team_b=team_b, kickoff_utc=kickoff_utc,
            predicted_1x2={"home": pred_home, "draw": pred_draw, "away": pred_away},
            actual_outcome=_outcome_from_score(goals_a, goals_b),
            predicted_xg_a=pred_xg_a, predicted_xg_b=pred_xg_b,
            actual_xg_a=float(actual_xg_a) if actual_xg_a is not None else None,
            actual_xg_b=float(actual_xg_b) if actual_xg_b is not None else None,
            predicted_total_goals=pred_total,
            actual_total_goals=goals_a + goals_b,
        ))
    return samples


def summarise_bias(samples: list[CalibrationSample]) -> BiasReport:
    n = len(samples)
    if n == 0:
        return BiasReport(
            0, 0, 0, 0, 0, 0, 0, None, None, None, None, 0.0,
            favourites_calibration={}, notes=["Sin partidos cerrados con stats profundas."],
        )

    home_preds = [s.predicted_1x2["home"] for s in samples]
    draw_preds = [s.predicted_1x2["draw"] for s in samples]
    away_preds = [s.predicted_1x2["away"] for s in samples]
    home_actual = sum(1 for s in samples if s.actual_outcome == "home") / n
    draw_actual = sum(1 for s in samples if s.actual_outcome == "draw") / n
    away_actual = sum(1 for s in samples if s.actual_outcome == "away") / n

    # xG bias per team (each sample contributes 2 team observations).
    xg_residuals: list[float] = []
    for s in samples:
        if s.predicted_xg_a is not None and s.actual_xg_a is not None:
            xg_residuals.append(s.predicted_xg_a - s.actual_xg_a)
        if s.predicted_xg_b is not None and s.actual_xg_b is not None:
            xg_residuals.append(s.predicted_xg_b - s.actual_xg_b)
    xg_bias = mean(xg_residuals) if xg_residuals else None
    xg_mae = mean(abs(v) for v in xg_residuals) if xg_residuals else None

    # Total goals bias.
    total_residuals = [
        s.predicted_total_goals - s.actual_total_goals
        for s in samples if s.predicted_total_goals is not None
    ]
    total_bias = mean(total_residuals) if total_residuals else None
    total_mae = mean(abs(v) for v in total_residuals) if total_residuals else None

    # Outcome accuracy: model's argmax outcome equals actual.
    correct = 0
    for s in samples:
        argmax = max(s.predicted_1x2, key=s.predicted_1x2.get)
        if argmax == s.actual_outcome:
            correct += 1
    accuracy = correct / n

    # Favourites calibration: when the modelled-favourite probability is X,
    # does the favourite actually win at frequency X?
    fav_buckets: dict[str, list[tuple[float, int]]] = {
        "fuerte (>=55%)": [], "ligero (40-55%)": [], "duda (<40%)": [],
    }
    for s in samples:
        fav_outcome = max(s.predicted_1x2, key=s.predicted_1x2.get)
        prob = s.predicted_1x2[fav_outcome]
        fav_buckets[_favourite_bucket(prob)].append((prob, 1 if s.actual_outcome == fav_outcome else 0))
    favourites_calibration: dict = {}
    for label, items in fav_buckets.items():
        if items:
            avg_pred = mean(p for p, _ in items)
            actual_freq = mean(hit for _, hit in items)
            favourites_calibration[label] = {
                "n": len(items), "predicted": avg_pred, "actual": actual_freq,
            }

    notes: list[str] = []
    if xg_bias is not None and abs(xg_bias) >= 0.25:
        direction = "sobreestima" if xg_bias > 0 else "subestima"
        notes.append(
            f"El modelo {direction} el xG por equipo en {abs(xg_bias):.2f} goles "
            f"({xg_mae:.2f} de error medio absoluto en {len(xg_residuals)} observaciones)."
        )
    if total_bias is not None and abs(total_bias) >= 0.40:
        direction = "sobreestima" if total_bias > 0 else "subestima"
        notes.append(
            f"El modelo {direction} el total de goles en {abs(total_bias):.2f} goles/partido."
        )
    for outcome, pred_avg, actual in (
        ("home", mean(home_preds), home_actual),
        ("draw", mean(draw_preds), draw_actual),
        ("away", mean(away_preds), away_actual),
    ):
        gap = pred_avg - actual
        if abs(gap) >= 0.08:
            direction = "sobreestima" if gap > 0 else "subestima"
            label = {"home": "victoria local", "draw": "empate", "away": "victoria visitante"}[outcome]
            notes.append(
                f"{direction.capitalize()} la {label}: media {pred_avg:.0%} vs frecuencia real {actual:.0%} "
                f"(gap {gap:+.0%})."
            )

    return BiasReport(
        sample_size=n,
        home_predicted_avg=mean(home_preds),
        home_actual_frequency=home_actual,
        draw_predicted_avg=mean(draw_preds),
        draw_actual_frequency=draw_actual,
        away_predicted_avg=mean(away_preds),
        away_actual_frequency=away_actual,
        xg_bias_per_team=xg_bias,
        xg_mean_absolute_error=xg_mae,
        total_goals_bias=total_bias,
        total_goals_mae=total_mae,
        outcome_accuracy=accuracy,
        favourites_calibration=favourites_calibration,
        notes=notes,
    )
