import unittest
from types import SimpleNamespace

from wcpredict.knockout_audit import (
    build_knockout_snapshot_section,
    evaluate_knockout_snapshot,
)
from wcpredict.knockout_model import predict_knockout_match
from wcpredict.match_phases import MatchPhaseResultInput


class KnockoutAuditTests(unittest.TestCase):
    def setUp(self):
        prediction = predict_knockout_match(
            1.4, 1.1, extra_time_xg=(0.52, 0.28), home_penalty_win_probability=0.60
        )
        penalty_context = SimpleNamespace(
            team_a_shootout_win_probability=0.60,
            team_b_shootout_win_probability=0.40,
            player_rows=(
                SimpleNamespace(
                    player_name="Spain Taker",
                    team_name="Spain",
                    conversion=0.80,
                    on_field_probability=0.75,
                    first_five_probability=0.65,
                ),
            ),
            coverage=SimpleNamespace(squad_players=52, players_with_history=20, attempts=140),
        )
        self.snapshot = {
            "team_a": "Spain",
            "team_b": "Germany",
            "primary": [
                {"selection_name": "Spain", "probability": 0.50},
                {"selection_name": "Draw", "probability": 0.30},
                {"selection_name": "Germany", "probability": 0.20},
            ],
            "knockout": build_knockout_snapshot_section(
                prediction, (0.52, 0.28), penalty_context
            ),
        }

    def test_regulation_only_marks_later_phases_not_played(self):
        result = MatchPhaseResultInput(2, 0, None, None, None, None, "regulation")

        audit = evaluate_knockout_snapshot(self.snapshot, result, ())

        self.assertEqual("played", audit.regulation.status)
        self.assertEqual("not_played", audit.extra_time.status)
        self.assertEqual("not_played", audit.shootout.status)

    def test_extra_time_audit_uses_only_goals_scored_in_extra_time(self):
        result = MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time")

        audit = evaluate_knockout_snapshot(self.snapshot, result, ())

        self.assertEqual("1-0", audit.extra_time.actual_score)
        self.assertEqual("home", audit.extra_time.actual_outcome)
        self.assertAlmostEqual(
            self.snapshot["knockout"]["extra_time"]["conditional"]["home"],
            audit.extra_time.observed_probability,
        )

    def test_shootout_audit_scores_winner_and_each_kick(self):
        result = MatchPhaseResultInput(1, 1, 0, 0, 1, 0, "shootout")
        kicks = (
            {"player_name": "Spain Taker", "team_name": "Spain", "outcome": "scored"},
            {"player_name": "Unknown", "team_name": "Germany", "outcome": "saved"},
        )

        audit = evaluate_knockout_snapshot(self.snapshot, result, kicks)

        self.assertEqual("home", audit.shootout.actual_outcome)
        self.assertAlmostEqual((0.60 - 1.0) ** 2, audit.shootout.brier)
        self.assertEqual(2, len(audit.shootout.rows))
        self.assertAlmostEqual((0.80 - 1.0) ** 2, audit.shootout.rows[0]["brier"])
        self.assertAlmostEqual((0.76 - 0.0) ** 2, audit.shootout.rows[1]["brier"])

    def test_snapshot_keeps_conditional_extra_time_and_penalty_probabilities(self):
        knockout = self.snapshot["knockout"]

        self.assertEqual([0.52, 0.28], knockout["extra_time"]["expected_xg"])
        self.assertAlmostEqual(1.0, sum(knockout["extra_time"]["conditional"].values()))
        self.assertEqual({"home": 0.60, "away": 0.40}, knockout["shootout"]["conditional"])


if __name__ == "__main__":
    unittest.main()
