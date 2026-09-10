"""Safe, idempotent migration from betting data to sports analytics data."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3


EXCLUDED_TABLES = {"manual_odds", "prediction_snapshots", "sqlite_sequence"}
NEUTRAL_PREDICTIONS = {
    "1X2": "Resultado probable",
    "Exact Score": "Marcador principal",
    "Exact Score (favorito)": "Marcador condicionado",
    "Exact Score (alt)": "Marcador alternativo",
    "Expected Score": "Goles esperados",
    "Exact Score Grid": "Mapa de marcadores",
}


@dataclass(frozen=True)
class MigrationReport:
    odds_rows_removed: int
    snapshots_migrated: int
    foreign_key_check_ok: bool
    sports_invariants_equal: bool


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if str(row[0]) not in EXCLUDED_TABLES
    ]


def capture_sports_invariants(connection: sqlite3.Connection) -> dict[str, object]:
    """Capture stable counts and hashes for every non-betting table."""
    invariants: dict[str, object] = {}
    for table in _tables(connection):
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        encoded = json.dumps(
            [dict(zip(columns, row)) for row in rows],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        invariants[table] = {"count": len(rows), "sha256": sha256(encoded).hexdigest()}
    return invariants


def migrate_snapshot_payload(payload: dict) -> dict:
    """Preserve analytical evidence while dropping betting-only derivatives."""
    migrated = dict(payload)
    predictions = []
    for original in payload.get("predictions") or []:
        row = dict(original)
        name = str(row.get("market_name") or row.get("label") or "")
        label = NEUTRAL_PREDICTIONS.get(name)
        if label is None:
            continue
        row.pop("market_family", None)
        row.pop("market_name", None)
        row.pop("line", None)
        row["label"] = label
        if row.get("selection_name") == "Draw":
            row["selection_name"] = "Empate"
        predictions.append(row)
    if "predictions" in payload:
        migrated["predictions"] = predictions
    migrated["analytics_schema_version"] = 1
    return migrated


def migrate_worldcup_database(database: Path, backup: Path) -> MigrationReport:
    database = Path(database)
    backup = Path(backup)
    if database.resolve() == backup.resolve():
        raise ValueError("La copia de seguridad debe tener una ruta distinta")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database, backup)

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        before = capture_sports_invariants(connection)
        foreign_keys_before = connection.execute("PRAGMA foreign_key_check").fetchall()
        connection.execute("BEGIN IMMEDIATE")
        odds_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_odds'"
        ).fetchone()
        odds_rows = (
            int(connection.execute("SELECT COUNT(*) FROM manual_odds").fetchone()[0])
            if odds_table
            else 0
        )
        snapshots_migrated = 0
        snapshot_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'"
        ).fetchone()
        if snapshot_table:
            for snapshot_id, raw in connection.execute(
                "SELECT id, payload_json FROM prediction_snapshots"
            ).fetchall():
                payload = json.loads(raw)
                migrated = migrate_snapshot_payload(payload)
                encoded = json.dumps(migrated, ensure_ascii=False, sort_keys=True)
                if encoded != raw:
                    connection.execute(
                        "UPDATE prediction_snapshots SET payload_json=? WHERE id=?",
                        (encoded, snapshot_id),
                    )
                    snapshots_migrated += 1
        if odds_table:
            connection.execute("DROP TABLE manual_odds")
        after = capture_sports_invariants(connection)
        foreign_keys_after = connection.execute("PRAGMA foreign_key_check").fetchall()
        foreign_keys_ok = foreign_keys_after == foreign_keys_before
        if before != after or not foreign_keys_ok:
            connection.rollback()
            changed_tables = sorted(
                table for table in set(before) | set(after)
                if before.get(table) != after.get(table)
            )
            raise RuntimeError(
                "La migración alteraría datos deportivos protegidos "
                f"(tablas={changed_tables}, fk_antes={len(foreign_keys_before)}, "
                f"fk_despues={len(foreign_keys_after)})"
            )
        connection.commit()
        return MigrationReport(
            odds_rows_removed=odds_rows,
            snapshots_migrated=snapshots_migrated,
            foreign_key_check_ok=foreign_keys_ok,
            sports_invariants_equal=True,
        )
    finally:
        connection.close()
