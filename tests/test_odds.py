from datetime import datetime, timezone
import unittest

from wcpredict.models import MarketFamily
from wcpredict.odds import compare_odds_to_probability, parse_odds_csv


class OddsTests(unittest.TestCase):
    def test_compare_odds_to_probability(self):
        row = compare_odds_to_probability(
            probability=0.48,
            decimal_odds=2.25,
            market_family=MarketFamily.MATCH_RESULT,
            market_name="1X2",
            selection_name="Spain",
            confidence="medium",
        )
        self.assertAlmostEqual(0.08, row.expected_value, places=6)
        self.assertAlmostEqual(2.0833333333, row.fair_odds, places=6)

    def test_parse_odds_csv(self):
        csv_text = "market_family,market_name,selection_name,line,decimal_odds,bookmaker\nmatch_result,1X2,Spain,,2.25,Winamax\n"
        odds = parse_odds_csv(csv_text, match_id=7, captured_at_utc=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(1, len(odds))
        self.assertEqual(MarketFamily.MATCH_RESULT, odds[0].market_family)
        self.assertEqual(2.25, odds[0].decimal_odds)


if __name__ == "__main__":
    unittest.main()
