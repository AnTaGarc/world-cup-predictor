import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.match_phases import MatchPhaseResultInput, ShootoutKickInput
from wcpredict.penalty_context_cache import repository_penalty_input_fingerprint
from wcpredict.penalty_profiles import build_goalkeeper_profile, build_player_profile
from wcpredict.repository import Repository


class TournamentPenaltyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.spain = self.repo.upsert_team("Spain")
        self.germany = self.repo.upsert_team("Germany")
        self.portugal = self.repo.upsert_team("Portugal")
        self.kickoff = datetime(2026, 6, 30, 18, tzinfo=timezone.utc)
        self.match_id = self.repo.upsert_match(
            "FIFA World Cup 2026", "Round of 32", self.kickoff,
            self.spain, self.germany, "scheduled",
        )
        self.future_id = self.repo.upsert_match(
            "FIFA World Cup 2026", "Round of 16", self.kickoff + timedelta(days=5),
            self.spain, self.portugal, "scheduled",
        )
        self.spain_takers = [self._player(f"Spain Taker {i}", self.spain, "FW") for i in range(3)]
        self.germany_takers = [self._player(f"Germany Taker {i}", self.germany, "FW") for i in range(3)]
        self.spain_keeper = self._player("Spain Keeper", self.spain, "GK")
        self.germany_keeper = self._player("Germany Keeper", self.germany, "GK")

    def tearDown(self):
        self.directory.cleanup()

    def _player(self, name: str, team_id: int, position: str) -> int:
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO players(name, team_id, position) VALUES(?, ?, ?)",
                (name, team_id, position),
            )
            return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _kicks(self):
        rows = []
        for index in range(3):
            rows.append(ShootoutKickInput(
                len(rows) + 1, self.spain, self.spain_takers[index], self.germany_keeper, "scored"
            ))
            rows.append(ShootoutKickInput(
                len(rows) + 1, self.germany, self.germany_takers[index], self.spain_keeper,
                "off_target_or_woodwork",
            ))
        return tuple(rows)

    def test_active_shootout_is_available_only_to_later_matches(self):
        phase = MatchPhaseResultInput(1, 1, 0, 0, 3, 0, "shootout")
        future_match = self.repo.get_match(self.future_id)
        before = repository_penalty_input_fingerprint(self.repo, future_match)

        self.repo.settle_knockout_match_versioned(
            self.match_id, phase, self._kicks(), None, self.kickoff + timedelta(hours=3)
        )

        same_match = self.repo.list_penalty_evidence(("Spain", "Germany"), self.kickoff)
        later = self.repo.list_penalty_evidence(
            ("Spain", "Germany"), self.kickoff + timedelta(days=1)
        )
        after = repository_penalty_input_fingerprint(self.repo, future_match)
        self.assertEqual([], same_match)
        self.assertEqual(6, len(later))
        self.assertNotEqual(before, after)

    def test_off_target_counts_as_faced_but_not_as_goalkeeper_save(self):
        phase = MatchPhaseResultInput(1, 1, 0, 0, 3, 0, "shootout")
        self.repo.settle_knockout_match_versioned(
            self.match_id, phase, self._kicks(), None, self.kickoff + timedelta(hours=3)
        )

        evidence = self.repo.list_penalty_evidence(
            ("Spain", "Germany"), self.kickoff + timedelta(days=1)
        )
        keeper = build_goalkeeper_profile({"player_name": "Spain Keeper"}, evidence)
        taker = build_player_profile(
            "Germany Taker 0", "FW", evidence, (self.kickoff + timedelta(days=1)).date()
        )
        with self.repo.session() as con:
            transfermarkt_rows = con.execute("SELECT COUNT(*) FROM penalty_attempts").fetchone()[0]

        self.assertEqual(3, keeper.faced_penalties)
        self.assertEqual(0, sum(row["outcome"] == "saved" and row["goalkeeper_name"] == "Spain Keeper" for row in evidence))
        self.assertEqual(1, taker.attempts)
        self.assertEqual(0, transfermarkt_rows)

    def test_day_first_historical_dates_respect_pre_match_cutoff(self):
        common = {
            "player_name": "Spain Taker 0",
            "team_name": "Spain",
            "outcome": "scored",
            "source_provider": "transfermarkt",
            "source_url": "https://example.test/penalties",
            "fetched_at_utc": self.kickoff.isoformat(),
        }
        self.repo.save_penalty_attempts([
            {**common, "attempted_on": "27/06/2026", "source_row_key": "before"},
            {**common, "attempted_on": "01/07/2026", "source_row_key": "after"},
        ])

        evidence = self.repo.list_penalty_evidence(("Spain",), self.kickoff)

        self.assertEqual(["before"], [row["source_row_key"] for row in evidence])


if __name__ == "__main__":
    unittest.main()
