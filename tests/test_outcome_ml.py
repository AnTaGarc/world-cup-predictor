import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from wcpredict.outcome_ml import build_training_rows, current_match_features, load_outcome_model, match_results_to_feature_rows, save_outcome_model, train_outcome_model
from wcpredict.ratings import MatchResult


class OutcomeModelTests(unittest.TestCase):
    def _rows(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        outcomes = ["home", "draw", "away"] * 20
        for i, outcome in enumerate(outcomes):
            signal = {"home": 1.4, "draw": 0.0, "away": -1.4}[outcome]
            rows.append(
                {
                    "played_at_utc": (start + timedelta(days=i)).isoformat(),
                    "rating_diff": signal + (i % 4) * 0.03,
                    "form_diff": signal / 2,
                    "goal_diff_form": signal / 3,
                    "neutral_site": 1,
                    "outcome": outcome,
                }
            )
        return rows

    def test_training_is_chronological_and_probabilities_normalize(self):
        fitted = train_outcome_model(self._rows(), minimum_matches=30)
        self.assertEqual(fitted.status, "ready")
        self.assertLess(fitted.training_cutoff_utc, fitted.validation_cutoff_utc)
        probabilities = fitted.predict({"rating_diff": 1.2, "form_diff": 0.6, "goal_diff_form": 0.4, "neutral_site": 1})
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=8)
        self.assertEqual(set(probabilities), {"home", "draw", "away"})

    def test_refuses_small_samples(self):
        fitted = train_outcome_model(self._rows()[:12], minimum_matches=30)
        self.assertEqual(fitted.status, "insufficient_data")

    def test_feature_replay_uses_only_prior_results(self):
        matches = [
            {"played_at_utc": "2026-01-01", "team_a": "A", "team_b": "B", "goals_a": 2, "goals_b": 0, "neutral_site": 1},
            {"played_at_utc": "2026-02-01", "team_a": "B", "team_b": "A", "goals_a": 1, "goals_b": 1, "neutral_site": 1},
        ]
        rows = build_training_rows(matches)
        self.assertEqual(2, len(rows))
        self.assertEqual(0.0, rows[0]["rating_diff"])
        features = current_match_features(matches, "A", "B", neutral_site=True)
        self.assertGreater(features["rating_diff"], 0)

    def test_feature_replay_deduplicates_alias_equivalent_matches(self):
        matches = [
            {"played_at_utc": "2026-06-11T18:00:00+00:00", "team_a": "Korea Republic", "team_b": "Czechia", "goals_a": 2, "goals_b": 1, "neutral_site": 1},
            {"played_at_utc": "2026-06-11T20:00:00+00:00", "team_a": "South Korea", "team_b": "Czech Republic", "goals_a": 2, "goals_b": 1, "neutral_site": 1},
        ]
        self.assertEqual(1, len(build_training_rows(matches)))

    def test_current_features_canonicalize_target_team_aliases(self):
        matches = [
            {"played_at_utc": "2026-06-12T00:00:00+00:00", "team_a": "USA", "team_b": "Paraguay", "goals_a": 4, "goals_b": 1, "neutral_site": 1},
        ]
        features = current_match_features(matches, "United States", "Australia", neutral_site=True)
        self.assertGreater(features["rating_diff"], 0.0)
        self.assertGreater(features["form_diff"], 0.0)
        self.assertGreater(features["goal_diff_form"], 0.0)

    def test_local_match_results_can_feed_current_features(self):
        rows = match_results_to_feature_rows([
            MatchResult(datetime(2026, 6, 12, tzinfo=timezone.utc).date(), "USA", "Paraguay", 4, 1, "world_cup")
        ])
        features = current_match_features(rows, "United States", "Australia", neutral_site=True)
        self.assertGreater(features["goal_diff_form"], 0.0)

    def test_ready_model_artifact_roundtrip(self):
        fitted = train_outcome_model(self._rows(), minimum_matches=30)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outcome.joblib"
            save_outcome_model(fitted, path)
            restored = load_outcome_model(path)
        self.assertEqual("ready", restored.status)
        self.assertEqual(fitted.sample_size, restored.sample_size)


if __name__ == "__main__":
    unittest.main()
