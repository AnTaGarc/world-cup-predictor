import unittest

from wcpredict.models import MarketFamily
from wcpredict.quality import Confidence
from wcpredict.services import MarketPrediction
from wcpredict.ui.interaction_models import (
    evaluate_odds_rows,
    localized_default_odds_rows,
)


class OddsInteractionTests(unittest.TestCase):
    def setUp(self):
        self.predictions = [
            MarketPrediction(
                MarketFamily.MATCH_RESULT,
                "1X2",
                "Spain",
                None,
                0.50,
                Confidence.HIGH,
                "test",
            ),
            MarketPrediction(
                MarketFamily.MATCH_RESULT,
                "1X2",
                "Draw",
                None,
                0.25,
                Confidence.HIGH,
                "test",
            ),
            MarketPrediction(
                MarketFamily.DRAW_NO_BET,
                "Draw No Bet",
                "Spain",
                None,
                2 / 3,
                Confidence.HIGH,
                "test",
            ),
        ]

    def test_default_rows_are_localized_without_changing_canonical_source(self):
        rows = localized_default_odds_rows("Spain", "Japan")

        self.assertEqual("Resultado del partido", rows[0]["market_family"])
        self.assertEqual("1X2", rows[0]["market_name"])
        self.assertEqual("Spain", rows[0]["selection_name"])

    def test_entered_odds_preserve_current_ev_and_draw_no_bet_push_math(self):
        edited = [
            {
                "market_family": "Resultado del partido",
                "market_name": "1X2",
                "selection_name": "Spain",
                "line": None,
                "decimal_odds": 2.20,
                "bookmaker": "Winamax",
            },
            {
                "market_family": "Empate no válido",
                "market_name": "Empate no válido",
                "selection_name": "Spain",
                "line": None,
                "decimal_odds": 1.80,
                "bookmaker": "Winamax",
            },
        ]

        result = evaluate_odds_rows(self.predictions, edited)

        self.assertEqual(2, len(result.entered))
        self.assertEqual(2, len(result.comparisons))
        self.assertAlmostEqual(0.10, result.comparisons[0].expected_value)
        self.assertAlmostEqual(0.50, result.comparisons[1].probability)
        self.assertAlmostEqual(0.15, result.comparisons[1].expected_value)


if __name__ == "__main__":
    unittest.main()
