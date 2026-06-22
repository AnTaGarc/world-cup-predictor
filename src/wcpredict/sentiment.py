from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CollectionGate:
    status: str
    detail: str


def x_collection_gate(api_key: str | None, budget_usd: float) -> CollectionGate:
    if not api_key:
        return CollectionGate("missing_credentials", "X_API_BEARER_TOKEN no está configurado")
    if budget_usd <= 0:
        return CollectionGate("zero_budget", "La recopilación de pago está desactivada")
    return CollectionGate("ready", "Credencial y presupuesto disponibles")


def normalize_sentiment_snapshot(
    *, match_id: int, provider_id: str, window_start_utc: datetime,
    window_end_utc: datetime, positive: int, neutral: int, negative: int,
    query: str, language: str, estimated_cost_usd: float = 0.0,
) -> dict:
    if window_end_utc <= window_start_utc:
        raise ValueError("La ventana de sentimiento debe tener duración positiva")
    counts = [int(positive), int(neutral), int(negative)]
    if any(value < 0 for value in counts):
        raise ValueError("Los recuentos no pueden ser negativos")
    sample_size = sum(counts)
    score = 0.0 if sample_size == 0 else (positive - negative) / sample_size
    return {
        "match_id": int(match_id), "provider_id": provider_id,
        "window_start_utc": window_start_utc.isoformat(),
        "window_end_utc": window_end_utc.isoformat(), "query": query,
        "language": language, "positive": positive, "neutral": neutral,
        "negative": negative, "sample_size": sample_size,
        "sentiment_score": float(score), "estimated_cost_usd": float(estimated_cost_usd),
        "eligible_for_model": False, "status": "experimental",
    }
