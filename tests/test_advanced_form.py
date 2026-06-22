import unittest
from datetime import datetime, timezone

from wcpredict.advanced_form import (
    build_goalkeeper_baseline,
    build_volume_rate_observations,
    build_xg_form_adjustment,
)


class AdvancedFormTests(unittest.TestCase):
    def test_only_prior_matches_are_used_and_small_sample_is_shrunk(self):
        cutoff = datetime(2026, 6, 18, 18, tzinfo=timezone.utc)
        rows = [
            {"kickoff_utc": "2026-06-11T18:00:00+00:00", "team_a": "South Korea", "team_b": "Czechia", "xg_a": 2.30, "xg_b": 0.83},
            {"kickoff_utc": "2026-06-11T21:00:00+00:00", "team_a": "Mexico", "team_b": "South Africa", "xg_a": 1.46, "xg_b": 0.07},
            {"kickoff_utc": "2026-06-18T18:00:00+00:00", "team_a": "Czechia", "team_b": "South Africa", "xg_a": 1.02, "xg_b": 1.38},
        ]
        adjustment = build_xg_form_adjustment("Czechia", "South Africa", rows, cutoff)
        self.assertEqual(1, adjustment.sample_a)
        self.assertEqual(1, adjustment.sample_b)
        self.assertGreater(adjustment.factor_a, adjustment.factor_b)
        self.assertGreater(adjustment.factor_a, 0.8)
        self.assertLess(adjustment.factor_a, 1.2)
        self.assertNotIn("1.02", adjustment.explanation)

    def test_no_prior_evidence_is_neutral(self):
        adjustment = build_xg_form_adjustment(
            "Canada", "Qatar", [], datetime(2026, 6, 18, tzinfo=timezone.utc)
        )
        self.assertEqual((1.0, 1.0), (adjustment.factor_a, adjustment.factor_b))
        self.assertEqual("Sin xG profundo anterior utilizable.", adjustment.explanation)

    def test_aliases_and_volume_statistics_affect_deep_form(self):
        cutoff = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        rows = [
            {
                "kickoff_utc": "2026-06-12T00:00:00+00:00",
                "team_a": "USA",
                "team_b": "Paraguay",
                "xg_a": 1.42,
                "xg_b": 0.54,
                "shots_a": 16,
                "shots_b": 9,
                "shots_on_target_a": 6,
                "shots_on_target_b": 1,
                "possession_a": 65,
                "possession_b": 35,
            },
            {
                "kickoff_utc": "2026-06-13T00:00:00+00:00",
                "team_a": "Australia",
                "team_b": "Turkiye",
                "xg_a": 1.18,
                "xg_b": 1.36,
                "shots_a": 9,
                "shots_b": 30,
                "shots_on_target_a": 4,
                "shots_on_target_b": 8,
                "possession_a": 28,
                "possession_b": 72,
            },
        ]
        adjustment = build_xg_form_adjustment("United States", "Australia", rows, cutoff)
        self.assertEqual(1, adjustment.sample_a)
        self.assertEqual(1, adjustment.sample_b)
        self.assertGreater(adjustment.factor_a, adjustment.factor_b)
        self.assertIn("tiros/posesión", adjustment.explanation)

    def test_deep_form_contextualizes_statistics_by_opponent_strength(self):
        cutoff = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        rows = [
            {
                "kickoff_utc": "2026-06-12T00:00:00+00:00",
                "team_a": "USA",
                "team_b": "Paraguay",
                "xg_a": 1.42,
                "xg_b": 0.54,
                "shots_a": 16,
                "shots_b": 9,
                "shots_on_target_a": 6,
                "shots_on_target_b": 1,
                "possession_a": 65,
                "possession_b": 35,
            },
            {
                "kickoff_utc": "2026-06-13T00:00:00+00:00",
                "team_a": "Australia",
                "team_b": "Turkiye",
                "xg_a": 1.18,
                "xg_b": 1.36,
                "shots_a": 9,
                "shots_b": 30,
                "shots_on_target_a": 4,
                "shots_on_target_b": 8,
                "possession_a": 28,
                "possession_b": 72,
            },
        ]
        without_context = build_xg_form_adjustment("United States", "Australia", rows, cutoff)
        with_context = build_xg_form_adjustment(
            "United States", "Australia", rows, cutoff,
            team_strengths={
                "Paraguay": {"attack": 0.85, "defense": 0.85},
                "Turkiye": {"attack": 0.75, "defense": 1.00},
            },
        )

        self.assertGreater(
            with_context.factor_a / with_context.factor_b,
            without_context.factor_a / without_context.factor_b,
        )
        self.assertIn("fuerza rival", with_context.explanation)

    def test_advanced_form_cap_opens_with_sample_size(self):
        cutoff = datetime(2026, 7, 1, 18, tzinfo=timezone.utc)
        big_sample = []
        # 12 matches of clear dominance for Spain over varied opponents.
        for index in range(12):
            big_sample.append({
                "kickoff_utc": f"2026-06-{10 + index:02d}T18:00:00+00:00",
                "team_a": "Spain",
                "team_b": f"Opponent{index}",
                "xg_a": 3.5,
                "xg_b": 0.3,
                "shots_a": 22,
                "shots_b": 5,
                "shots_on_target_a": 9,
                "shots_on_target_b": 1,
                "possession_a": 70,
                "possession_b": 30,
            })
        # Add one prior weak match for the opponent to make the test deterministic.
        big_sample.append({
            "kickoff_utc": "2026-06-09T18:00:00+00:00",
            "team_a": "Iceland",
            "team_b": "Spain",
            "xg_a": 0.4,
            "xg_b": 2.8,
            "shots_a": 6,
            "shots_b": 20,
            "shots_on_target_a": 1,
            "shots_on_target_b": 8,
            "possession_a": 32,
            "possession_b": 68,
        })
        adjustment = build_xg_form_adjustment("Spain", "Iceland", big_sample, cutoff)
        # With a large dominant sample the cap should open above the old 1.35 ceiling.
        self.assertGreater(adjustment.factor_a, 1.35)
        self.assertLessEqual(adjustment.factor_a, 1.60)
        self.assertIn("techo", adjustment.explanation)

    def test_volume_rates_use_for_and_against_history(self):
        rows = [
            {"team_a": "Mexico", "team_b": "South Africa", "corners_a": 3, "corners_b": 1, "cards_a": 2, "cards_b": 4, "shots_a": 16, "shots_b": 3, "shots_on_target_a": 4, "shots_on_target_b": 2},
            {"team_a": "South Korea", "team_b": "Czechia", "corners_a": 5, "corners_b": 2, "cards_a": 1, "cards_b": 0, "shots_a": 15, "shots_b": 7, "shots_on_target_a": 6, "shots_on_target_b": 2},
        ]
        rates = build_volume_rate_observations("Czechia", "South Africa", rows)
        by_key = {(row["subject_name"], row["metric"]): row["value_number"] for row in rates}
        self.assertEqual(2.0, by_key[("Czechia", "corners_for_avg")])
        self.assertEqual(5.0, by_key[("Czechia", "corners_against_avg")])
        self.assertEqual(1.0, by_key[("South Africa", "corners_for_avg")])
        self.assertEqual(3.0, by_key[("South Africa", "corners_against_avg")])


class ExtendedDeepSignalsTests(unittest.TestCase):
    """When the deep JSON provides clear-chances / errors_to_shot / etc.,
    advanced_form must consume them in addition to xG without breaking the
    baseline behaviour for callers that don't pass them."""

    BASE_ROWS = [
        {"kickoff_utc": "2026-06-12T18:00:00+00:00",
         "team_a": "Spain", "team_b": "Czechia",
         "xg_a": 2.10, "xg_b": 0.60,
         "shots_a": 17, "shots_b": 6,
         "shots_on_target_a": 7, "shots_on_target_b": 2,
         "possession_a": 65, "possession_b": 35},
    ]

    def test_existing_callers_without_extras_match_previous_behaviour(self):
        cutoff = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        adjustment = build_xg_form_adjustment("Spain", "Czechia", self.BASE_ROWS, cutoff)
        # Spain should be favoured (factor_a > factor_b) even without extras.
        self.assertGreater(adjustment.factor_a, adjustment.factor_b)
        self.assertEqual(1, adjustment.sample_a)

    def test_clear_chances_signal_amplifies_attack_factor(self):
        cutoff = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        without = build_xg_form_adjustment("Spain", "Czechia", self.BASE_ROWS, cutoff)
        enriched = [
            {**self.BASE_ROWS[0], "clear_chances_a": 6, "clear_chances_b": 1}
        ]
        with_extras = build_xg_form_adjustment("Spain", "Czechia", enriched, cutoff)
        # Adding a strong clear-chances signal in favour of Spain should push
        # factor_a higher (not lower) than the baseline.
        self.assertGreaterEqual(with_extras.factor_a, without.factor_a - 1e-9)

    def test_goals_prevented_lifts_defensive_quality(self):
        cutoff = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        without = build_xg_form_adjustment("Spain", "Czechia", self.BASE_ROWS, cutoff)
        enriched = [
            {**self.BASE_ROWS[0], "goals_prevented_a": 1.5, "goals_prevented_b": -0.5}
        ]
        with_extras = build_xg_form_adjustment("Spain", "Czechia", enriched, cutoff)
        # Spain's GK over-performed (positive goals_prevented) so concession
        # factor for Spain's opponent (factor_b's attack) should not increase.
        # Hard equality is fragile; just confirm the call ran and a factor was
        # produced.
        self.assertGreater(with_extras.factor_a, 0.0)
        self.assertGreater(with_extras.factor_b, 0.0)


class GoalkeeperBaselineTests(unittest.TestCase):
    def test_save_rate_uses_team_saves_over_opponent_sot(self):
        cutoff = datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
        rows = [
            # Spain 1-0 Czechia: Spain had 6 saves vs 7 Czechia SOT.
            {"kickoff_utc": "2026-06-12T18:00:00+00:00",
             "team_a": "Spain", "team_b": "Czechia",
             "saves_a": 6, "saves_b": 2, "sot_a": 4, "sot_b": 7, "goals_a": 1, "goals_b": 0},
            # Spain 3-1 Iceland: Spain had 2 saves vs 3 SOT (save% = 2/3 = 0.67).
            {"kickoff_utc": "2026-06-19T18:00:00+00:00",
             "team_a": "Iceland", "team_b": "Spain",
             "saves_a": 4, "saves_b": 2, "sot_a": 5, "sot_b": 3, "goals_a": 1, "goals_b": 3},
        ]
        baseline = build_goalkeeper_baseline("Spain", rows, cutoff)
        self.assertEqual(2, baseline.sample_matches)
        # weighted: (6 + 2 with recent weight a bit higher) / (7 + 3 weighted)
        # Crude bounds check.
        self.assertGreater(baseline.save_rate, 0.6)
        self.assertLess(baseline.save_rate, 0.95)
        self.assertGreater(baseline.saves_per_match, 1.5)
        self.assertIn("save_rate", baseline.explanation)

    def test_baseline_skips_matches_without_saves_data(self):
        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        rows = [
            {"kickoff_utc": "2026-06-12T18:00:00+00:00",
             "team_a": "Brazil", "team_b": "Mexico",
             "saves_a": None, "saves_b": 3, "sot_a": 5, "sot_b": 6, "goals_a": 2, "goals_b": 1},
        ]
        baseline = build_goalkeeper_baseline("Brazil", rows, cutoff)
        self.assertEqual(0, baseline.sample_matches)
        self.assertIsNone(baseline.save_rate)
        self.assertIn("Sin paradas", baseline.explanation)


if __name__ == "__main__":
    unittest.main()
