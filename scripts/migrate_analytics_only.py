from argparse import ArgumentParser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.analytics_migration import migrate_worldcup_database


def main() -> None:
    parser = ArgumentParser(description="Migra una base a la edición de analítica deportiva")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    report = migrate_worldcup_database(args.database, args.backup)
    print(
        f"odds_rows_removed={report.odds_rows_removed} "
        f"snapshots_migrated={report.snapshots_migrated} "
        f"foreign_key_check={'ok' if report.foreign_key_check_ok else 'failed'} "
        f"sports_invariants={'equal' if report.sports_invariants_equal else 'changed'}"
    )


if __name__ == "__main__":
    main()
