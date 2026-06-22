from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from wcpredict.names import canonical_team_name


@dataclass(frozen=True)
class DeepMetric:
    team_name: str
    metric: str
    value: float
    unit: str | None
    context: dict[str, Any]


@dataclass(frozen=True)
class DeepMatchRecord:
    source_match_id: str
    name: str
    team_a: str
    team_b: str
    raw_team_a: str
    raw_team_b: str
    statistics: dict[str, Any]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class DeepMatchCollection:
    path: Path
    sha256: str
    description: str
    matches: tuple[DeepMatchRecord, ...]


@dataclass(frozen=True)
class DeepImportResult:
    imported_matches: int
    unchanged_matches: int
    ambiguous_matches: int
    unmatched_matches: int
    observations: int


def load_deep_match_file(path: Path) -> DeepMatchCollection:
    content = path.read_bytes()
    payload = json.loads(content.decode("utf-8-sig"))
    rows = payload.get("partidos")
    if not isinstance(rows, list):
        raise ValueError("El JSON no contiene una lista de partidos")
    if int(payload.get("numero_de_partidos", -1)) != len(rows):
        raise ValueError("El número de partidos declarado no coincide con el contenido")
    matches = []
    for row in rows:
        teams = row.get("equipos") or {}
        raw_a = str(teams.get("izquierda_verde") or "").strip()
        raw_b = str(teams.get("derecha_azul") or "").strip()
        if not raw_a or not raw_b or raw_a == raw_b:
            raise ValueError(f"Equipos inválidos en {row.get('id')}")
        statistics = row.get("estadisticas")
        if not isinstance(statistics, dict):
            raise ValueError(f"Estadísticas inválidas en {row.get('id')}")
        matches.append(DeepMatchRecord(
            str(row.get("id") or ""), str(row.get("nombre") or f"{raw_a} vs {raw_b}"),
            canonical_team_name(raw_a), canonical_team_name(raw_b), raw_a, raw_b,
            statistics, tuple(str(value) for value in (row.get("fuentes") or [])),
        ))
    return DeepMatchCollection(
        path, sha256(content).hexdigest(), str(payload.get("descripcion") or ""), tuple(matches)
    )


def _unit(metric: str) -> str | None:
    if metric.endswith("_pct") or metric.endswith(".porcentaje"):
        return "%"
    if metric.endswith("_km"):
        return "km"
    return None


def flatten_team_metrics(match: DeepMatchRecord) -> list[DeepMetric]:
    output: list[DeepMetric] = []
    raw_to_canonical = {match.raw_team_a: match.team_a, match.raw_team_b: match.team_b}

    def add_value(team: str, path: tuple[str, ...], value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            metric = ".".join(path)
            output.append(DeepMetric(team, metric, float(value), _unit(metric), {"json_path": metric}))
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"estado_dato", "descripcion"}:
                    continue
                add_value(team, path + (str(key),), child)

    def visit(node: Any, path: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        present = [raw for raw in raw_to_canonical if raw in node]
        if present:
            for raw in present:
                add_value(raw_to_canonical[raw], path, node.get(raw))
            return
        for key, child in node.items():
            if key in {"estado_dato", "descripcion", "direccion_de_ataque", "tipo_de_dato", "equipo_mostrado"}:
                continue
            visit(child, path + (str(key),))

    visit(match.statistics, ())
    return output
