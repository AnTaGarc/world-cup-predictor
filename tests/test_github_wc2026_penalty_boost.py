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


class ContextInjectionTests(unittest.TestCase):
    """Tournament evidence must reach attempts and deep_rates with a strict
    temporal cutoff at the match kickoff."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc).isoformat()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'England', 'ENG')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status) VALUES(200, 'FIFA World Cup 2026', 'Round of 16', "
                "'2026-07-05T17:00:00+00:00', 1, 2, 'scheduled')"
            )

    def tearDown(self):
        self.directory.cleanup()

    def _seed_player_stats(self, last_verified: str) -> None:
        with self.repo.session() as con:
            con.execute("DELETE FROM gh_player_stats")
            con.execute(
                "INSERT INTO gh_player_stats(provider_id, external_player_id, team_id, "
                "player_name, position, penalty_goals, saves, goals_conceded, "
                "last_verified, imported_at_utc) VALUES"
                "('github_wc2026_player_stats', 16, 1, 'Quinones', 'FWD', 1, NULL, NULL, ?, ?),"
                "('github_wc2026_player_stats', 1, 1, 'Rangel', 'GK', 0, 5, 1, ?, ?)",
                (last_verified, self.now, last_verified, self.now),
            )

    def _inputs(self):
        from wcpredict.penalty_context_cache import _repository_inputs
        match = next(m for m in self.repo.list_matches() if m.id == 200)
        return _repository_inputs(self.repo, match)

    def test_tournament_evidence_reaches_attempts_and_deep_rates(self):
        self._seed_player_stats("2026-07-01")
        _squads, _lineups, deep_rates, attempts, _gk_attempts, _cov = self._inputs()
        synthetic = [a for a in attempts if a.get("source_provider") == "github_wc2026"]
        self.assertEqual(1, len(synthetic))
        self.assertEqual("scored", synthetic[0]["outcome"])
        self.assertAlmostEqual(5 / 6, deep_rates.get("Rangel"))

    def test_evidence_after_kickoff_is_excluded(self):
        self._seed_player_stats("2026-07-06")
        _squads, _lineups, deep_rates, attempts, _gk_attempts, _cov = self._inputs()
        synthetic = [a for a in attempts if a.get("source_provider") == "github_wc2026"]
        self.assertEqual([], synthetic)
        self.assertNotIn("Rangel", deep_rates)
