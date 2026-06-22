from __future__ import annotations


MARKETS = {
    "Double Chance": "Doble oportunidad",
    "Draw No Bet": "Empate no válido",
    "Both Teams To Score": "Ambos equipos marcan",
    "1X2": "1X2",
    "Player Anytime Goal": "Jugador marca en cualquier momento",
    "Player Card": "Tarjeta del jugador",
}

MARKET_FAMILIES = {
    "match_result": "Resultado del partido",
    "double_chance": "Doble oportunidad",
    "draw_no_bet": "Empate no válido",
    "handicap": "Hándicap",
    "goals": "Goles",
    "both_teams_to_score": "Ambos equipos marcan",
    "team_totals": "Totales de equipo",
    "corners": "Córners",
    "cards": "Tarjetas",
    "shots": "Tiros",
    "shots_on_target": "Tiros a puerta",
    "player_goal": "Gol del jugador",
    "player_assist": "Asistencia del jugador",
    "player_shots": "Tiros del jugador",
    "player_shots_on_target": "Tiros a puerta del jugador",
    "player_cards": "Tarjeta del jugador",
    "player_passes": "Pases del jugador",
    "player_saves": "Paradas del portero",
    "player_goals_conceded": "Goles concedidos por el portero",
    "player_clean_sheet": "Portería a cero",
    "custom": "Personalizado",
}

METRICS = {
    "corners": "Córners",
    "cards": "Tarjetas",
    "shots": "Tiros",
    "shots_on_target": "Tiros a puerta",
}

RESOURCE_TIERS = {"low": "Bajo", "medium": "Medio", "high": "Alto"}
COST_TIERS = {
    "free": "Gratuito",
    "free_manual": "Gratuito/manual",
    "free_or_restricted": "Gratuito o restringido",
    "free_unofficial": "Gratuito/no oficial",
    "freemium": "Freemium",
    "pay_per_use": "Pago por uso",
}

CONFIDENCE = {
    "high": "Alta",
    "medium": "Media",
    "low": "Baja",
    "not_estimable": "No estimable",
    "no_evidence": "Sin evidencia",
}

MODELS = {
    "unified_1x2_blend": "Modelo unificado 1X2",
    "score_matrix": "Matriz de marcadores",
    "calibrated_multinomial_blend": "Ensemble multiclase calibrado",
    "dynamic_dixon_coles": "Dixon–Coles dinámico",
    "bivariate_poisson": "Poisson bivariante",
    "negative_binomial": "Binomial negativa",
    "hierarchical_negative_binomial": "Binomial negativa jerárquica",
    "compound_poisson": "Poisson compuesto",
    "conditional_binomial": "Binomial condicional",
    "rare_event_logistic": "Logística para eventos raros",
    "hierarchical_cloglog": "Cloglog jerárquico",
    "exposure_count": "Conteo ajustado por exposición",
    "hierarchical_player_count": "Conteo jerárquico de jugador",
    "hurdle_count": "Modelo hurdle de conteo",
    "binary_logistic": "Logística binaria",
    "hierarchical_hazard": "Riesgo jerárquico",
    "quantile_count": "Conteo por cuantiles",
    "poisson": "Poisson",
    "elo": "Elo",
    "manual_review": "Revisión manual",
}

STATUSES = {
    "current": "Actual",
    "updated": "Actualizado",
    "partial": "Parcial",
    "stale": "Obsoleto",
    "failed": "Fallido",
    "ready": "Listo",
    "complete": "Completo",
    "cached": "En caché",
}

ORIGINS = {
    "baseline": "Modelo base",
    "observed_form": "Forma observada",
    "player_adjusted": "Ajustado por jugadores",
    "unified_model": "Modelo unificado",
}

TABLE_COLUMNS = {
    "match_id": "ID del partido",
    "team_name": "Selección",
    "player_name": "Jugador",
    "minutes": "Minutos",
    "games": "Partidos",
    "starts": "Titularidades",
    "goals": "Goles",
    "assists": "Asistencias",
    "shots": "Tiros",
    "shots_on_target": "Tiros a puerta",
    "passes": "Pases",
    "goals_per90": "Goles / 90",
    "assists_per90": "Asistencias / 90",
    "shots_per90": "Tiros / 90",
    "shots_on_target_per90": "Tiros a puerta / 90",
    "passes_per90": "Pases / 90",
    "impact": "Impacto relativo",
    "style_label": "Estilo",
    "corners": "Córners",
    "cards": "Tarjetas",
    "source_id": "Fuente",
    "source_url": "URL de la fuente",
    "captured_at_utc": "Capturado (UTC)",
    "created_at_utc": "Creado (UTC)",
    "market_name": "Mercado",
    "selection_name": "Selección del mercado",
    "probability": "Probabilidad",
    "decimal_odds": "Cuota decimal",
    "confidence": "Confianza",
    "status": "Estado",
    "metric": "Métrica",
    "value": "Valor",
}


def localize_market(value: str) -> str:
    if value.startswith("Over/Under "):
        return "Más/menos de " + value.removeprefix("Over/Under ")
    if value.startswith("Total Corners "):
        return "Córners totales " + value.removeprefix("Total Corners ")
    if value.startswith("Total Cards "):
        return "Tarjetas totales " + value.removeprefix("Total Cards ")
    if value.startswith("Player Shots On Target "):
        return "Tiros a puerta del jugador " + value.removeprefix("Player Shots On Target ")
    if value.startswith("Player Shots "):
        return "Tiros del jugador " + value.removeprefix("Player Shots ")
    if value.startswith("Player Passes "):
        return "Pases del jugador " + value.removeprefix("Player Passes ")
    if " Shots On Target " in value:
        return value.replace(" Shots On Target ", " · tiros a puerta ")
    if " Shots " in value:
        return value.replace(" Shots ", " · tiros ")
    if " Corners " in value:
        return value.replace(" Corners ", " · córners ")
    return MARKETS.get(value, value)


def localize_selection(value: str) -> str:
    if value == "Draw":
        return "Empate"
    if value == "Yes":
        return "Sí"
    if value == "No":
        return "No"
    if value.startswith("Over "):
        return "Más de " + value.removeprefix("Over ")
    if value.startswith("Under "):
        return "Menos de " + value.removeprefix("Under ")
    if value.endswith(" or Draw"):
        return value.removesuffix(" or Draw") + " o empate"
    return value


def localize_confidence(value: str) -> str:
    return CONFIDENCE.get(value, value)


def localize_model(value: str | None) -> str:
    if not value:
        return "—"
    return MODELS.get(value, value.replace("_", " "))


def localize_status(value: str) -> str:
    return STATUSES.get(value, value)


def localize_origin(value: str) -> str:
    return ORIGINS.get(value, value)


def localize_market_family(value: str) -> str:
    return MARKET_FAMILIES.get(value, value.replace("_", " "))


def localize_metric(value: str) -> str:
    return METRICS.get(value, value.replace("_", " "))


def localize_resource_tier(value: str) -> str:
    return RESOURCE_TIERS.get(value, value)


def localize_cost_tier(value: str) -> str:
    return COST_TIERS.get(value, value)


def canonical_market(value: str) -> str:
    reverse = {translated: original for original, translated in MARKETS.items()}
    if value.startswith("Más/menos de "):
        return "Over/Under " + value.removeprefix("Más/menos de ")
    if value.startswith("Córners totales "):
        return "Total Corners " + value.removeprefix("Córners totales ")
    if value.startswith("Tarjetas totales "):
        return "Total Cards " + value.removeprefix("Tarjetas totales ")
    if " · tiros a puerta " in value:
        return value.replace(" · tiros a puerta ", " Shots On Target ")
    if " · tiros " in value:
        return value.replace(" · tiros ", " Shots ")
    if " · córners " in value:
        return value.replace(" · córners ", " Corners ")
    return reverse.get(value, value)


def canonical_selection(value: str) -> str:
    if value == "Empate":
        return "Draw"
    if value == "Sí":
        return "Yes"
    if value.startswith("Más de "):
        return "Over " + value.removeprefix("Más de ")
    if value.startswith("Menos de "):
        return "Under " + value.removeprefix("Menos de ")
    if value.endswith(" o empate"):
        return value.removesuffix(" o empate") + " or Draw"
    return value


def canonical_market_family(value: str) -> str:
    reverse = {translated: original for original, translated in MARKET_FAMILIES.items()}
    return reverse.get(value, value)


def localize_table_columns(rows: list[dict]) -> list[dict]:
    """Translate storage-oriented field names before presenting records to users."""
    return [
        {TABLE_COLUMNS.get(str(key), str(key).replace("_", " ").capitalize()): value for key, value in row.items()}
        for row in rows
    ]
