"""Post-match audit: compare what the model predicted with what actually happened.

This module is pure: it takes already-fetched data (final score, predictions,
team stats, evaluated backtests) and returns rows ready to render. The UI layer
only formats the output and applies colours.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditRow:
    label: str
    predicted: str
    actual: str
    delta: float
    delta_label: str
    severity: str  # "good" | "ok" | "warn" | "bad"


def _severity(delta: float, thresholds: tuple[float, float]) -> str:
    abs_delta = abs(delta)
    if abs_delta <= thresholds[0]:
        return "good"
    if abs_delta <= thresholds[1]:
        return "ok"
    if abs_delta <= thresholds[1] * 2:
        return "warn"
    return "bad"


def _format_score(goals_a: int, goals_b: int) -> str:
    return f"{int(goals_a)}-{int(goals_b)}"


def build_match_audit(
    *,
    team_a: str,
    team_b: str,
    goals_a: int,
    goals_b: int,
    primary_1x2: dict[str, float],
    mode_score: tuple[int, int] | None,
    expected_score: tuple[float, float] | None,
    team_a_stats: dict[str, float | None] | None,
    team_b_stats: dict[str, float | None] | None,
    predicted_volume: dict[str, float] | None = None,
    brier_average: float | None = None,
    evaluations: int = 0,
) -> dict[str, list[AuditRow] | float | str | None]:
    """Return audit rows grouped by section, ready to be rendered as a coloured table.

    `primary_1x2` keys: "home", "draw", "away" with probability mass (sums to 1).
    `predicted_volume` keys are metric names ("corners", "cards", "shots",
    "shots_on_target", "possession") with the model's expected TOTAL for the match.
    Missing values become "—" rows with severity "warn".
    """
    actual_outcome = "home" if goals_a > goals_b else "away" if goals_b > goals_a else "draw"
    actual_label = team_a if actual_outcome == "home" else team_b if actual_outcome == "away" else "Empate"
    predicted_label_pairs = (("home", team_a), ("draw", "Empate"), ("away", team_b))
    best_key, best_value = max(
        ((key, primary_1x2.get(key, 0.0)) for key, _ in predicted_label_pairs),
        key=lambda item: item[1],
    )
    predicted_label = next(label for key, label in predicted_label_pairs if key == best_key)
    outcome_delta = primary_1x2.get(actual_outcome, 0.0) - best_value
    outcome_row = AuditRow(
        "Resultado (1X2)",
        f"{predicted_label} ({best_value:.0%})",
        f"{actual_label} ({primary_1x2.get(actual_outcome, 0.0):.0%} en el modelo)",
        outcome_delta,
        f"{outcome_delta:+.0%}",
        "good" if best_key == actual_outcome else "bad",
    )

    score_rows: list[AuditRow] = []
    actual_score = _format_score(goals_a, goals_b)
    if mode_score is not None:
        mode_label = _format_score(mode_score[0], mode_score[1])
        diff = abs(mode_score[0] - goals_a) + abs(mode_score[1] - goals_b)
        score_rows.append(
            AuditRow(
                "Marcador (modo)", mode_label, actual_score,
                float(diff), f"|Δ| {diff} goles",
                "good" if diff == 0 else "ok" if diff <= 1 else "warn" if diff <= 2 else "bad",
            )
        )
    if expected_score is not None:
        expected_label = f"{expected_score[0]:.2f}-{expected_score[1]:.2f}"
        signed_total_delta = (goals_a + goals_b) - (expected_score[0] + expected_score[1])
        score_rows.append(
            AuditRow(
                "Goles esperados (xG)", expected_label, actual_score,
                signed_total_delta, f"{signed_total_delta:+.2f} goles vs esperado",
                _severity(signed_total_delta, (0.6, 1.2)),
            )
        )

    volume_labels = {
        "corners": "Córners",
        "cards": "Tarjetas",
        "shots": "Tiros",
        "shots_on_target": "Tiros a puerta",
        "possession": "Posesión",
    }
    volume_thresholds = {
        "corners": (1.5, 3.0),
        "cards": (1.0, 2.0),
        "shots": (3.0, 6.0),
        "shots_on_target": (1.5, 3.0),
        "possession": (5.0, 10.0),
    }
    volume_rows: list[AuditRow] = []
    if predicted_volume:
        team_a_stats = team_a_stats or {}
        team_b_stats = team_b_stats or {}
        for metric, label in volume_labels.items():
            if metric not in predicted_volume:
                continue
            predicted_total = float(predicted_volume[metric])
            observed_a = team_a_stats.get(metric)
            observed_b = team_b_stats.get(metric)
            if metric == "possession":
                if observed_a is None or observed_b is None:
                    continue
                observed = (float(observed_a) + float(observed_b)) / 2.0
            else:
                if observed_a is None and observed_b is None:
                    continue
                observed = float(observed_a or 0.0) + float(observed_b or 0.0)
            delta = observed - predicted_total
            volume_rows.append(
                AuditRow(
                    label,
                    f"{predicted_total:.1f}",
                    f"{observed:.1f}",
                    delta,
                    f"{delta:+.1f}",
                    _severity(delta, volume_thresholds[metric]),
                )
            )

    return {
        "outcome": [outcome_row],
        "score": score_rows,
        "volume": volume_rows,
        "brier_average": brier_average,
        "evaluations": evaluations,
        "actual_score": actual_score,
        "actual_outcome": actual_outcome,
    }


SEVERITY_COLORS = {
    "good": "#1ea672",
    "ok": "#3a8dde",
    "warn": "#e5a23a",
    "bad": "#d95b4f",
}


_PER_TEAM_THRESHOLDS = {
    "xg": (0.3, 0.7),
    "goals": (0.5, 1.0),
    "shots": (2.0, 4.0),
    "shots_on_target": (1.0, 2.0),
    "corners": (1.0, 2.0),
    "cards": (0.7, 1.5),
    "possession": (5.0, 10.0),
}

_PER_TEAM_LABELS = {
    "xg": "xG",
    "goals": "Goles",
    "shots": "Tiros",
    "shots_on_target": "Tiros a puerta",
    "corners": "Córners",
    "cards": "Tarjetas",
    "possession": "Posesión %",
}


def build_per_team_audit(
    *,
    team_a: str,
    team_b: str,
    goals_a: int,
    goals_b: int,
    expected_xg: tuple[float, float] | tuple,
    team_volume_predictions: dict[str, dict[str, float]],
    team_a_stats: dict[str, float | None] | None,
    team_b_stats: dict[str, float | None] | None,
) -> list[dict]:
    """Per-team predicted-vs-actual comparison using deep stats from team_match_stats.

    Each row covers one metric and shows the prediction for each team alongside
    the observed value, plus a per-team delta with severity. This is what makes
    the audit useful when deep stats have been imported.
    """
    team_a_stats = team_a_stats or {}
    team_b_stats = team_b_stats or {}
    rows: list[dict] = []

    def append_row(metric: str, predicted_a, predicted_b, actual_a, actual_b):
        if predicted_a is None and predicted_b is None and actual_a is None and actual_b is None:
            return
        thresholds = _PER_TEAM_THRESHOLDS.get(metric, (1.0, 2.0))

        def cell(predicted, actual):
            if predicted is None and actual is None:
                return ("—", "—", "ok", 0.0)
            predicted_label = f"{predicted:.2f}" if isinstance(predicted, float) else (str(predicted) if predicted is not None else "—")
            actual_label = f"{actual:.2f}" if isinstance(actual, float) else (str(actual) if actual is not None else "—")
            if predicted is None or actual is None:
                return (predicted_label, actual_label, "warn", 0.0)
            delta = float(actual) - float(predicted)
            severity = _severity(delta, thresholds)
            return (predicted_label, actual_label, severity, delta)

        pa_label, aa_label, severity_a, delta_a = cell(predicted_a, actual_a)
        pb_label, ab_label, severity_b, delta_b = cell(predicted_b, actual_b)
        rows.append({
            "metric": metric,
            "label": _PER_TEAM_LABELS.get(metric, metric),
            "team_a": {
                "predicted": pa_label, "actual": aa_label,
                "severity": severity_a, "delta": delta_a,
                "delta_label": f"{delta_a:+.2f}" if isinstance(delta_a, float) and pa_label != "—" and aa_label != "—" else "—",
            },
            "team_b": {
                "predicted": pb_label, "actual": ab_label,
                "severity": severity_b, "delta": delta_b,
                "delta_label": f"{delta_b:+.2f}" if isinstance(delta_b, float) and pb_label != "—" and ab_label != "—" else "—",
            },
        })

    expected_xg_tuple = tuple(expected_xg) if expected_xg else ()
    pred_xg_a = expected_xg_tuple[0] if len(expected_xg_tuple) == 2 else None
    pred_xg_b = expected_xg_tuple[1] if len(expected_xg_tuple) == 2 else None
    append_row(
        "xg", pred_xg_a, pred_xg_b,
        team_a_stats.get("xg"), team_b_stats.get("xg"),
    )
    # Goals: predicted = expected_xg (same point estimate) vs actual integer goals.
    append_row(
        "goals", pred_xg_a, pred_xg_b,
        float(goals_a), float(goals_b),
    )

    for metric in ("shots", "shots_on_target", "corners"):
        pred = team_volume_predictions.get(metric, {})
        append_row(
            metric,
            pred.get(team_a), pred.get(team_b),
            team_a_stats.get(metric), team_b_stats.get(metric),
        )

    cards_pred = team_volume_predictions.get("cards", {})
    cards_a_actual = None
    if team_a_stats.get("yellow_cards") is not None or team_a_stats.get("red_cards") is not None:
        cards_a_actual = float(team_a_stats.get("yellow_cards") or 0) + float(team_a_stats.get("red_cards") or 0)
    cards_b_actual = None
    if team_b_stats.get("yellow_cards") is not None or team_b_stats.get("red_cards") is not None:
        cards_b_actual = float(team_b_stats.get("yellow_cards") or 0) + float(team_b_stats.get("red_cards") or 0)
    append_row(
        "cards", cards_pred.get(team_a), cards_pred.get(team_b),
        cards_a_actual, cards_b_actual,
    )

    if team_a_stats.get("possession") is not None and team_b_stats.get("possession") is not None:
        # Predicted possession is 50/50 by default; if we ever produce a
        # possession estimate we can plug it in here.
        append_row(
            "possession", 50.0, 50.0,
            float(team_a_stats["possession"]), float(team_b_stats["possession"]),
        )

    return rows


def audit_rows_to_records(rows: list[AuditRow]) -> list[dict]:
    return [
        {
            "Métrica": row.label,
            "Predicho": row.predicted,
            "Real": row.actual,
            "Δ": row.delta_label,
            "_severity": row.severity,
        }
        for row in rows
    ]
