from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.collector_store import CollectorStore  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402
from wcpredict.schedule import seed_schedule  # noqa: E402


FIXTURES = {
    "Czechia vs South Africa",
    "Switzerland vs Bosnia and Herzegovina",
    "Canada vs Qatar",
    "Mexico vs South Korea",
}


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()
    seed_schedule(repo, ROOT / "data" / "fixtures" / "world_cup_2026_schedule.csv")
    store = CollectorStore(ROOT.parent / "sports-data" / "sports.db")
    imported = 0
    for match in repo.list_matches():
        if match.label not in FIXTURES:
            continue
        bundle = store.find_event(match.team_a.name, match.team_b.name, match.kickoff_utc.date())
        if bundle is None:
            print(f"MISSING {match.label}")
            continue
        repo.import_collector_bundle(match.id, bundle)
        imported += 1
        print(
            f"{match.label}: {bundle.completeness_status}; "
            f"stats={len(bundle.statistics)} lineups={len(bundle.lineups)} "
            f"missing={bundle.missing_critical + bundle.missing_optional}"
        )
    print(f"Imported calibration fixtures: {imported}/4")
    return 0 if imported == 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
