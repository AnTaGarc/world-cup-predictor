import unittest

from wcpredict.github_wc2026_dataset import derive_period


class PeriodDerivationTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (1, "first_half"),
            (45, "first_half"),
            (46, "second_half"),
            (90, "second_half"),
            (91, "et_first"),
            (105, "et_first"),
            (106, "et_second"),
            (120, "et_second"),
        ]
        for minute, expected in cases:
            with self.subTest(minute=minute):
                self.assertEqual(expected, derive_period(minute))

    def test_out_of_range_raises(self):
        for bad in (0, -1, 121, 200):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    derive_period(bad)
