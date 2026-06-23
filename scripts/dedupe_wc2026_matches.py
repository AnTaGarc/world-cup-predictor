"""Eliminar partidos WC2026 duplicados que el feed swaptr insertó con
home/away invertido respecto a los del seed.

Para cada par de equipos (sin orden) que tenga varios matches WC2026, se
queda con el de id más bajo (el seed original) y reasigna manual_odds,
predictions, observations, etc. al match conservado antes de borrar los
duplicados. Hace backup de la BD antes.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "worldcup.sqlite"

TABLES_WITH_MATCH_ID = [
    "manual_odds",
    "predictions",
    "observations",
    "imported_lineups",
    "import_runs",
    "team_match_stats",
    "match_results",
]


def main() -> int:
    if not DB.exists():
        print(f"DB no encontrada: {DB}")
        return 1
    backup = DB.with_suffix(".sqlite.pre-match-dedupe")
    print(f"Backup -> {backup}")
    shutil.copy2(DB, backup)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    before = con.execute(
        "SELECT COUNT(*) FROM matches WHERE competition='FIFA World Cup 2026'"
    ).fetchone()[0]
    print(f"Partidos WC2026 antes: {before}")

    rows = con.execute(
        "SELECT id, team_a_id, team_b_id, kickoff_utc FROM matches "
        "WHERE competition='FIFA World Cup 2026' ORDER BY id"
    ).fetchall()

    by_pair: dict[tuple[int, int], list[sqlite3.Row]] = {}
    for row in rows:
        key = tuple(sorted((int(row["team_a_id"]), int(row["team_b_id"]))))
        by_pair.setdefault(key, []).append(row)

    removed = 0
    for pair, matches in by_pair.items():
        if len(matches) <= 1:
            continue
        keep = matches[0]
        drops = matches[1:]
        keep_id = int(keep["id"])
        for d in drops:
            drop_id = int(d["id"])
            for table in TABLES_WITH_MATCH_ID:
                exists = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    continue
                try:
                    con.execute(
                        f"UPDATE OR IGNORE {table} SET match_id=? WHERE match_id=?",
                        (keep_id, drop_id),
                    )
                    # Delete remaining rows that collided with UNIQUE constraints.
                    con.execute(f"DELETE FROM {table} WHERE match_id=?", (drop_id,))
                except sqlite3.OperationalError:
                    pass
            con.execute("DELETE FROM matches WHERE id=?", (drop_id,))
            removed += 1
            print(f"  borrado match id={drop_id} (par {pair}); conservado id={keep_id}")
    con.commit()
    con.close()

    after = sqlite3.connect(DB).execute(
        "SELECT COUNT(*) FROM matches WHERE competition='FIFA World Cup 2026'"
    ).fetchone()[0]
    print(f"\nPartidos WC2026 después: {after}  (-{removed})")
    print(f"Backup preservado en: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
