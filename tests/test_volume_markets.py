import unittest

from wcpredict.volume_markets import estimate_total_market


class VolumeMarketTests(unittest.TestCase):
    def test_blends_for_and_against_rates_for_total(self):
        rows = [
            {"subject_name": "Canada", "metric": "corners_for_avg", "value_number": 6.0, "sample_size": 10},
            {"subject_name": "Canada", "metric": "corners_against_avg", "value_number": 4.0, "sample_size": 10},
            {"subject_name": "Qatar", "metric": "corners_for_avg", "value_number": 3.0, "sample_size": 8},
            {"subject_name": "Qatar", "metric": "corners_against_avg", "value_number": 5.0, "sample_size": 8},
        ]
        estimate = estimate_total_market("Canada", "Qatar", rows, "corners", 8.5)
        self.assertAlmostEqual(9.0, estimate.expected_total, places=6)
        self.assertGreater(estimate.over_probability, 0.5)
        self.assertEqual("medium", estimate.confidence)
        self.assertLess(estimate.low_probability, estimate.high_probability)

    def test_sparse_or_missing_rates_are_not_estimable(self):
        estimate = estimate_total_market("Canada", "Qatar", [], "cards", 3.5)
        self.assertIsNone(estimate.over_probability)
        self.assertEqual("not_estimable", estimate.confidence)

    def test_overdispersed_market_uses_negative_binomial_when_dispersion_is_available(self):
        rows = [
            {"subject_name": team, "metric": metric, "value_number": value, "sample_size": 12}
            for team, metric, value in (
                ("Canada", "corners_for_avg", 6), ("Canada", "corners_against_avg", 4),
                ("Qatar", "corners_for_avg", 3), ("Qatar", "corners_against_avg", 5),
            )
        ]
        estimate = estimate_total_market("Canada", "Qatar", rows, "corners", 8.5, dispersion=0.2)
        self.assertEqual("negative_binomial", estimate.model_family)
        self.assertIn("sobredispers", estimate.explanation)


if __name__ == "__main__":
    unittest.main()
