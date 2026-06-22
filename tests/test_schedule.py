from pathlib import Path
from datetime import datetime, timezone
import shutil
import tempfile
import unittest

from wcpredict.repository import Repository
from wcpredict.schedule import load_schedule_csv, seed_schedule


class ScheduleTests(unittest.TestCase):
    def test_load_schedule_csv_returns_matches_without_api_calls(self):
        path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "world_cup_2026_schedule.csv"
        matches = load_schedule_csv(path)
        labels = {match["label"] for match in matches}
        self.assertIn("Mexico vs South Africa", labels)
        self.assertIn("USA vs Paraguay", labels)
        self.assertIn("England vs Croatia", labels)
        self.assertIn("Mexico vs South Korea", labels)

        kickoffs = {match["label"]: match["kickoff_utc"] for match in matches}
        self.assertEqual("2026-06-18T16:00:00+00:00", kickoffs["Czechia vs South Africa"])
        self.assertEqual("2026-06-18T19:00:00+00:00", kickoffs["Switzerland vs Bosnia and Herzegovina"])
        self.assertEqual("2026-06-18T22:00:00+00:00", kickoffs["Canada vs Qatar"])
        self.assertEqual("2026-06-19T01:00:00+00:00", kickoffs["Mexico vs South Korea"])

    def test_seed_schedule_creates_selectable_matches(self):
        path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "world_cup_2026_schedule.csv"
        tmp = tempfile.mkdtemp()
        try:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            seed_schedule(repo, path)
            seed_schedule(repo, path)
            matches = repo.list_matches()
            del repo
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertGreaterEqual(len(matches), 60)
        self.assertEqual("Mexico vs South Africa", matches[0].label)
        mexico_korea = next(match for match in matches if match.label == "Mexico vs South Korea")
        self.assertEqual(
            datetime(2026, 6, 19, 1, 0, tzinfo=timezone.utc),
            mexico_korea.kickoff_utc,
        )

    def test_seed_schedule_replaces_legacy_midnight_fixture(self):
        path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "world_cup_2026_schedule.csv"
        tmp = tempfile.mkdtemp()
        try:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            a = repo.upsert_team("Czechia")
            b = repo.upsert_team("South Africa")
            repo.upsert_match(
                "FIFA World Cup 2026", "Group stage - Group A",
                datetime(2026, 6, 18, tzinfo=timezone.utc), a, b, "scheduled"
            )
            seed_schedule(repo, path)
            matching = [m for m in repo.list_matches() if m.label == "Czechia vs South Africa"]
            del repo
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(1, len(matching))
        self.assertEqual(datetime(2026, 6, 18, 16, tzinfo=timezone.utc), matching[0].kickoff_utc)


if __name__ == "__main__":
    unittest.main()
