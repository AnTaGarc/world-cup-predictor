import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


class TournamentEvidenceQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc).isoformat()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute(
                "INSERT INTO gh_player_stats(provider_id, external_player_id, team_id, "
                "player_name, position, penalty_goals, saves, goals_conceded, "
                "last_verified, imported_at_utc) VALUES"
                "('github_wc2026_player_stats', 16, 1, 'Quinones', 'FWD', 2, NULL, NULL, '2026-07-01', ?),"
                "('github_wc2026_player_stats', 1, 1, 'Rangel', 'GK', 0, 5, 1, '2026-07-01', ?),"
                "('github_wc2026_player_stats', 99, 1, 'Nadie', 'MID', 0, NULL, NULL, '2026-07-01', ?)",
                (now, now, now),
            )

    def tearDown(self):
        self.directory.cleanup()

    def test_penalty_evidence_only_returns_scorers_of_selected_teams(self):
        rows = self.repo.list_gh_tournament_penalty_evidence(
            ("Mexico",), "2026-07-04T17:00:00+00:00"
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("Quinones", rows[0]["player_name"])
        self.assertEqual(2, rows[0]["penalty_goals"])

    def test_penalty_evidence_other_team_returns_empty(self):
        self.assertEqual(
            [], self.repo.list_gh_tournament_penalty_evidence(("Brazil",), "2026-07-04T17:00:00+00:00")
        )

    def test_penalty_evidence_respects_cutoff(self):
        self.assertEqual(
            [], self.repo.list_gh_tournament_penalty_evidence(("Mexico",), "2026-06-30T17:00:00+00:00")
        )

    def test_goalkeeper_rates_returns_only_gk(self):
        rows = self.repo.list_gh_goalkeeper_tournament_rates(("Mexico",))
        self.assertEqual(1, len(rows))
        self.assertEqual("Rangel", rows[0]["player_name"])
        self.assertEqual(5, rows[0]["saves"])
