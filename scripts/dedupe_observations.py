"""Eliminar observaciones duplicadas en la tabla ``observations``.

Cada vez que se importaba un JSON profundo con un SHA distinto, las mismas
métricas para el mismo partido se insertaban de nuevo con un ``source_id``
diferente (la UNIQUE constraint de la tabla incluye ``source_id`` así que no
las consideraba duplicadas). Esto infla la BD ~5x sin aportar información:
la query ``list_deep_team_metric_observations_before`` ya devuelve solo la
observación más reciente por ``(match_id, subject_name, metric)``.

Este script borra todas las observaciones redundantes y conserva, para cada
combinación de ``(match_id, subject_type, subject_name, metric, context_json)``,
SOLO la fila con el ``id`` más alto (la última importada). Luego ejecuta
``VACUUM`` para reclamar el espacio en disco.

Antes de borrar nada hace un backup de la BD a ``data/worldcup.sqlite.pre-dedupe-obs``.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "worldcup.sqlite"


def main() -> int:
    if not DB.exists():
        print(f"DB not found at {DB}")
        return 1
    backup = DB.with_suffix(".sqlite.pre-dedupe-obs")
    print(f"Backup -> {backup}")
    shutil.copy2(DB, backup)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    before = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    size_before = DB.stat().st_size / 1024 / 1024
    print(f"Filas observations antes: {before:,}  ({size_before:.1f} MB)")

    # Mantén solo la fila con MAX(id) por combinación natural.
    print("Identificando duplicados...")
    duplicates_query = """
        DELETE FROM observations
        WHERE id NOT IN (
            SELECT MAX(id) FROM observations
            GROUP BY match_id, subject_type, subject_name, metric, context_json
        )
    """
    cur = con.execute(duplicates_query)
    deleted = cur.rowcount
    con.commit()
    print(f"Borradas: {deleted:,} filas")

    # VACUUM no puede ir dentro de una transacción.
    con.isolation_level = None
    print("Ejecutando VACUUM...")
    con.execute("VACUUM")
    con.close()

    after = sqlite3.connect(DB).execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    size_after = DB.stat().st_size / 1024 / 1024
    print(f"\nFilas observations después: {after:,}  ({size_after:.1f} MB)")
    print(f"Reducción: {before - after:,} filas ({(1 - after/before)*100:.1f}%)")
    print(f"Reducción tamaño: {size_before - size_after:.1f} MB ({(1 - size_after/size_before)*100:.1f}%)")
    print(f"\nBackup preservado en: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
