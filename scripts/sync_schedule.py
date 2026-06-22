from pathlib import Path
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
    print(f"Calendario sincronizado: {len(repo.list_matches())} partidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
