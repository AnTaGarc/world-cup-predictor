import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository
from wcpredict.tournament_form_adjustment import (
    Adjustment,
    apply_form_adjustment,
    build_form_features,
    build_match_adjustment,
    calibrate_alpha,
)


def _seed_gh_match(con, ext_id, home_id, away_id, hs, aws, kickoff, home_xg=None, away_xg=None):
    con.execute(
        "INSERT INTO gh_matches(provider_id, external_match_id, home_team_id, away_team_id, "
        "home_score, away_score, kickoff_time_utc, date, home_xg, away_xg, imported_at_utc) "
        "VALUES('github_wc2026_matches', ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-07-01T00:00:00+00:00')",
        (ext_id, home_id, away_id, hs, aws, kickoff, kickoff[:10], home_xg, away_xg),
    )


def _seed_event(con, event_id, ext_match_id, minute, event_type, team_id):
    con.execute(
        "INSERT INTO gh_match_events(provider_id, external_event_id, external_match_id, "
        "minute, period, event_type, external_team_id, team_id, external_player_id, "
        "provider_version, imported_at_utc) "
        "VALUES('github_wc2026_events', ?, ?, ?, 'x', ?, 0, ?, 0, 'v', '2026-07-01T00:00:00+00:00')",
        (event_id, ext_match_id, minute, event_type, team_id),
    )


class FormFeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'England', 'ENG')")

    def tearDown(self):
        self.directory.cleanup()

    def test_team_without_matches_has_zero_weight(self):
        features = build_form_features(self.repo, "Mexico", "2026-07-05T17:00:00+00:00")
        self.assertEqual(0, features.matches_played)
        adjustment = build_match_adjustment(
            self.repo, "Mexico", "England", "2026-07-05T17:00:00+00:00"
        )
        self.assertEqual(0.0, adjustment.weight)
        self.assertEqual(0.0, adjustment.score)

    def test_no_temporal_leakage(self):
        with self.repo.session() as con:
            _seed_gh_match(con, 1, 1, 2, 2, 0, "2026-07-05T17:00:00+00:00", 2.0, 0.5)
        features = build_form_features(self.repo, "Mexico", "2026-07-05T17:00:00+00:00")
        self.assertEqual(0, features.matches_played)

    def test_features_reflect_dominant_team(self):
        with self.repo.session() as con:
            _seed_gh_match(con, 1, 1, 2, 3, 0, "2026-06-20T17:00:00+00:00", 2.5, 0.4)
            _seed_event(con, 1, 1, 20, "Goal", 1)
            _seed_event(con, 2, 1, 80, "Goal", 1)
            _seed_event(con, 3, 1, 85, "Goal", 1)
        features = build_form_features(self.repo, "Mexico", "2026-07-05T17:00:00+00:00")
        self.assertEqual(1, features.matches_played)
        self.assertGreater(features.values["xg_delta"], 0)
        self.assertGreater(features.values["late_dominance"], 0)
        self.assertEqual(1.0, features.values["momentum"])

    def test_red_card_creates_inferiority_penalty(self):
        with self.repo.session() as con:
            _seed_gh_match(con, 1, 1, 2, 0, 1, "2026-06-20T17:00:00+00:00")
            _seed_event(con, 1, 1, 30, "Red Card", 1)
        features = build_form_features(self.repo, "Mexico", "2026-07-05T17:00:00+00:00")
        self.assertLess(features.values["inferiority"], 0)


class ApplyAdjustmentTests(unittest.TestCase):
    def test_zero_alpha_is_identity(self):
        probs = {"home": 0.5, "draw": 0.3, "away": 0.2}
        self.assertEqual(probs, apply_form_adjustment(probs, 0.0, 0.8, 0.6))

    def test_positive_score_shifts_towards_home(self):
        probs = {"home": 0.4, "draw": 0.3, "away": 0.3}
        adjusted = apply_form_adjustment(probs, 0.5, 0.8, 0.6)
        self.assertGreater(adjusted["home"], probs["home"])
        self.assertLess(adjusted["away"], probs["away"])
        self.assertAlmostEqual(1.0, sum(adjusted.values()), places=9)


class CalibrationTests(unittest.TestCase):
    def test_uninformative_scores_give_zero_alpha(self):
        samples = [
            {"probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
             "score": 0.5 if i % 2 else -0.5, "weight": 0.5,
             "outcome": "home" if i % 2 == 0 else "away"}
            for i in range(20)
        ]
        calibration = calibrate_alpha(samples)
        self.assertEqual(0.0, calibration.alpha)

    def test_informative_scores_give_positive_alpha(self):
        samples = []
        for i in range(30):
            winner_is_home = i % 2 == 0
            samples.append({
                "probs": {"home": 0.35, "draw": 0.30, "away": 0.35},
                "score": 0.7 if winner_is_home else -0.7,
                "weight": 0.6,
                "outcome": "home" if winner_is_home else "away",
            })
        calibration = calibrate_alpha(samples)
        self.assertGreater(calibration.alpha, 0.0)
        self.assertLess(calibration.log_loss_adjusted, calibration.log_loss_base)

    def test_empty_samples(self):
        calibration = calibrate_alpha([])
        self.assertEqual(0.0, calibration.alpha)
        self.assertEqual(0, calibration.sample_size)


class StratifiedCalibrationTests(unittest.TestCase):
    def _samples(self, matches_min, informative):
        samples = []
        for i in range(30):
            winner_is_home = i % 2 == 0
            score = (0.7 if winner_is_home else -0.7) if informative else (0.7 if i % 3 else -0.7)
            samples.append({
                "probs": {"home": 0.35, "draw": 0.30, "away": 0.35},
                "score": score,
                "weight": 0.5,
                "matches_min": matches_min,
                "outcome": "home" if winner_is_home else "away",
            })
        return samples

    def test_buckets_activate_independently(self):
        from wcpredict.tournament_form_adjustment import calibrate_stratified
        samples = self._samples(3, informative=True) + self._samples(2, informative=False)
        report = calibrate_stratified(samples)
        self.assertGreater(report["3plus"]["alpha"], 0.0)
        self.assertEqual(0.0, report["2"]["alpha"])

    def test_alpha_for_match_selection(self):
        from wcpredict.tournament_form_adjustment import alpha_for_match
        alphas = {"2": 0.9, "3plus": 1.3}
        self.assertEqual(0.0, alpha_for_match(alphas, 0))
        self.assertEqual(0.0, alpha_for_match(alphas, 1))
        self.assertEqual(0.9, alpha_for_match(alphas, 2))
        self.assertEqual(1.3, alpha_for_match(alphas, 3))
        self.assertEqual(1.3, alpha_for_match(alphas, 5))
