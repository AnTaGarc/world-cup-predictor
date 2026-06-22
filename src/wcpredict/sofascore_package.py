from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json

from wcpredict.names import same_team


@dataclass(frozen=True)
class SofaScoreRecord:
    subject_type: str
    subject_name: str | None
    metric: str
    value_number: float | None
    value_text: str | None
    unit: str | None
    period: str
    method: str
    confidence: float


@dataclass(frozen=True)
class SofaScorePackage:
    event_id: int
    source_url: str
    team_a: str
    team_b: str
    observed_at_utc: datetime
    records: tuple[SofaScoreRecord, ...]
    warnings: tuple[str, ...]


def load_sofascore_package(path: Path) -> SofaScorePackage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("Unsupported SofaScore package schema")
    records = tuple(SofaScoreRecord(**row) for row in payload.get("records", []))
    if any(
        row.method not in {"dom", "vision", "manual_confirmation"}
        for row in records
    ):
        raise ValueError("Unsupported extraction method")
    return SofaScorePackage(
        event_id=int(payload["event_id"]),
        source_url=payload["source_url"],
        team_a=payload["team_a"],
        team_b=payload["team_b"],
        observed_at_utc=datetime.fromisoformat(payload["observed_at_utc"]),
        records=records,
        warnings=tuple(payload.get("warnings", [])),
    )


def validate_package_identity(
    package: SofaScorePackage, team_a: str, team_b: str
) -> None:
    if not same_team(package.team_a, team_a) or not same_team(
        package.team_b, team_b
    ):
        raise ValueError("SofaScore package does not match selected match")
