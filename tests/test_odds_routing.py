import unittest

from wcpredict.odds_routing import route_world_cup_odds


class OddsRoutingTests(unittest.TestCase):
    def test_zero_budget_skips_exact_feed_before_network(self):
        decision = route_world_cup_odds("FIFA World Cup", 0)
        self.assertEqual(0, decision.max_credits)
        self.assertEqual("skipped_zero_budget", decision.exact_status)

    def test_world_cup_uses_validated_soccer_key(self):
        decision = route_world_cup_odds("FIFA World Cup", 10)
        self.assertEqual("soccer_fifa_world_cup", decision.the_odds_sport_key)
        self.assertEqual("enabled", decision.exact_status)


if __name__ == "__main__":
    unittest.main()
