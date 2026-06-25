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

    def test_compare_draw_no_bet_odds_accounts_for_push_probability(self):
        row = compare_odds_to_probability(
            probability=0.60,
            decimal_odds=1.80,
            market_family=MarketFamily.DRAW_NO_BET,
            market_name="Draw No Bet",
            selection_name="Spain",
            confidence="medium",
            push_probability=0.25,
        )
        # Real EV per staked unit: p_win * odds + p_push - 1.
        self.assertAlmostEqual(0.33, row.expected_value, places=6)
        # Fair odds for refundable markets are lower than 1 / p_win because
        # push mass returns the stake.
        self.assertAlmostEqual(1.25, row.fair_odds, places=6)

    def test_parse_odds_csv(self):
        csv_text = "market_family,market_name,selection_name,line,decimal_odds,bookmaker\nmatch_result,1X2,Spain,,2.25,Winamax\n"
        odds = parse_odds_csv(csv_text, match_id=7, captured_at_utc=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(1, len(odds))
        self.assertEqual(MarketFamily.MATCH_RESULT, odds[0].market_family)
        self.assertEqual(2.25, odds[0].decimal_odds)


if __name__ == "__main__":
    unittest.main()
