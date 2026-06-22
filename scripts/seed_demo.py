from datetime import datetime, timezone
from pathlib import Path
import csv
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.repository import Repository  # noqa: E402
from wcpredict.schedule import seed_schedule  # noqa: E402


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()
    seed_schedule(repo, ROOT / "data" / "fixtures" / "world_cup_2026_schedule.csv")
    matches = repo.list_matches()
    match_id = matches[0].id
    odds_path = ROOT / "data" / "fixtures" / "sample_odds.csv"
    with odds_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            repo.add_manual_odds(
                match_id=match_id,
                market_family=row["market_family"],
                market_name=row["market_name"],
                selection_name=row["selection_name"],
                line=float(row["line"]) if row["line"] else None,
                decimal_odds=float(row["decimal_odds"]),
                bookmaker=row["bookmaker"],
                captured_at_utc=datetime.now(timezone.utc),
            )
    print(f"Seeded demo database at {repo.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
