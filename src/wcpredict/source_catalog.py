from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SourceDefinition:
    provider_id: str
    label: str
    bank: int
    reliability: float
    cost_tier: str
    resource_tier: str
    domains: tuple[str, ...]
    freshness_hours: int
    requires_credentials: bool = False
    notes: str = ""


@dataclass(frozen=True)
class RouteResult:
    status: str
    selected: dict[str, Any] | None
    candidates: tuple[dict[str, Any], ...]
    skipped: tuple[str, ...]


def default_source_catalog() -> list[SourceDefinition]:
    return [
        SourceDefinition("reviewed_capture", "Captura revisada por el usuario", 0, 0.99, "free", "medium", ("postmatch_stats", "player_stats", "results"), 24 * 365, notes="Revisión obligatoria y huella del archivo"),
        SourceDefinition("official_competition", "FIFA / competición oficial", 0, 0.99, "free_or_restricted", "low", ("fixtures", "results", "lineups", "postmatch_stats"), 12),
        SourceDefinition("exact_bookmaker", "Cuota del bookmaker exacto", 0, 0.99, "free_manual", "low", ("odds",), 1),
        SourceDefinition("martj42", "Resultados internacionales (fuente original)", 1, 0.94, "free", "low", ("historical_results", "international_scorers"), 24 * 30),
        SourceDefinition("openfootball", "OpenFootball internationals", 1, 0.91, "free", "low", ("historical_results",), 24 * 30),
        SourceDefinition("statsbomb_open", "StatsBomb Open Data", 1, 0.95, "free", "medium", ("events", "player_stats"), 24 * 30),
        SourceDefinition("transfermarkt_dataset", "Transfermarkt datasets", 1, 0.88, "free", "medium", ("entities", "squads", "valuations"), 24 * 14, notes="Usar el dataset publicado y revisar términos de procedencia"),
        SourceDefinition("open_meteo", "Open-Meteo", 1, 0.94, "free", "low", ("weather",), 3),
        SourceDefinition("xgabora", "Datos históricos de partidos de clubes", 1, 0.88, "free", "medium", ("club_history", "historical_odds"), 24 * 30),
        SourceDefinition("swaptr_wc2026_matches", "Partidos diarios del Mundial 2026", 1, 0.86, "free", "low", ("world_cup_2026", "fixtures", "results", "postmatch_stats"), 36, notes="Fuente comunitaria diaria; contrastar conflictos con FIFA o captura revisada"),
        SourceDefinition("swaptr_wc2026_teams", "Selecciones diarias del Mundial 2026", 1, 0.84, "free", "low", ("world_cup_2026", "team_form", "postmatch_stats"), 36, notes="Fuente comunitaria diaria; conserva versión y hash"),
        SourceDefinition("swaptr_wc2026_players", "Jugadores diarios del Mundial 2026", 1, 0.84, "free", "medium", ("world_cup_2026", "player_stats"), 36, notes="Fuente comunitaria diaria; alta importancia predictiva, autoridad subordinada a evidencia revisada"),
        SourceDefinition("api_football", "API-Football", 2, 0.90, "freemium", "low", ("fixtures", "lineups", "postmatch_stats", "player_stats"), 2, True),
        SourceDefinition("football_data_org", "football-data.org", 2, 0.88, "freemium", "low", ("fixtures", "results"), 3, True),
        SourceDefinition("odds_api", "The Odds API", 2, 0.91, "pay_per_use", "low", ("odds",), 1, True),
        SourceDefinition("sofascore_hybrid", "SofaScore híbrido", 3, 0.82, "free_unofficial", "high", ("fixtures", "lineups", "postmatch_stats", "player_stats"), 6, notes="Automatización frágil; captura revisada como respaldo"),
        SourceDefinition("soccerdata", "Adaptadores de soccerdata", 3, 0.78, "free_unofficial", "high", ("club_history", "player_stats", "ratings"), 24),
        SourceDefinition("kaggle_mirror", "Conjuntos comunitarios de Kaggle", 3, 0.65, "free", "medium", ("historical_results", "world_cup_2026"), 24 * 90, notes="Verificar autor, fecha, licencia y fuente original"),
        SourceDefinition("x_api", "X API", 3, 0.58, "pay_per_use", "high", ("sentiment",), 2, True, "Señal experimental; nunca sustituye datos deportivos"),
    ]


def route_observations(
    domain: str,
    observations: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    paid_budget_usd: float = 0.0,
    catalog: list[SourceDefinition] | None = None,
) -> RouteResult:
    now = now or datetime.now(timezone.utc)
    definitions = {row.provider_id: row for row in (catalog or default_source_catalog())}
    eligible: list[tuple[SourceDefinition, dict[str, Any]]] = []
    skipped: list[str] = []
    for observation in observations:
        provider_id = str(observation.get("provider_id") or "")
        definition = definitions.get(provider_id)
        if definition is None or domain not in definition.domains:
            skipped.append(f"{provider_id}:unsupported")
            continue
        if definition.cost_tier == "pay_per_use" and paid_budget_usd <= 0:
            skipped.append(f"{provider_id}:budget")
            continue
        if observation.get("available") is False:
            skipped.append(f"{provider_id}:unavailable")
            continue
        try:
            observed = datetime.fromisoformat(str(observation["observed_at_utc"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            skipped.append(f"{provider_id}:timestamp")
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (now - observed).total_seconds() / 3600)
        if age_hours > definition.freshness_hours:
            skipped.append(f"{provider_id}:stale")
            continue
        eligible.append((definition, observation))
    if not eligible:
        return RouteResult("not_found", None, (), tuple(skipped))
    best_bank = min(definition.bank for definition, _ in eligible)
    bank_rows = [(definition, row) for definition, row in eligible if definition.bank == best_bank]
    distinct = {repr(row.get("value")) for _, row in bank_rows}
    if len(bank_rows) > 1 and len(distinct) > 1:
        return RouteResult("conflicting", None, tuple(row for _, row in bank_rows), tuple(skipped))
    definition, selected = max(bank_rows, key=lambda item: item[0].reliability)
    return RouteResult("verified", selected, tuple(row for _, row in bank_rows), tuple(skipped))
