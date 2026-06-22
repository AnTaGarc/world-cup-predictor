import unittest

from wcpredict.market_math import expected_value, fair_odds, implied_probability


class MarketMathTests(unittest.TestCase):
    def test_expected_value_for_decimal_odds(self):
        self.assertAlmostEqual(0.08, expected_value(0.48, 2.25), places=6)

    def test_implied_probability(self):
        self.assertAlmostEqual(0.5, implied_probability(2.0), places=6)

    def test_fair_odds(self):
        self.assertAlmostEqual(2.0833333333, fair_odds(0.48), places=6)

    def test_rejects_invalid_probability(self):
        with self.assertRaises(ValueError):
            expected_value(1.2, 2.0)

    def test_rejects_invalid_decimal_odds(self):
        with self.assertRaises(ValueError):
            implied_probability(1.0)


if __name__ == "__main__":
    unittest.main()
