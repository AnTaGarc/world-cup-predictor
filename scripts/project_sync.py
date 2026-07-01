from __future__ import annotations

from pathlib import Path
import sqlite3


DURABLE_PATHS = (
    "data/worldcup.sqlite",
    "data/models",
    "data/fixtures",
    "data/evidence/reviewed-json",
    "data/precomputed",
)

FORBIDDEN_PREFIXES = (
    "data/cache/",
    "output/",
    ".codex-remote-attachments/",
    ".pytest_cache/",
    "__pycache__/",
)

FORBIDDEN_SUFFIXES = (
    ".log",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite-journal",
    ".bak",
    ".tmp",
)


class SyncError(RuntimeError):
    pass


def _normal_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def is_forbidden_path(path: str) -> bool:
    normalized = _normal_path(path)
    return normalized.startswith(FORBIDDEN_PREFIXES) or normalized.endswith(
        FORBIDDEN_SUFFIXES
    )


def checkpoint_and_validate_sqlite(db_path: Path) -> None:
    if not db_path.is_file():
        raise SyncError(f"La base SQLite no existe: {db_path}")
    con = None
    try:
        con = sqlite3.connect(db_path, timeout=5)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        result = str(con.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.Error as exc:
        raise SyncError(
            "No se pudo cerrar y validar SQLite. Cierra la aplicación y vuelve a "
            f"intentarlo: {exc}"
        ) from exc
    finally:
        if con is not None:
            con.close()
    if result.casefold() != "ok":
        raise SyncError(f"SQLite no supera integrity_check: {result}")
