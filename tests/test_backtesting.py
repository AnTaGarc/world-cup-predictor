import unittest

from wcpredict.backtesting import brier_score, calibration_bands, market_hit, summarize_by_market_family, calibration_drift


class BacktestingTests(unittest.TestCase):
    def test_brier_score(self):
        self.assertAlmostEqual(0.16, brier_score(0.6, True), places=6)
        self.assertAlmostEqual(0.36, brier_score(0.6, False), places=6)

    def test_market_hit(self):
        self.assertTrue(market_hit("over", observed_value=3, line=2.5))
        self.assertFalse(market_hit("under", observed_value=3, line=2.5))

    def test_calibration_bands(self):
        rows = [(0.62, True), (0.68, False), (0.21, False)]
        bands = calibration_bands(rows, band_size=0.2)
        self.assertIn("0.60-0.80", bands)
        self.assertEqual(2, bands["0.60-0.80"]["count"])

    def test_market_family_summary_and_drift_keep_sample_size(self):
        rows = [
            {"market_family": "goals", "probability": .6, "hit": 1, "brier_score": .16, "evaluated_at_utc": "2026-06-18T10:00:00+00:00"},
            {"market_family": "goals", "probability": .7, "hit": 0, "brier_score": .49, "evaluated_at_utc": "2026-06-19T10:00:00+00:00"},
        ]
        summary = summarize_by_market_family(rows)
        self.assertEqual(2, summary["goals"]["count"])
        self.assertAlmostEqual(.325, summary["goals"]["avg_brier"])
        drift = calibration_drift(rows)
        self.assertEqual(2, len(drift))
        self.assertAlmostEqual(.325, drift[-1]["cumulative_brier"])


if __name__ == "__main__":
    unittest.main()
