import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.repository import Repository

from backfill_github_wc2026 import run_backfill


def _fx(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / "github_wc2026" / name).read_bytes()


FIXTURES = {
    "github_wc2026_events": "match_events.csv",
    "github_wc2026_team_stats": "match_team_stats.csv",
    "github_wc2026_lineups": "match_lineups.csv",
    "github_wc2026_matches": "matches_detailed.csv",
    "github_wc2026_referees": "referees.csv",
    "github_wc2026_player_stats": "player_stats.csv",
    "github_wc2026_teams": "teams.csv",
}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def _fetcher(self, provider_id):
        return DatasetDownload(
            provider_id, "sha:local/parser-1", _fx(FIXTURES[provider_id]), self.now, 1
        )

    def test_backfill_imports_all_providers_once(self):
        report = run_backfill(self.repo, fetcher=self._fetcher, now=self.now)
        self.assertEqual(7, len(report["imported"]))
        with self.repo.session() as con:
            events = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, events)

    def test_backfill_is_idempotent(self):
        run_backfill(self.repo, fetcher=self._fetcher, now=self.now)
        run_backfill(self.repo, fetcher=self._fetcher, now=self.now)
        with self.repo.session() as con:
            events = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, events)

    def test_backfill_reports_observations_synced(self):
        report = run_backfill(self.repo, fetcher=self._fetcher, now=self.now)
        self.assertIn("observations_synced", report)
