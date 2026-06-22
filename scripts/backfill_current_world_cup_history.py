"""Incorpora al histórico los resultados ya presentes en el banco diario local."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository  # noqa: E402


def main() -> None:
    repository = Repository(ROOT / "data" / "worldcup.sqlite")
    repository.initialize()
    with sqlite3.connect(repository.path) as connection:
        rows = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT data_json FROM current_wc_match_stats WHERE provider_id=? ORDER BY match_key",
                ("swaptr_wc2026_matches",),
            ).fetchall()
        ]
    repository.replace_current_world_cup_matches(
        "swaptr_wc2026_matches", rows, datetime.now(timezone.utc)
    )
    finished = sum(row.get("goals_a") is not None and row.get("goals_b") is not None for row in rows)
    print(f"Partidos diarios revisados: {len(rows)}; finalizados incorporados: {finished}")


if __name__ == "__main__":
    main()
