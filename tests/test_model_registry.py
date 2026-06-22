import unittest

from wcpredict.model_registry import market_model_policy
from wcpredict.models import MarketFamily


class ModelRegistryTests(unittest.TestCase):
    def test_market_families_have_specific_active_and_challenger_models(self):
        self.assertEqual("unified_1x2_blend", market_model_policy(MarketFamily.MATCH_RESULT).active)
        self.assertEqual("score_matrix", market_model_policy(MarketFamily.MATCH_RESULT).fallback)
        self.assertEqual("dynamic_dixon_coles", market_model_policy(MarketFamily.GOALS).challenger)
        self.assertEqual("negative_binomial", market_model_policy(MarketFamily.CORNERS).active)
        self.assertEqual("negative_binomial", market_model_policy(MarketFamily.CARDS).active)
        self.assertEqual("conditional_binomial", market_model_policy(MarketFamily.SHOTS_ON_TARGET).challenger)
        self.assertEqual("rare_event_logistic", market_model_policy("red_cards").active)
        self.assertEqual("exposure_count", market_model_policy(MarketFamily.PLAYER_GOAL).active)

    def test_policy_exposes_validation_and_fallback_instead_of_claiming_untrained_model(self):
        policy = market_model_policy(MarketFamily.CORNERS)
        self.assertEqual("poisson", policy.fallback)
        self.assertIn("log", policy.validation_metric.casefold())
        self.assertTrue(policy.required_features)


if __name__ == "__main__":
    unittest.main()
