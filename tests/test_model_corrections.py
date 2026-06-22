import math
import unittest

from wcpredict.calibration import BiasReport
from wcpredict.model_corrections import (
    ModelCorrections,
    apply_outcome_shifts,
    derive_corrections,
    describe_corrections,
    is_active,
)


def _report(**overrides) -> BiasReport:
    defaults = dict(
        sample_size=36,
        home_predicted_avg=0.40, home_actual_frequency=0.40,
        draw_predicted_avg=0.27, draw_actual_frequency=0.27,
        away_predicted_avg=0.33, away_actual_frequency=0.33,
        xg_bias_per_team=0.0, xg_mean_absolute_error=0.0,
        total_goals_bias=0.0, total_goals_mae=0.0,
        outcome_accuracy=0.5, favourites_calibration={}, notes=[],
    )
    defaults.update(overrides)
    return BiasReport(**defaults)


class DeriveCorrectionsTests(unittest.TestCase):
    def test_small_sample_does_not_overcorrect(self):
        # Measured xG bias of +0.5 with only 36 samples and prior strength of 30
        # should yield ~0.27 applied (36 / 66 ≈ 0.545).
        report = _report(xg_bias_per_team=0.5, sample_size=36)
        corrections = derive_corrections(report, xg_prior_strength=30)
        self.assertAlmostEqual(0.5 * 36 / 66, corrections.xg_shift, places=4)
        self.assertAlmostEqual(36 / 66, corrections.applied_strength["xg"], places=4)

    def test_bias_below_threshold_is_dropped(self):
        report = _report(xg_bias_per_team=0.05)
        corrections = derive_corrections(report, min_xg_to_apply=0.10)
        self.assertEqual(0.0, corrections.xg_shift)

    def test_outcome_overestimation_yields_negative_shift(self):
        # Model predicts home 60% but actual frequency is 45% → log(0.45/0.6) ≈ -0.288
        report = _report(
            home_predicted_avg=0.60, home_actual_frequency=0.45,
            draw_predicted_avg=0.20, draw_actual_frequency=0.25,
            away_predicted_avg=0.20, away_actual_frequency=0.30,
        )
        corrections = derive_corrections(report, outcome_prior_strength=30)
        self.assertLess(corrections.outcome_logit_shifts["home"], 0)
        self.assertGreater(corrections.outcome_logit_shifts["away"], 0)
        # Shrinkage: applied < raw shift.
        raw_home = math.log(0.45 / 0.60)
        self.assertGreater(corrections.outcome_logit_shifts["home"], raw_home)

    def test_no_sample_yields_inactive_corrections(self):
        corrections = derive_corrections(_report(sample_size=0))
        self.assertFalse(is_active(corrections))

    def test_zero_corrections_describe_as_inactive_text(self):
        corrections = derive_corrections(_report())
        self.assertIn("Sin corrección", describe_corrections(corrections))

    def test_description_lists_active_shifts(self):
        corrections = ModelCorrections(
            xg_shift=-0.20,
            outcome_logit_shifts={"home": -0.15, "draw": 0.0, "away": 0.15},
            sample_size=36,
        )
        text = describe_corrections(corrections)
        self.assertIn("xG por equipo", text)
        self.assertIn("local", text)
        self.assertIn("visitante", text)


class ApplyOutcomeShiftsTests(unittest.TestCase):
    def test_shift_redistributes_mass_and_renormalises_to_one(self):
        result = apply_outcome_shifts(
            {"home": 0.6, "draw": 0.2, "away": 0.2},
            {"home": -0.4, "draw": 0.0, "away": 0.0},
        )
        self.assertAlmostEqual(1.0, sum(result.values()), places=6)
        self.assertLess(result["home"], 0.6)
        self.assertGreater(result["away"], 0.2)

    def test_zero_shifts_only_renormalise(self):
        result = apply_outcome_shifts(
            {"home": 0.5, "draw": 0.3, "away": 0.2},
            {"home": 0.0, "draw": 0.0, "away": 0.0},
        )
        for key in ("home", "draw", "away"):
            self.assertAlmostEqual(
                {"home": 0.5, "draw": 0.3, "away": 0.2}[key], result[key], places=6,
            )


if __name__ == "__main__":
    unittest.main()
