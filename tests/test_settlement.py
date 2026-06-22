import unittest

from wcpredict.settlement import prediction_occurred


class SettlementTests(unittest.TestCase):
    def test_resolves_core_market_outcomes(self):
        base = {"team_a": "Canada", "team_b": "Qatar", "goals_a": 2, "goals_b": 1}
        self.assertTrue(prediction_occurred({"market_name": "1X2", "selection_name": "Canada", "line": None}, **base))
        self.assertFalse(prediction_occurred({"market_name": "1X2", "selection_name": "Draw", "line": None}, **base))
        self.assertTrue(prediction_occurred({"market_name": "Over/Under 2.5", "selection_name": "Over 2.5", "line": 2.5}, **base))
        self.assertTrue(prediction_occurred({"market_name": "Both Teams To Score", "selection_name": "Yes", "line": None}, **base))
        self.assertTrue(prediction_occurred({"market_name": "Double Chance", "selection_name": "Canada or Draw", "line": None}, **base))

    def test_unsupported_market_returns_none(self):
        self.assertIsNone(prediction_occurred({"market_name": "Player shots", "selection_name": "A", "line": 1.5}, "Canada", "Qatar", 2, 1))


if __name__ == "__main__":
    unittest.main()
