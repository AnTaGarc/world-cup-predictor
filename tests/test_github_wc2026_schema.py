import tempfile
import unittest
from pathlib import Path

from wcpredict.repository import Repository


EXPECTED_TABLES = (
    "gh_match_events",
    "gh_match_team_stats",
    "gh_match_lineups",
    "gh_matches",
    "gh_referees",
    "gh_player_stats",
    "gh_teams",
    "entity_alias_map",
    "gh_score_verifications",
)


class GithubWc2026SchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()

    def tearDown(self):
        self.directory.cleanup()

    def test_all_expected_tables_are_present(self):
        with self.repo.session() as con:
            names = {
                row["name"] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        for table in EXPECTED_TABLES:
            self.assertIn(table, names)

    def test_initialize_is_idempotent(self):
        self.repo.initialize()
        self.repo.initialize()
        with self.repo.session() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='gh_matches'"
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_gh_match_events_has_expected_columns(self):
        with self.repo.session() as con:
            columns = {
                row["name"] for row in con.execute("PRAGMA table_info(gh_match_events)")
            }
        for column in (
            "provider_id", "external_event_id", "external_match_id", "match_id",
            "minute", "period", "event_type", "external_team_id", "team_id",
            "external_player_id", "player_id", "provider_version", "imported_at_utc",
        ):
            self.assertIn(column, columns)
