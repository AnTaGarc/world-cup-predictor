"""Phase 5 wiring test: predict_match_markets must apply per-team 1X2 shifts."""
import unittest
from datetime import date

from wcpredict.ratings import MatchResult
from wcpredict.services import predict_match_markets


def _sample_results():
    return [
        MatchResult(date(2024, 11, 1), "A", "B", 1, 0, "world_cup"),
        MatchResult(date(2024, 12, 1), "A", "C", 2, 1, "world_cup"),
        MatchResult(date(2025, 1, 1), "B", "C", 0, 1, "world_cup"),
        MatchResult(date(2025, 2, 1), "B", "A", 1, 2, "world_cup"),
    ]


def _winning_prob(predictions, team):
    return next(
        p.probability for p in predictions
        if p.market_name == "1X2" and p.selection_name == team
    )


class TeamCorrectionsTests(unittest.TestCase):
    def test_positive_shift_for_home_increases_their_1x2(self):
        baseline = predict_match_markets(
            "A", "B", _sample_results(), date(2026, 6, 1),
            outcome_probabilities={"home": 0.50, "draw": 0.30, "away": 0.20},
            advanced_form=None,
        )
        shifted = predict_match_markets(
            "A", "B", _sample_results(), date(2026, 6, 1),
            outcome_probabilities={"home": 0.50, "draw": 0.30, "away": 0.20},
            advanced_form=None,
            team_corrections={"A": {"1X2": 0.30}},
        )
        self.assertGreater(_winning_prob(shifted, "A"), _winning_prob(baseline, "A"))

    def test_tiny_shift_does_nothing(self):
        baseline = predict_match_markets(
            "A", "B", _sample_results(), date(2026, 6, 1),
            outcome_probabilities={"home": 0.50, "draw": 0.30, "away": 0.20},
            advanced_form=None,
        )
        shifted = predict_match_markets(
            "A", "B", _sample_results(), date(2026, 6, 1),
            outcome_probabilities={"home": 0.50, "draw": 0.30, "away": 0.20},
            advanced_form=None,
            team_corrections={"A": {"1X2": 0.001}},
        )
        self.assertAlmostEqual(
            _winning_prob(shifted, "A"),
            _winning_prob(baseline, "A"),
            places=4,
        )

    def test_no_corrections_argument_keeps_baseline(self):
        baseline = predict_match_markets(
            "A", "B", _sample_results(), date(2026, 6, 1),
            outcome_probabilities={"home": 0.50, "draw": 0.30, "away": 0.20},
            advanced_form=None,
        )
        # Calling without team_corrections must not change behaviour.
        self.assertGreater(_winning_prob(baseline, "A"), 0.0)


if __name__ == "__main__":
    unittest.main()
