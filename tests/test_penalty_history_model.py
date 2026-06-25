import unittest

from wcpredict.knockout_model import predict_knockout_match
from wcpredict.penalty_history_model import build_penalty_match_context


class PenaltyHistoryModelTests(unittest.TestCase):
    def test_no_penalty_history_keeps_shootout_symmetric(self):
        context = build_penalty_match_context("Canada", "South Africa", [])
        self.assertAlmostEqual(0.5, context.team_a_shootout_win_probability, places=6)
        self.assertIn("Sin penalty_history", context.explanation)

    def test_team_conversion_moves_shootout_probability_but_is_capped(self):
        attempts = []
        for idx in range(10):
            attempts.append({
                "team_name": "Canada",
                "outcome": "scored",
                "attempted_on": f"2026-06-{10+idx:02d}",
                "source_row_key": f"canada-{idx}",
            })
        for idx in range(10):
            attempts.append({
                "team_name": "South Africa",
                "outcome": "missed" if idx < 6 else "scored",
                "attempted_on": f"2026-06-{10+idx:02d}",
                "source_row_key": f"sa-{idx}",
            })
        context = build_penalty_match_context("Canada", "South Africa", attempts)
        self.assertGreater(context.team_a_shootout_win_probability, 0.55)
        self.assertLessEqual(context.team_a_shootout_win_probability, 0.64)

    def test_knockout_model_accepts_penalty_history_probability(self):
        neutral = predict_knockout_match(1.2, 1.2)
        adjusted = predict_knockout_match(1.2, 1.2, home_penalty_win_probability=0.64)
        self.assertGreater(adjusted.home_advances, neutral.home_advances)
        self.assertAlmostEqual(adjusted.home_advances + adjusted.away_advances, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
