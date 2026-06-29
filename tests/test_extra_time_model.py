import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.extra_time_model import adjust_extra_time_xg
from wcpredict.knockout_model import EXTRA_TIME_FRACTION, predict_knockout_match
from wcpredict.match_phases import MatchPhaseResultInput
from wcpredict.repository import Repository


class ExtraTimeModelTests(unittest.TestCase):
    def setUp(self):
        self.as_of = datetime(2026, 7, 5, 18, tzinfo=timezone.utc)

    def test_no_history_keeps_current_extra_time_fraction(self):
        result = adjust_extra_time_xg("Spain", "Germany", 1.5, 1.2, [], self.as_of)

        self.assertAlmostEqual(1.5 * EXTRA_TIME_FRACTION, result.adjusted_xg[0])
        self.assertAlmostEqual(1.2 * EXTRA_TIME_FRACTION, result.adjusted_xg[1])
        self.assertEqual((0, 0), (result.sample_a, result.sample_b))

    def test_one_extreme_sample_is_strongly_shrunk(self):
        rows = [{
            "kickoff_utc": "2026-07-01T18:00:00+00:00",
            "team_name": "Spain",
            "opponent_name": "Germany",
            "regulation_xg": 1.5,
            "extra_time_xg": 2.0,
            "extra_time_goals": 3,
        }]

        result = adjust_extra_time_xg("Spain", "Portugal", 1.5, 1.2, rows, self.as_of)

        self.assertLessEqual(result.factor_a, 1.10)
        self.assertEqual(1, result.sample_a)

    def test_extra_time_adjustment_does_not_change_regulation_probabilities(self):
        base = predict_knockout_match(1.5, 1.2)
        adjusted = predict_knockout_match(1.5, 1.2, extra_time_xg=(0.60, 0.25))

        self.assertEqual(base.home_wins_90, adjusted.home_wins_90)
        self.assertEqual(base.away_wins_90, adjusted.away_wins_90)
        self.assertEqual(base.p_draw_90, adjusted.p_draw_90)
        self.assertNotEqual(
            base.cond_home_wins_et_given_draw_90,
            adjusted.cond_home_wins_et_given_draw_90,
        )

    def test_repository_returns_only_prior_active_extra_time_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            spain = repo.upsert_team("Spain")
            germany = repo.upsert_team("Germany")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Round of 32", self.as_of - timedelta(days=2),
                spain, germany, "scheduled",
            )
            with repo.session() as con:
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, xg, source_id) VALUES(?, ?, 1.50, 'test')",
                    (match_id, spain),
                )
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, xg, source_id) VALUES(?, ?, 1.20, 'test')",
                    (match_id, germany),
                )
                for team_id, xg in ((spain, 0.30), (germany, 0.15)):
                    con.execute(
                        "INSERT INTO team_match_period_stats("
                        "match_id, team_id, period, xg, source_id, content_sha256, observed_at_utc"
                        ") VALUES(?, ?, 'extra_time_first', ?, 'test', 'hash', ?)",
                        (match_id, team_id, xg, self.as_of.isoformat()),
                    )
            repo.settle_knockout_match_versioned(
                match_id,
                MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
                (),
                None,
                self.as_of - timedelta(days=2) + timedelta(hours=3),
            )

            rows = repo.list_extra_time_training_rows_before(self.as_of)

        self.assertEqual(2, len(rows))
        spain_row = next(row for row in rows if row["team_name"] == "Spain")
        self.assertAlmostEqual(0.30, spain_row["extra_time_xg"])
        self.assertEqual(1, spain_row["extra_time_goals"])


if __name__ == "__main__":
    unittest.main()
