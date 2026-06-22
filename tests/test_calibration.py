import unittest
from datetime import datetime, timezone

from wcpredict.calibration import (
    CalibrationSample,
    build_calibration_samples,
    summarise_bias,
)


def _sample(home: float, draw: float, away: float, actual: str,
            xg_a=None, xg_b=None, actual_xg_a=None, actual_xg_b=None,
            total_pred=None, total_actual=0) -> CalibrationSample:
    return CalibrationSample(
        match_id=1, team_a="A", team_b="B",
        kickoff_utc=datetime(2026, 6, 18, 18, tzinfo=timezone.utc),
        predicted_1x2={"home": home, "draw": draw, "away": away},
        actual_outcome=actual,
        predicted_xg_a=xg_a, predicted_xg_b=xg_b,
        actual_xg_a=actual_xg_a, actual_xg_b=actual_xg_b,
        predicted_total_goals=total_pred, actual_total_goals=total_actual,
    )


class BiasReportTests(unittest.TestCase):
    def test_empty_input_emits_zero_sized_report(self):
        report = summarise_bias([])
        self.assertEqual(0, report.sample_size)
        self.assertIn("Sin partidos cerrados", report.notes[0])

    def test_xg_bias_detected_when_model_overshoots(self):
        # Five matches where the model predicted 2.0 xG/team but reality was 1.0 xG.
        samples = [
            _sample(0.5, 0.3, 0.2, "home", xg_a=2.0, xg_b=2.0,
                    actual_xg_a=1.0, actual_xg_b=1.0,
                    total_pred=4.0, total_actual=2)
            for _ in range(5)
        ]
        report = summarise_bias(samples)
        self.assertAlmostEqual(1.0, report.xg_bias_per_team, places=2)
        self.assertGreater(report.total_goals_bias, 1.5)
        self.assertTrue(any("sobreestima el xG" in note for note in report.notes))

    def test_outcome_accuracy_reflects_argmax_hits(self):
        samples = [
            _sample(0.6, 0.2, 0.2, "home"),
            _sample(0.6, 0.2, 0.2, "away"),
            _sample(0.4, 0.4, 0.2, "draw"),
            _sample(0.6, 0.2, 0.2, "home"),
        ]
        report = summarise_bias(samples)
        # argmax picks home, home, draw (tie broken by dict order: 'home' before 'draw'), home.
        # Hits: 1, 0, 0 (predicted home not draw), 1 = 2/4
        # Actually need to check: argmax of {"home":0.4, "draw":0.4, "away":0.2}
        # Python's max with dict.get key returns the first maximum: 'home' (insertion order).
        # So predictions: home, home, home, home → matches actual home twice (samples 1 & 4).
        self.assertAlmostEqual(0.50, report.outcome_accuracy, places=2)

    def test_1x2_calibration_gap_emits_a_note(self):
        # Model says home 70% but reality is home 40% in 5 matches.
        samples = (
            [_sample(0.70, 0.20, 0.10, "home") for _ in range(2)]
            + [_sample(0.70, 0.20, 0.10, "draw") for _ in range(1)]
            + [_sample(0.70, 0.20, 0.10, "away") for _ in range(2)]
        )
        report = summarise_bias(samples)
        self.assertGreater(report.home_predicted_avg - report.home_actual_frequency, 0.20)
        self.assertTrue(any("victoria local" in note for note in report.notes))


if __name__ == "__main__":
    unittest.main()
