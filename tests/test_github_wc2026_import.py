import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.github_wc2026_dataset import import_github_wc2026_download
from wcpredict.repository import Repository


def _fixture(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / "github_wc2026" / name).read_bytes()


class GhwcImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def _download(self, provider_id: str, filename: str) -> DatasetDownload:
        return DatasetDownload(
            provider_id, "sha:abc1234/parser-1", _fixture(filename), self.now, 1
        )

    def test_import_events_populates_rows_with_period(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_events", "match_events.csv"), self.now
        )
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_event_id, minute, period, event_type FROM gh_match_events "
                "ORDER BY external_event_id"
            ))
        self.assertEqual(4, len(rows))
        self.assertEqual("first_half", rows[0]["period"])
        self.assertEqual("et_second", rows[3]["period"])

    def test_import_is_idempotent(self):
        dl = self._download("github_wc2026_events", "match_events.csv")
        import_github_wc2026_download(self.repo, dl, self.now)
        import_github_wc2026_download(self.repo, dl, self.now)
        with self.repo.session() as con:
            count = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, count)

    def test_import_matches(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_matches", "matches_detailed.csv"), self.now
        )
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_match_id, home_score, referee_name FROM gh_matches ORDER BY external_match_id"
            ))
        self.assertEqual(2, len(rows))
        self.assertEqual(2, rows[0]["home_score"])
        self.assertEqual("Szymon Marciniak", rows[0]["referee_name"])
        self.assertIsNone(rows[1]["home_score"])

    def test_import_all_seven_providers_end_to_end(self):
        provider_files = [
            ("github_wc2026_events", "match_events.csv"),
            ("github_wc2026_team_stats", "match_team_stats.csv"),
            ("github_wc2026_lineups", "match_lineups.csv"),
            ("github_wc2026_matches", "matches_detailed.csv"),
            ("github_wc2026_referees", "referees.csv"),
            ("github_wc2026_player_stats", "player_stats.csv"),
            ("github_wc2026_teams", "teams.csv"),
        ]
        for provider_id, filename in provider_files:
            import_github_wc2026_download(self.repo, self._download(provider_id, filename), self.now)
        with self.repo.session() as con:
            for table, expected in [
                ("gh_match_events", 4),
                ("gh_match_team_stats", 2),
                ("gh_match_lineups", 2),
                ("gh_matches", 2),
                ("gh_referees", 2),
                ("gh_player_stats", 2),
                ("gh_teams", 2),
            ]:
                self.assertEqual(
                    expected, con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    f"unexpected count in {table}",
                )

    def test_team_id_resolved_via_fifa_code(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_teams", "teams.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_teams", self.now.isoformat())
        with self.repo.session() as con:
            row = con.execute(
                "SELECT team_id FROM gh_teams WHERE external_team_id=1"
            ).fetchone()
        self.assertEqual(1, row["team_id"])

    def test_events_team_id_resolved_after_teams_imported(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_teams", "teams.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_teams", self.now.isoformat())
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_events", "match_events.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_events", self.now.isoformat())
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_team_id, team_id FROM gh_match_events ORDER BY external_event_id"
            ))
        self.assertEqual(1, rows[0]["team_id"])
