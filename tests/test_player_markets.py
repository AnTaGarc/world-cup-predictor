import unittest

from wcpredict.models import MarketFamily
from wcpredict.player_markets import (
    DEFAULT_OPPONENT_SOT_PER90,
    PlayerAssumption,
    derive_player_assumption,
    estimate_player_market_probability,
    is_goalkeeper,
)
from wcpredict.quality import Confidence


class PlayerMarketTests(unittest.TestCase):
    def test_player_shots_probability_scales_by_minutes(self):
        assumption = PlayerAssumption(
            player_name="Example Forward",
            team_name="Spain",
            expected_minutes=75,
            starter_probability=0.85,
            per90_rate=2.4,
            opponent_adjustment=0.95,
            manually_estimated=False,
        )
        estimate = estimate_player_market_probability(assumption, MarketFamily.PLAYER_SHOTS, line=1.5, sample_size=12)
        self.assertGreater(estimate.probability, 0.4)
        self.assertEqual(Confidence.LOW, estimate.confidence)

    def test_missing_minutes_is_not_estimable(self):
        assumption = PlayerAssumption(
            player_name="Example Midfielder",
            team_name="Japan",
            expected_minutes=None,
            starter_probability=0.5,
            per90_rate=1.0,
            opponent_adjustment=1.0,
            manually_estimated=True,
        )
        estimate = estimate_player_market_probability(assumption, MarketFamily.PLAYER_SHOTS, line=0.5, sample_size=0)
        self.assertEqual(Confidence.NOT_ESTIMABLE, estimate.confidence)
        self.assertIsNone(estimate.probability)

    def test_player_count_market_uses_dispersion_when_observed(self):
        assumption = PlayerAssumption(
            player_name="Forward", team_name="Spain", expected_minutes=90,
            starter_probability=1.0, per90_rate=3.0, opponent_adjustment=1.0,
            manually_estimated=False, dispersion=0.3,
        )
        estimate = estimate_player_market_probability(assumption, MarketFamily.PLAYER_SHOTS, 2.5, 20)
        self.assertEqual("negative_binomial", estimate.model_family)

    def test_expected_minutes_are_not_discounted_a_second_time_by_starter_probability(self):
        assumption = PlayerAssumption(
            player_name="Rotation Forward", team_name="Spain", expected_minutes=30,
            starter_probability=1 / 3, per90_rate=3.0, opponent_adjustment=1.0,
            manually_estimated=False,
        )
        estimate = estimate_player_market_probability(assumption, MarketFamily.PLAYER_SHOTS, 0.5, 12)
        self.assertAlmostEqual(0.632, estimate.probability, places=3)

    def test_player_assumption_is_derived_from_observed_stats(self):
        derived = derive_player_assumption(
            {
                "player_name": "Forward", "team_name": "Spain", "games": 4,
                "starts": 3, "minutes": 270, "shots": 9,
            },
            MarketFamily.PLAYER_SHOTS,
        )

        self.assertIsNotNone(derived)
        self.assertAlmostEqual(3.0, derived.assumption.per90_rate)
        self.assertEqual(68, derived.assumption.expected_minutes)
        self.assertAlmostEqual(4 / 6, derived.assumption.starter_probability)
        self.assertEqual(4, derived.sample_size)
        self.assertFalse(derived.assumption.manually_estimated)

    def test_player_assumption_shrinks_tiny_minute_per90_outliers(self):
        derived = derive_player_assumption(
            {
                "player_name": "Cameo Forward", "team_name": "Spain", "games": 1,
                "starts": 0, "minutes": 10, "shots": 1,
            },
            MarketFamily.PLAYER_SHOTS,
        )

        self.assertIsNotNone(derived)
        self.assertLess(derived.assumption.per90_rate, 3.0)
        self.assertIn("contraido", derived.explanation)

    def test_player_assumption_refuses_an_unobserved_metric(self):
        derived = derive_player_assumption(
            {"player_name": "Midfielder", "team_name": "Spain", "games": 3, "minutes": 240, "passes": None},
            MarketFamily.PLAYER_PASSES,
        )

        self.assertIsNone(derived)


class GoalkeeperMarketTests(unittest.TestCase):
    GOALKEEPER_ROW = {
        "player_name": "Matej Kovar", "team_name": "Czechia", "position": "GK",
        "games": 2, "starts": 2, "minutes": 180, "save_percentage": 77.8,
    }

    def test_is_goalkeeper_detects_common_position_strings(self):
        self.assertTrue(is_goalkeeper({"position": "GK"}))
        self.assertTrue(is_goalkeeper({"position": "Goalkeeper"}))
        self.assertTrue(is_goalkeeper({"position": "POR"}))
        self.assertFalse(is_goalkeeper({"position": "DF"}))
        self.assertFalse(is_goalkeeper({"position": None}))

    def test_saves_per_90_uses_save_percentage_and_opponent_sot(self):
        derived = derive_player_assumption(self.GOALKEEPER_ROW, MarketFamily.PLAYER_SAVES)
        # 77.8% save rate × 4 SOT/90 baseline = 3.11 saves/90
        self.assertAlmostEqual(0.778 * DEFAULT_OPPONENT_SOT_PER90, derived.assumption.per90_rate, places=3)
        self.assertIn("paradas/90", derived.explanation)
        # Custom opponent SOT (e.g. they face a strong attacking rival).
        stronger = derive_player_assumption(
            self.GOALKEEPER_ROW, MarketFamily.PLAYER_SAVES, opponent_sot_per90=6.5,
        )
        self.assertGreater(stronger.assumption.per90_rate, derived.assumption.per90_rate)

    def test_goals_conceded_per_90_inverts_save_percentage(self):
        derived = derive_player_assumption(self.GOALKEEPER_ROW, MarketFamily.PLAYER_GOALS_CONCEDED)
        self.assertAlmostEqual((1.0 - 0.778) * DEFAULT_OPPONENT_SOT_PER90, derived.assumption.per90_rate, places=3)
        self.assertIn("goles concedidos/90", derived.explanation)

    def test_saves_over_line_market_estimates_a_real_probability(self):
        derived = derive_player_assumption(self.GOALKEEPER_ROW, MarketFamily.PLAYER_SAVES)
        estimate = estimate_player_market_probability(
            derived.assumption, MarketFamily.PLAYER_SAVES, line=2.5, sample_size=derived.sample_size,
        )
        self.assertIsNotNone(estimate.probability)
        self.assertGreater(estimate.probability, 0.0)
        self.assertLess(estimate.probability, 1.0)

    def test_team_save_rate_override_replaces_bank_save_percentage(self):
        # The GK has 50% save in the daily bank, but the team's deep history
        # says 80% over a strong sample. With override the per90 should be
        # higher than without it.
        bank_only = derive_player_assumption(self.GOALKEEPER_ROW, MarketFamily.PLAYER_SAVES)
        deep_override = derive_player_assumption(
            {**self.GOALKEEPER_ROW, "save_percentage": 50.0},
            MarketFamily.PLAYER_SAVES,
            team_save_rate_override=0.80,
        )
        # Both have the same opponent_sot baseline so per90 scales with save_pct.
        # Bank ⇒ per90 = 4 × 0.778 = 3.11. Override ⇒ 4 × 0.80 = 3.20.
        self.assertGreater(deep_override.assumption.per90_rate, 0.80 * DEFAULT_OPPONENT_SOT_PER90 - 0.01)
        self.assertIn("histórico deep", deep_override.explanation)
        self.assertIn("banco diario", bank_only.explanation)

    def test_clean_sheet_uses_poisson_zero_not_an_overline(self):
        derived = derive_player_assumption(
            self.GOALKEEPER_ROW, MarketFamily.PLAYER_CLEAN_SHEET, opponent_sot_per90=3.0,
        )
        estimate = estimate_player_market_probability(
            derived.assumption, MarketFamily.PLAYER_CLEAN_SHEET, line=0.5, sample_size=derived.sample_size,
        )
        # per90 = (1 - 0.778) x 3.0 = 0.666. The 90 expected minutes already
        # encode exposure, so starter probability is not applied a second time.
        # P(0 goals) = exp(-0.666) ~= 0.514.
        self.assertAlmostEqual(0.514, estimate.probability, places=2)
        self.assertEqual("poisson_zero", estimate.model_family)
        self.assertIn("portería a cero", estimate.explanation)


if __name__ == "__main__":
    unittest.main()
