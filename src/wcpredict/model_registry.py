from __future__ import annotations

from dataclasses import dataclass

from wcpredict.models import MarketFamily


@dataclass(frozen=True)
class MarketModelPolicy:
    market: str
    active: str
    challenger: str | None
    fallback: str | None
    required_features: tuple[str, ...]
    validation_metric: str
    note: str


def _policy(
    market: str,
    active: str,
    challenger: str | None,
    fallback: str | None,
    features: tuple[str, ...],
    metric: str,
    note: str,
) -> MarketModelPolicy:
    return MarketModelPolicy(market, active, challenger, fallback, features, metric, note)


POLICIES = {
    MarketFamily.MATCH_RESULT.value: _policy(
        "1X2",
        "unified_1x2_blend",
        "calibrated_multinomial_blend",
        "score_matrix",
        ("chronological_ml", "score_matrix", "deep_form", "player_availability"),
        "multiclass log loss / RPS",
        "El 1X2 combina ML cronológico y matriz de goles; la matriz sola queda como respaldo.",
    ),
    MarketFamily.GOALS.value: _policy(
        "goles",
        "score_matrix",
        "dynamic_dixon_coles",
        "poisson",
        ("attack", "defence", "recency"),
        "log score",
        "Dixon-Coles debe superar al baseline fuera de muestra.",
    ),
    MarketFamily.BTTS.value: _policy(
        "BTTS",
        "score_matrix",
        "bivariate_poisson",
        "poisson",
        ("joint_score_distribution",),
        "Brier / log loss",
        "Derivado de la distribución conjunta.",
    ),
    MarketFamily.CORNERS.value: _policy(
        "córners",
        "negative_binomial",
        "compound_poisson",
        "poisson",
        ("for_rate", "against_rate", "dispersion"),
        "count log loss",
        "Usa NB solo cuando la dispersión ha sido estimada.",
    ),
    MarketFamily.CARDS.value: _policy(
        "amarillas",
        "negative_binomial",
        "hierarchical_negative_binomial",
        "poisson",
        ("for_rate", "against_rate", "referee", "dispersion"),
        "count log loss",
        "Árbitro y contexto quedan ausentes si no hay evidencia.",
    ),
    MarketFamily.SHOTS.value: _policy(
        "tiros",
        "negative_binomial",
        "hierarchical_negative_binomial",
        "poisson",
        ("for_rate", "against_rate", "dispersion"),
        "count log loss",
        "La sobredispersión debe medirse.",
    ),
    MarketFamily.SHOTS_ON_TARGET.value: _policy(
        "tiros a puerta",
        "negative_binomial",
        "conditional_binomial",
        "poisson",
        ("shots", "accuracy", "minutes"),
        "log loss",
        "El challenger condiciona tiros a puerta a tiros totales.",
    ),
    "red_cards": _policy(
        "rojas",
        "rare_event_logistic",
        "hierarchical_cloglog",
        None,
        ("fouls", "referee", "stage"),
        "rare-event log loss",
        "No se trata como un conteo Poisson ordinario.",
    ),
    MarketFamily.PLAYER_GOAL.value: _policy(
        "gol de jugador",
        "exposure_count",
        "hierarchical_player_count",
        "poisson",
        ("per90", "expected_minutes", "lineup", "team_xg"),
        "log loss",
        "Integra la incertidumbre de minutos.",
    ),
    MarketFamily.PLAYER_ASSIST.value: _policy(
        "asistencia",
        "exposure_count",
        "hurdle_count",
        "poisson",
        ("per90", "expected_minutes", "team_xg"),
        "log loss",
        "Se valida un hurdle por exceso de ceros.",
    ),
    MarketFamily.PLAYER_SHOTS.value: _policy(
        "tiros de jugador",
        "exposure_count",
        "negative_binomial",
        "poisson",
        ("per90", "expected_minutes", "dispersion"),
        "count log loss",
        "NB se usa con dispersión observada.",
    ),
    MarketFamily.PLAYER_SHOTS_ON_TARGET.value: _policy(
        "tiros a puerta de jugador",
        "exposure_count",
        "conditional_binomial",
        "poisson",
        ("shots", "accuracy", "expected_minutes"),
        "log loss",
        "Condicional a los tiros si hay muestra.",
    ),
    MarketFamily.PLAYER_CARDS.value: _policy(
        "tarjeta de jugador",
        "binary_logistic",
        "hierarchical_hazard",
        None,
        ("minutes", "fouls", "referee"),
        "Brier / log loss",
        "Mercado binario dependiente de exposición.",
    ),
    MarketFamily.PLAYER_PASSES.value: _policy(
        "pases",
        "negative_binomial",
        "quantile_count",
        "poisson",
        ("per90", "minutes", "possession", "opponent_press"),
        "count log loss",
        "Debe reflejar posesión y rol.",
    ),
}


def market_model_policy(market: MarketFamily | str) -> MarketModelPolicy:
    key = market.value if isinstance(market, MarketFamily) else str(market)
    if key in POLICIES:
        return POLICIES[key]
    return _policy(
        key,
        "manual_review",
        None,
        None,
        ("verified_evidence",),
        "not configured",
        "Mercado sin política validada.",
    )
