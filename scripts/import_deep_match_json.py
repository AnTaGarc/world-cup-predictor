from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.deep_match_import import load_deep_match_file  # noqa: E402
from wcpredict.names import same_team  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402
from wcpredict.schedule import seed_schedule  # noqa: E402


def preview(repository: Repository, collection) -> dict[str, int]:
    matches = repository.list_matches()
    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for record in collection.matches:
        candidates = [
            match for match in matches
            if (
                same_team(match.team_a.name, record.team_a) and same_team(match.team_b.name, record.team_b)
            ) or (
                same_team(match.team_a.name, record.team_b) and same_team(match.team_b.name, record.team_a)
            )
        ]
        key = "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
        counts[key] += 1
        if key != "matched":
            print(f"{key}: {record.name} -> {len(candidates)} candidatos")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa estadísticas profundas revisadas de partidos")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    collection = load_deep_match_file(args.json_path)
    repository = Repository(ROOT / "data" / "worldcup.sqlite")
    repository.initialize()
    seed_schedule(repository, ROOT / "data" / "fixtures" / "world_cup_2026_schedule.csv")
    counts = preview(repository, collection)
    print(f"hash={collection.sha256} partidos={len(collection.matches)} preview={counts}")
    if args.dry_run or counts["ambiguous"] or counts["unmatched"]:
        return 0 if not counts["ambiguous"] and not counts["unmatched"] else 2
    result = repository.import_deep_match_collection(collection, datetime.now(timezone.utc))
    print(
        f"importados={result.imported_matches} sin_cambios={result.unchanged_matches} "
        f"observaciones={result.observations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
