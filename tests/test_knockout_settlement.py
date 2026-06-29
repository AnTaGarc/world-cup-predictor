import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.knockout_bracket import _decide_winner
from wcpredict.match_phases import MatchPhaseResultInput, ShootoutKickInput
from wcpredict.repository import Repository


class KnockoutSettlementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.team_a = self.repo.upsert_team("Spain")
        self.team_b = self.repo.upsert_team("Germany")
        self.kickoff = datetime(2026, 6, 30, 18, tzinfo=timezone.utc)
        self.match_id = self.repo.upsert_match(
            "FIFA World Cup 2026", "Round of 32", self.kickoff,
            self.team_a, self.team_b, "scheduled",
        )
        self.players_a = [self._player(f"Spain Player {index}", self.team_a, "FW") for index in range(4)]
        self.players_b = [self._player(f"Germany Player {index}", self.team_b, "FW") for index in range(4)]
        self.keeper_a = self._player("Spain Keeper", self.team_a, "GK")
        self.keeper_b = self._player("Germany Keeper", self.team_b, "GK")
        self.now = self.kickoff + timedelta(hours=3)

    def tearDown(self):
        self.directory.cleanup()

    def _player(self, name: str, team_id: int, position: str) -> int:
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO players(name, team_id, position) VALUES(?, ?, ?)",
                (name, team_id, position),
            )
            return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _kicks(self, winner: str) -> tuple[ShootoutKickInput, ...]:
        a_outcome, b_outcome = (("scored", "saved") if winner == "a" else ("saved", "scored"))
        rows = []
        for round_index in range(3):
            rows.append(ShootoutKickInput(
                len(rows) + 1, self.team_a, self.players_a[round_index], self.keeper_b, a_outcome
            ))
            rows.append(ShootoutKickInput(
                len(rows) + 1, self.team_b, self.players_b[round_index], self.keeper_a, b_outcome
            ))
        return tuple(rows)

    def test_extra_time_keeps_official_score_but_training_uses_regulation(self):
        phase = MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time")

        settlement_id = self.repo.settle_knockout_match_versioned(
            self.match_id, phase, (), None, self.now
        )

        result = self.repo.get_match_result(self.match_id)
        phase_row = self.repo.get_active_match_phase_result(self.match_id)
        local = self.repo.list_match_results_before(self.kickoff + timedelta(days=1))
        with self.repo.session() as con:
            historical = con.execute(
                "SELECT goals_a, goals_b FROM historical_matches "
                "WHERE source_id='reviewed_settlement' AND source_row_key=?",
                (str(self.match_id),),
            ).fetchone()
        self.assertGreater(settlement_id, 0)
        self.assertEqual((2, 1), (result["goals_a"], result["goals_b"]))
        self.assertEqual("extra_time", phase_row["decided_in"])
        self.assertEqual((1, 1), tuple(historical))
        self.assertEqual((1, 1), (local[0].goals_a, local[0].goals_b))

    def test_shootout_correction_replaces_active_kicks_without_duplicates(self):
        first_phase = MatchPhaseResultInput(1, 1, 0, 0, 3, 0, "shootout")
        second_phase = MatchPhaseResultInput(1, 1, 0, 0, 0, 3, "shootout")

        first_id = self.repo.settle_knockout_match_versioned(
            self.match_id, first_phase, self._kicks("a"), None, self.now
        )
        second_id = self.repo.settle_knockout_match_versioned(
            self.match_id, second_phase, self._kicks("b"), None, self.now + timedelta(minutes=5)
        )

        active = self.repo.get_active_match_phase_result(self.match_id)
        kicks = self.repo.list_active_shootout_kicks(self.match_id)
        with self.repo.session() as con:
            versions = con.execute(
                "SELECT id, active FROM settlement_versions WHERE match_id=? ORDER BY version",
                (self.match_id,),
            ).fetchall()
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(2, len(versions))
        self.assertEqual([0, 1], [row["active"] for row in versions])
        self.assertEqual((0, 3), (active["shootout_goals_a"], active["shootout_goals_b"]))
        self.assertEqual(6, len(kicks))
        self.assertTrue(all(row["settlement_version_id"] == second_id for row in kicks))

    def test_phase_result_decides_shootout_winner(self):
        row = {
            "team_a_id": self.team_a,
            "team_b_id": self.team_b,
            "goals_a": 1,
            "goals_b": 1,
            "extra_time_team_a_goals": None,
            "extra_time_team_b_goals": None,
            "penalty_team_a": None,
            "penalty_team_b": None,
            "decided_in": "shootout",
            "regulation_goals_a": 1,
            "regulation_goals_b": 1,
            "phase_extra_time_goals_a": 0,
            "phase_extra_time_goals_b": 0,
            "shootout_goals_a": 5,
            "shootout_goals_b": 4,
        }

        self.assertEqual(self.team_a, _decide_winner(row))


if __name__ == "__main__":
    unittest.main()
