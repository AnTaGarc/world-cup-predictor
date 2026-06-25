from datetime import date, timedelta
import unittest

from wcpredict.ratings import MatchResult, build_team_ratings
from wcpredict.services import predict_match_markets
from wcpredict.advanced_form import XgFormAdjustment


class ServiceTests(unittest.TestCase):
    def test_advanced_form_moves_1x2_without_breaking_normalization(self):
        baseline = predict_match_markets("Czechia", "South Africa", [], date(2026, 6, 18))
        adjusted = predict_match_markets(
            "Czechia", "South Africa", [], date(2026, 6, 18),
            advanced_form=XgFormAdjustment(1.08, 0.82, 1, 1, "xG reciente"),
        )
        base_home = next(row for row in baseline if row.market_name == "1X2" and row.selection_name == "Czechia")
        adjusted_home = next(row for row in adjusted if row.market_name == "1X2" and row.selection_name == "Czechia")
        self.assertGreater(adjusted_home.probability, base_home.probability)
        self.assertAlmostEqual(sum(row.probability for row in adjusted if row.market_name == "1X2"), 1.0, places=6)
        self.assertIn("xG reciente", adjusted_home.explanation)

    def test_team_aliases_use_the_same_rating_history(self):
        today = date(2026, 6, 19)
        results = [
            MatchResult(today - timedelta(days=7), "USA", "Paraguay", 4, 1, "world_cup"),
            MatchResult(today - timedelta(days=7), "Australia", "Turkiye", 1, 0, "world_cup"),
        ]
        canonical = predict_match_markets("USA", "Australia", results, as_of=today)
        aliased = predict_match_markets("United States", "Australia", results, as_of=today)

        canonical_home = next(row for row in canonical if row.market_name == "1X2" and row.selection_name == "USA")
        aliased_home = next(row for row in aliased if row.market_name == "1X2" and row.selection_name == "United States")

        self.assertAlmostEqual(canonical_home.probability, aliased_home.probability, places=6)
        self.assertGreater(aliased_home.sample_size, 0.0)

    def test_unified_1x2_blends_chronological_ml_over_score_matrix(self):
        today = date(2026, 6, 19)
        predictions = predict_match_markets(
            "Brazil", "Haiti", [], today,
            advanced_form=XgFormAdjustment(1.00, 1.00, 1, 1, "proceso neutro"),
            outcome_probabilities={"home": 0.82, "draw": 0.13, "away": 0.05},
        )

        home = next(row for row in predictions if row.market_name == "1X2" and row.selection_name == "Brazil")
        away = next(row for row in predictions if row.market_name == "1X2" and row.selection_name == "Haiti")
        exact_score = next(row for row in predictions if row.market_name == "Exact Score")

        self.assertGreater(home.probability, 0.65)
        self.assertLess(away.probability, 0.15)
        self.assertIn("Modelo unificado 1X2", home.explanation)
        self.assertNotIn("Modelo unificado 1X2", exact_score.explanation)

    def test_exact_score_top_three_uses_tempered_scoreline_matrix(self):
        today = date(2026, 6, 19)
        predictions = predict_match_markets(
            "Brazil", "Haiti", [], today,
            outcome_probabilities={"home": 0.90, "draw": 0.05, "away": 0.05},
        )

        home = next(row for row in predictions if row.market_name == "1X2" and row.selection_name == "Brazil")
        exact_score = next(row for row in predictions if row.market_name == "Exact Score")
        alt_scores = [row.selection_name.split(" ")[0] for row in predictions if row.market_name == "Exact Score (alt)"]
        top_three = [exact_score.selection_name, *alt_scores[:2]]

        self.assertGreater(home.probability, 0.75)
        self.assertIn("1-1", top_three)
        self.assertLess(exact_score.probability, home.probability)

    def test_exact_score_top_three_reserves_high_tail_for_clear_favorites(self):
        today = date(2026, 6, 19)
        predictions = predict_match_markets(
            "Brazil", "Haiti", [], today,
            outcome_probabilities={"home": 0.90, "draw": 0.05, "away": 0.05},
        )

        exact_score = next(row for row in predictions if row.market_name == "Exact Score")
        alt_scores = [row.selection_name.split(" ")[0] for row in predictions if row.market_name == "Exact Score (alt)"]
        top_three = [exact_score.selection_name, *alt_scores[:2]]

        self.assertTrue(
            any(
                int(score.split("-")[0]) > int(score.split("-")[1])
                and sum(int(value) for value in score.split("-")) >= 4
                for score in top_three
            )
        )

    def test_draw_incentive_lifts_draw_without_breaking_normalization(self):
        today = date(2026, 6, 19)
        baseline = predict_match_markets(
            "Alpha", "Bravo", [], today,
            outcome_probabilities={"home": 0.46, "draw": 0.25, "away": 0.29},
        )
        adjusted = predict_match_markets(
            "Alpha", "Bravo", [], today,
            outcome_probabilities={"home": 0.46, "draw": 0.25, "away": 0.29},
            draw_incentive=0.22,
            draw_incentive_note="Empate clasifica a ambos equipos.",
        )

        base_draw = next(row for row in baseline if row.market_name == "1X2" and row.selection_name == "Draw")
        adjusted_draw = next(row for row in adjusted if row.market_name == "1X2" and row.selection_name == "Draw")

        self.assertGreater(adjusted_draw.probability, base_draw.probability)
        self.assertAlmostEqual(sum(row.probability for row in adjusted if row.market_name == "1X2"), 1.0, places=6)
        self.assertIn("Empate clasifica", adjusted_draw.explanation)

    def test_unified_1x2_applies_host_and_deep_process_to_outcome_model(self):
        today = date(2026, 6, 19)
        baseline = predict_match_markets(
            "United States", "Australia", [], today,
            advanced_form=XgFormAdjustment(1.08, 0.86, 1, 1, "proceso profundo"),
            outcome_probabilities={"home": 0.39, "draw": 0.25, "away": 0.36},
        )
        adjusted = predict_match_markets(
            "United States", "Australia", [], today,
            advanced_form=XgFormAdjustment(1.08, 0.86, 1, 1, "proceso profundo"),
            outcome_probabilities={"home": 0.39, "draw": 0.25, "away": 0.36},
            host_factor_a=1.10,
        )

        baseline_home = next(row for row in baseline if row.market_name == "1X2" and row.selection_name == "United States")
        baseline_away = next(row for row in baseline if row.market_name == "1X2" and row.selection_name == "Australia")
        adjusted_home = next(row for row in adjusted if row.market_name == "1X2" and row.selection_name == "United States")
        adjusted_away = next(row for row in adjusted if row.market_name == "1X2" and row.selection_name == "Australia")

        self.assertGreater(adjusted_home.probability, baseline_home.probability)
        self.assertLess(adjusted_away.probability, baseline_away.probability)
        self.assertGreater(adjusted_home.probability, adjusted_away.probability)
        self.assertIn("localía", adjusted_home.explanation)
        self.assertIn("proceso profundo", adjusted_home.explanation)

    def test_predict_match_markets_contains_core_markets(self):
        today = date(2026, 6, 18)
        results = [
            MatchResult(today - timedelta(days=20), "Spain", "Japan", 2, 0, "competitive"),
            MatchResult(today - timedelta(days=10), "Spain", "Canada", 3, 1, "world_cup"),
            MatchResult(today - timedelta(days=10), "Japan", "Canada", 1, 1, "world_cup"),
        ]
        predictions = predict_match_markets("Spain", "Japan", results, as_of=today)
        keys = {(p.market_name, p.selection_name) for p in predictions}
        self.assertIn(("1X2", "Spain"), keys)
        self.assertIn(("1X2", "Draw"), keys)
        self.assertIn(("Over/Under 2.5", "Over 2.5"), keys)
        self.assertIn(("Both Teams To Score", "Yes"), keys)
        self.assertIn(("Double Chance", "Spain or Draw"), keys)
        self.assertIn(("Draw No Bet", "Spain"), keys)
        exact = next(p for p in predictions if p.market_name == "Exact Score")
        self.assertRegex(exact.selection_name, r"^\d+-\d+$")
        self.assertGreater(exact.probability, 0.0)

    def test_no_team_history_is_low_confidence_with_probability_range(self):
        predictions = predict_match_markets("Canada", "Qatar", [], as_of=date(2026, 6, 18))
        self.assertTrue(all(row.confidence.value == "low" for row in predictions))
        self.assertTrue(all(row.low_probability <= row.probability <= row.high_probability for row in predictions))
        self.assertTrue(all(row.data_origin == "baseline" for row in predictions))

    def test_alt_scores_and_expected_score_accompany_the_mode(self):
        today = date(2026, 6, 19)
        results = [
            MatchResult(today - timedelta(days=20), "Spain", "Japan", 2, 0, "competitive"),
            MatchResult(today - timedelta(days=10), "Spain", "Canada", 3, 1, "world_cup"),
            MatchResult(today - timedelta(days=10), "Japan", "Canada", 1, 1, "world_cup"),
        ]
        predictions = predict_match_markets("Spain", "Japan", results, as_of=today)
        alt = [row for row in predictions if row.market_name == "Exact Score (alt)"]
        expected_row = next(
            (row for row in predictions if row.market_name == "Expected Score"),
            None,
        )
        self.assertEqual(3, len(alt))
        self.assertIsNotNone(expected_row)
        # The expected score format is "x.xx-y.yy".
        self.assertRegex(expected_row.selection_name, r"^\d+\.\d{2}-\d+\.\d{2}$")
        # Alternatives are distinct from the mode and from one another.
        labels = {row.selection_name.split(" ")[0] for row in alt}
        mode_row = next(p for p in predictions if p.market_name == "Exact Score")
        self.assertNotIn(mode_row.selection_name, labels)
        self.assertEqual(len(alt), len(labels))

    def test_over_under_now_covers_4_5_for_high_scoring_matches(self):
        predictions = predict_match_markets("Spain", "Japan", [], as_of=date(2026, 6, 18))
        keys = {row.market_name for row in predictions}
        self.assertIn("Over/Under 4.5", keys)

    def test_corrections_none_is_backward_compatible(self):
        # Default behaviour must be identical with corrections=None.
        results = [
            MatchResult(date(2026, 6, 1), "Spain", "Japan", 2, 0, "competitive"),
        ]
        baseline = predict_match_markets("Spain", "Japan", results, as_of=date(2026, 6, 18))
        with_none = predict_match_markets(
            "Spain", "Japan", results, as_of=date(2026, 6, 18), corrections=None,
        )
        for left, right in zip(baseline, with_none):
            self.assertEqual(left.market_name, right.market_name)
            self.assertEqual(left.selection_name, right.selection_name)
            self.assertAlmostEqual(left.probability, right.probability, places=10)

    def test_precomputed_ratings_do_not_change_predictions(self):
        today = date(2026, 6, 18)
        results = [
            MatchResult(today - timedelta(days=20), "Spain", "Japan", 2, 0, "competitive"),
            MatchResult(today - timedelta(days=10), "Spain", "Canada", 3, 1, "world_cup"),
            MatchResult(today - timedelta(days=10), "Japan", "Canada", 1, 1, "world_cup"),
        ]
        baseline = predict_match_markets("Spain", "Japan", results, as_of=today)
        optimized = predict_match_markets(
            "Spain",
            "Japan",
            results,
            as_of=today,
            precomputed_ratings=build_team_ratings(results, today),
        )
        self.assertEqual(
            [(row.market_name, row.selection_name) for row in baseline],
            [(row.market_name, row.selection_name) for row in optimized],
        )
        for left, right in zip(baseline, optimized):
            self.assertAlmostEqual(left.probability, right.probability, places=12)

    def test_positive_xg_shift_lowers_total_goals_prediction(self):
        from wcpredict.model_corrections import ModelCorrections
        today = date(2026, 6, 20)
        no_corr = predict_match_markets(
            "Brazil", "Haiti", [], today,
            advanced_form=XgFormAdjustment(1.30, 0.80, 5, 5, "deep"),
            outcome_probabilities={"home": 0.80, "draw": 0.15, "away": 0.05},
        )
        corrected = predict_match_markets(
            "Brazil", "Haiti", [], today,
            advanced_form=XgFormAdjustment(1.30, 0.80, 5, 5, "deep"),
            outcome_probabilities={"home": 0.80, "draw": 0.15, "away": 0.05},
            corrections=ModelCorrections(
                xg_shift=0.30, outcome_logit_shifts={"home": 0.0, "draw": 0.0, "away": 0.0},
                sample_size=36,
            ),
        )
        baseline_expected = next(p for p in no_corr if p.market_name == "Expected Score")
        corrected_expected = next(p for p in corrected if p.market_name == "Expected Score")
        b_a, b_b = (float(x) for x in baseline_expected.selection_name.split("-"))
        c_a, c_b = (float(x) for x in corrected_expected.selection_name.split("-"))
        self.assertLess(c_a + c_b, b_a + b_b)
        # Both teams' expected xG reduced.
        self.assertLess(c_a, b_a)
        self.assertLess(c_b, b_b)

    def test_outcome_logit_shift_reduces_overestimated_home(self):
        from wcpredict.model_corrections import ModelCorrections
        today = date(2026, 6, 20)
        base = predict_match_markets(
            "USA", "Australia", [], today,
            outcome_probabilities={"home": 0.55, "draw": 0.25, "away": 0.20},
        )
        corrected = predict_match_markets(
            "USA", "Australia", [], today,
            outcome_probabilities={"home": 0.55, "draw": 0.25, "away": 0.20},
            corrections=ModelCorrections(
                xg_shift=0.0,
                outcome_logit_shifts={"home": -0.20, "draw": 0.0, "away": 0.10},
                sample_size=36,
            ),
        )
        base_home = next(p for p in base if p.market_name == "1X2" and p.selection_name == "USA")
        corrected_home = next(p for p in corrected if p.market_name == "1X2" and p.selection_name == "USA")
        base_away = next(p for p in base if p.market_name == "1X2" and p.selection_name == "Australia")
        corrected_away = next(p for p in corrected if p.market_name == "1X2" and p.selection_name == "Australia")
        self.assertLess(corrected_home.probability, base_home.probability)
        self.assertGreater(corrected_away.probability, base_away.probability)
        # Probabilities still sum to 1.
        self.assertAlmostEqual(
            sum(p.probability for p in corrected if p.market_name == "1X2"),
            1.0, places=6,
        )

    def test_unified_1x2_reweights_goal_markets_from_the_same_score_matrix(self):
        today = date(2026, 6, 20)
        baseline = predict_match_markets("Spain", "Japan", [], today)
        draw_heavy = predict_match_markets(
            "Spain", "Japan", [], today,
            outcome_probabilities={"home": 0.05, "draw": 0.90, "away": 0.05},
        )

        base_over = next(p for p in baseline if p.market_name == "Over/Under 2.5" and p.selection_name == "Over 2.5")
        draw_over = next(p for p in draw_heavy if p.market_name == "Over/Under 2.5" and p.selection_name == "Over 2.5")
        base_btts = next(p for p in baseline if p.market_name == "Both Teams To Score" and p.selection_name == "Yes")
        draw_btts = next(p for p in draw_heavy if p.market_name == "Both Teams To Score" and p.selection_name == "Yes")

        self.assertLess(draw_over.probability, base_over.probability)
        self.assertNotAlmostEqual(base_btts.probability, draw_btts.probability, places=4)

    def test_player_context_changes_probabilities_and_keeps_auditable_explanation(self):
        baseline = predict_match_markets("Canada", "Qatar", [], date(2026, 6, 20))
        adjusted = predict_match_markets(
            "Canada", "Qatar", [], date(2026, 6, 20),
            player_context=[{
                "player_name": "Canada scorer", "team_name": "Canada", "minutes": 270,
                "expected_minutes": 90, "starter_probability": 0.0, "availability": "out",
                "goals": 4, "assists": 1, "shots_on_target": 8,
            }],
        )
        base_home = next(row for row in baseline if row.market_name == "1X2" and row.selection_name == "Canada")
        adjusted_home = next(row for row in adjusted if row.market_name == "1X2" and row.selection_name == "Canada")
        self.assertLess(adjusted_home.probability, base_home.probability)
        self.assertIn("Canada scorer", adjusted_home.explanation)


if __name__ == "__main__":
    unittest.main()
