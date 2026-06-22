import unittest

from wcpredict.count_models import count_variance, over_probability


class CountModelTests(unittest.TestCase):
    def test_negative_binomial_variance_exceeds_mean(self):
        self.assertGreater(count_variance(8.0, dispersion=0.25), 8.0)

    def test_zero_dispersion_matches_poisson_limit(self):
        poisson = over_probability(3.2, 2.5, distribution="poisson")
        limit = over_probability(3.2, 2.5, distribution="negative_binomial", dispersion=0.0)
        self.assertAlmostEqual(poisson, limit, places=10)

    def test_over_probability_is_normalized(self):
        probability = over_probability(9.0, 8.5, distribution="negative_binomial", dispersion=0.2)
        self.assertGreaterEqual(probability, 0.0)
        self.assertLessEqual(probability, 1.0)


if __name__ == "__main__":
    unittest.main()
