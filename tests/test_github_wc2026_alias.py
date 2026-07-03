import tempfile
import unittest
from pathlib import Path

from wcpredict.repository import Repository


class GhwcAliasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO teams(id, name, fifa_code) VALUES(?, ?, ?)",
                (1, "Mexico", "MEX"),
            )
            con.execute(
                "INSERT INTO teams(id, name, fifa_code) VALUES(?, ?, ?)",
                (2, "Cape Verde", "CPV"),
            )
        self.now = "2026-07-03T12:00:00+00:00"

    def tearDown(self):
        self.directory.cleanup()

    def test_reconcile_by_fifa_code_exact_case_insensitive(self):
        internal_id = self.repo.reconcile_gh_team(1, "México", "mex", self.now)
        self.assertEqual(1, internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by, internal_id FROM entity_alias_map "
                "WHERE entity_type='team' AND source_key='github_wc2026' AND external_id='1'"
            ).fetchone()
        self.assertEqual("auto_exact_match", row["confirmed_by"])
        self.assertEqual(1, row["internal_id"])

    def test_reconcile_by_normalized_name(self):
        internal_id = self.repo.reconcile_gh_team(55, "Cabo Verde", None, self.now)
        self.assertEqual(2, internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by FROM entity_alias_map "
                "WHERE entity_type='team' AND external_id='55'"
            ).fetchone()
        self.assertEqual("auto_normalized", row["confirmed_by"])

    def test_unresolved_returns_none_without_insert(self):
        internal_id = self.repo.reconcile_gh_team(999, "Atlantis", "ATL", self.now)
        self.assertIsNone(internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT * FROM entity_alias_map WHERE external_id='999'"
            ).fetchone()
        self.assertIsNone(row)

    def test_list_pending_aliases_for_teams(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO gh_teams(provider_id, external_team_id, team_name, imported_at_utc) "
                "VALUES('github_wc2026_teams', 77, 'Freedonia', ?)",
                (self.now,),
            )
        pending = self.repo.list_pending_aliases("team")
        self.assertEqual(1, len(pending))
        self.assertEqual("77", pending[0]["external_id"])
        self.assertEqual("Freedonia", pending[0]["display_name"])

    def test_confirm_alias(self):
        self.repo.confirm_alias("team", "github_wc2026", "77", 2, "anton", self.now)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by, internal_id FROM entity_alias_map WHERE external_id='77'"
            ).fetchone()
        self.assertEqual("user:anton", row["confirmed_by"])
        self.assertEqual(2, row["internal_id"])
