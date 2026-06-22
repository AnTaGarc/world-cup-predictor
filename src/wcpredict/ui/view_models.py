from wcpredict.odds import OddsComparison
from wcpredict.services import MarketPrediction
from wcpredict.model_registry import POLICIES
from datetime import datetime, timezone
from wcpredict.ui.translations import (
    localize_confidence,
    localize_market,
    localize_model,
    localize_origin,
    localize_selection,
)


def prediction_rows(predictions: list[MarketPrediction]) -> list[dict]:
    return [
        {
            "Market": localize_market(prediction.market_name),
            "Selection": localize_selection(prediction.selection_name),
            "Line": prediction.line,
            "Probability": round(prediction.probability, 4),
            "Low": round(prediction.low_probability, 4),
            "High": round(prediction.high_probability, 4),
            "Confidence": localize_confidence(prediction.confidence.value),
            "Sample": round(prediction.sample_size, 2),
            "Origin": localize_origin(prediction.data_origin),
            "Explanation": prediction.explanation,
        }
        for prediction in predictions
    ]


def probability_chart_rows(
    predictions: list[MarketPrediction],
    market_name: str,
) -> list[dict]:
    return [
        {
            "Seleccion": localize_selection(row.selection_name),
            "Probabilidad": row.probability,
            "Minimo": row.low_probability,
            "Maximo": row.high_probability,
            "Etiqueta": f"{row.probability:.1%}",
            "Confianza": localize_confidence(row.confidence.value),
        }
        for row in predictions
        if row.market_name == market_name
    ]


def ml_probability_rows(team_a: str, team_b: str, probabilities: dict[str, float]) -> list[dict]:
    return [
        {"Resultado": team_a, "Probabilidad (%)": probabilities["home"] * 100.0},
        {"Resultado": "Empate", "Probabilidad (%)": probabilities["draw"] * 100.0},
        {"Resultado": team_b, "Probabilidad (%)": probabilities["away"] * 100.0},
    ]


def coverage_summary(
    *,
    collector_statistics: int,
    imported_lineups: int,
    daily_players: int,
    sources: int,
    deep_statistics: int,
) -> dict[str, int | str]:
    return {
        "Estadísticas disponibles": collector_statistics,
        "Jugadores disponibles": daily_players,
        "Alineación": "Confirmada" if imported_lineups else "No confirmada",
        "Fuentes": sources,
        "Estadísticas profundas": deep_statistics,
    }


def model_comparison_rows(
    team_a: str,
    team_b: str,
    score_probabilities: dict[str, float],
    ml_probabilities: dict[str, float],
    unified_probabilities: dict[str, float] | None = None,
) -> list[dict]:
    labels = (("home", team_a), ("draw", "Empate"), ("away", team_b))

    def normalized(values: dict[str, float]) -> dict[str, float]:
        total = sum(max(0.0, float(values.get(key, 0.0))) for key, _ in labels) or 1.0
        return {key: max(0.0, float(values.get(key, 0.0))) * 100.0 / total for key, _ in labels}

    score = normalized(score_probabilities)
    ml = normalized(ml_probabilities)
    unified = normalized(unified_probabilities or score_probabilities)
    return [
        {
            "Resultado": label,
            "Modelo unificado 1X2 (%)": unified[key],
            "ML cronológico (%)": ml[key],
            "Matriz de marcadores (%)": score[key],
            "Diferencia (pp)": round(ml[key] - score[key], 1),
        }
        for key, label in labels
    ]


def model_disagreement_note(rows: list[dict]) -> str:
    diagnostic_gap = max((abs(float(row["Diferencia (pp)"])) for row in rows), default=0.0)
    return (
        f"La mayor diferencia diagnostica es de {diagnostic_gap:.1f} puntos. El modelo unificado es el modelo operativo: "
        "combina ML cronologico, matriz de marcadores, forma profunda, fuerza rival, localia y jugadores cuando hay datos. "
        "La matriz de marcadores y el ML cronologico se muestran solo para explicar por que pueden discrepar."
    )
    maximum = max((abs(float(row["Diferencia (pp)"])) for row in rows), default=0.0)
    return (
        f"La mayor diferencia diagnostica es de {maximum:.1f} puntos. El modelo unificado es el modelo operativo: "
        "y combina goles esperados, forma profunda y jugadores; el ML cronológico es un contraste "
        "basado en Elo, resultados y forma reciente. No usan las mismas señales y todavía no se promedian."
    )


def postmatch_queue_message(
    *, pending_scores: int, with_imported_statistics: int, missing_statistics: int
) -> str:
    message = f"{pending_scores} partidos necesitan marcador final para cerrar la calibración."
    if with_imported_statistics:
        message += (
            f" {with_imported_statistics} ya tienen estadísticas importadas: se usan en la forma de "
            "partidos posteriores y no hay que introducirlas otra vez."
        )
    if missing_statistics:
        message += f" {missing_statistics} además tienen estadísticas de equipo incompletas."
    return message


def ev_rows(comparisons: list[OddsComparison]) -> list[dict]:
    return [
        {
            "Mercado": localize_market(comparison.market_name),
            "Selección": localize_selection(comparison.selection_name),
            "Probabilidad del modelo": round(comparison.probability, 4),
            "Cuota": comparison.decimal_odds,
            "Probabilidad implícita": round(comparison.implied_probability, 4),
            "Cuota justa": round(comparison.fair_odds, 3),
            "EV": round(comparison.expected_value, 4),
            "Confianza": localize_confidence(comparison.confidence),
        }
        for comparison in comparisons
    ]


def dataset_freshness_rows(
    snapshots: list[dict], checks: list[dict], now: datetime | None = None
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    latest_snapshots = {}
    for row in snapshots:
        latest_snapshots.setdefault(str(row["provider_id"]), row)
    latest_checks = {}
    for row in checks:
        latest_checks.setdefault(str(row["provider_id"]), row)
    providers = sorted(set(latest_snapshots) | set(latest_checks))
    output = []
    for provider in providers:
        snapshot = latest_snapshots.get(provider)
        check = latest_checks.get(provider)
        checked_raw = (check or snapshot or {}).get("checked_at_utc")
        checked = datetime.fromisoformat(str(checked_raw).replace("Z", "+00:00")) if checked_raw else None
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if check and check.get("status") == "failed":
            state = "Sin conexión" if snapshot is None else "Obsoleto (falló revisión)"
        elif snapshot is None:
            state = "Sin datos"
        elif checked is None or (now - checked).total_seconds() > 36 * 3600:
            state = "Obsoleto"
        else:
            state = "Actual"
        output.append(
            {
                "Proveedor": provider,
                "Estado": state,
                "Filas": int(snapshot.get("row_count") or 0) if snapshot else 0,
                "Datos actualizados": snapshot.get("data_updated_at_utc") if snapshot else None,
                "Última revisión": checked_raw,
                "Detalle": (check or {}).get("error_message") or "",
            }
        )
    return output


def model_policy_rows() -> list[dict]:
    seen = set()
    rows = []
    for policy in POLICIES.values():
        if policy.market in seen:
            continue
        seen.add(policy.market)
        rows.append(
            {
                "Mercado": policy.market,
                "Activo": localize_model(policy.active),
                "Challenger": localize_model(policy.challenger),
                "Fallback": localize_model(policy.fallback),
                "Validación": policy.validation_metric,
                "Nota": policy.note,
            }
        )
    return rows
