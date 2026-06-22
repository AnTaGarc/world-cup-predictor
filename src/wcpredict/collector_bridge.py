from dataclasses import dataclass
from pathlib import Path
import json
import unicodedata


@dataclass(frozen=True)
class CollectorBundle:
    events: list[dict]
    coverage: dict
    missing_critical: list[str]
    staleness_warnings: list[str]


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_value.lower().replace("cote d'ivoire", "ivory coast").strip()


def load_collector_export(export_dir: Path) -> CollectorBundle:
    data_path = export_dir / "data.json"
    manifest_path = export_dir / "manifest.json"
    if not data_path.exists():
        return CollectorBundle(events=[], coverage={}, missing_critical=[], staleness_warnings=[])

    data = json.loads(data_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return CollectorBundle(
        events=list(data.get("events", [])),
        coverage=dict(manifest.get("coverage", {})),
        missing_critical=list(manifest.get("missing_critical", [])),
        staleness_warnings=list(manifest.get("staleness_warnings", [])),
    )


def find_cached_event(bundle: CollectorBundle, team_a: str, team_b: str) -> dict | None:
    wanted = {_normalize_name(team_a), _normalize_name(team_b)}
    for event in bundle.events:
        event_teams = {
            _normalize_name(str(event.get("participant1_name", ""))),
            _normalize_name(str(event.get("participant2_name", ""))),
        }
        if event_teams == wanted:
            return event
    return None


def event_coverage_rows(bundle: CollectorBundle, event: dict) -> list[dict]:
    coverage = bundle.coverage.get(str(event.get("id")), {})
    return [
        {"Tipo": "Presentes", "Campos": ", ".join(coverage.get("present", [])) or "Ninguno"},
        {"Tipo": "Faltan criticos", "Campos": ", ".join(coverage.get("missing_critical", [])) or "Ninguno"},
        {"Tipo": "Faltan opcionales", "Campos": ", ".join(coverage.get("missing_optional", [])) or "Ninguno"},
    ]


def event_market_rows(event: dict) -> list[dict]:
    rows = []
    for market in event.get("market_comparisons", []):
        rows.append(
            {
                "Bookmaker": market.get("bookmaker"),
                "Fuente": market.get("source_label"),
                "Familia": market.get("market_family"),
                "Mercado": market.get("market_name"),
                "Periodo": market.get("period"),
                "Seleccion": market.get("outcome_name"),
                "Linea": market.get("line"),
                "Cuota": market.get("decimal_price"),
                "Imp.": market.get("implied_probability"),
                "Stale": bool(market.get("stale")),
            }
        )
    return rows


def event_evidence_rows(event: dict) -> list[dict]:
    rows = []
    for stat in event.get("statistics", []):
        rows.append(
            {
                "Metrica": stat.get("metric"),
                "Estado": stat.get("evidence_status"),
                "Fuente": (stat.get("context") or {}).get("source_name"),
                "Valor": stat.get("value"),
            }
        )
    return rows
