import unittest

from wcpredict.advanced_form import GoalkeeperBaseline, goalkeeper_xg_factor


def _baseline(save_rate, matches=5):
    return GoalkeeperBaseline(
        team_name="X", save_rate=save_rate, saves_per_match=None,
        goals_conceded_per_match=None, sample_matches=matches,
        explanation="",
    )


class GoalkeeperXgFactorTests(unittest.TestCase):
    def test_missing_baseline_returns_neutral(self):
        self.assertEqual(1.0, goalkeeper_xg_factor(None))

    def test_no_sample_returns_neutral(self):
        b = _baseline(0.95, matches=1)
        self.assertEqual(1.0, goalkeeper_xg_factor(b, minimum_sample=3))

    def test_good_keeper_lowers_factor(self):
        b = _baseline(0.85)
        factor = goalkeeper_xg_factor(b)
        self.assertLess(factor, 1.0)
        self.assertGreaterEqual(factor, 0.92)

    def test_porous_keeper_raises_factor(self):
        b = _baseline(0.55)
        factor = goalkeeper_xg_factor(b)
        self.assertGreater(factor, 1.0)
        self.assertLessEqual(factor, 1.08)

    def test_average_keeper_is_close_to_one(self):
        b = _baseline(0.70)
        self.assertAlmostEqual(1.0, goalkeeper_xg_factor(b), places=4)

    def test_extreme_save_rate_hits_floor(self):
        # Very high save_rate would compute factor far below 0.92; must clip.
        b = _baseline(2.0)  # impossible value, just to hit the clip
        self.assertAlmostEqual(0.92, goalkeeper_xg_factor(b), places=4)


if __name__ == "__main__":
    unittest.main()
