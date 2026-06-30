from datetime import date
import unittest

from wcpredict.penalty_profiles import (
    GLOBAL_PENALTY_SAVE,
    build_goalkeeper_profile,
    build_player_profile,
    build_player_profiles,
)


class PenaltyProfileTests(unittest.TestCase):
    def test_missing_history_uses_global_prior_not_zero(self):
        profiles = build_player_profiles(
            [{"player_name": "Unknown", "position": "FW"}],
            [],
            date(2026, 6, 28),
        )
        self.assertAlmostEqual(0.76, profiles["Unknown"].conversion, places=2)
        self.assertEqual(0, profiles["Unknown"].attempts)

    def test_recent_shootout_attempts_outweigh_old_regular_attempts(self):
        recent = [{
            "player_name": "Taker", "phase": "shootout", "outcome": "scored",
            "attempted_on": "2026-06-01",
        }]
        old = [{
            "player_name": "Taker", "phase": "regular", "outcome": "scored",
            "attempted_on": "2016-06-01",
        }]
        strong = build_player_profile("Taker", "FW", recent, date(2026, 6, 28))
        weak = build_player_profile("Taker", "FW", old, date(2026, 6, 28))
        self.assertGreater(strong.effective_attempts, weak.effective_attempts)

    def test_transfermarkt_day_first_dates_drive_recency_weighting(self):
        recent = [{
            "player_name": "Taker", "phase": "regular", "outcome": "scored",
            "attempted_on": "01/06/2026",
        }]
        old = [{
            "player_name": "Taker", "phase": "regular", "outcome": "scored",
            "attempted_on": "01/06/2016",
        }]

        strong = build_player_profile("Taker", "FW", recent, date(2026, 6, 28))
        weak = build_player_profile("Taker", "FW", old, date(2026, 6, 28))

        self.assertGreater(strong.effective_attempts, weak.effective_attempts)

    def test_goalkeeper_penalty_history_dominates_general_rate_only_with_sample(self):
        keeper = {"player_name": "Keeper", "save_percentage": 80.0}
        one = [{"goalkeeper_name": "Keeper", "outcome": "saved"}]
        ten = [{"goalkeeper_name": "Keeper", "outcome": "saved"} for _ in range(10)]
        sparse = build_goalkeeper_profile(keeper, one, deep_save_rate=0.80)
        sampled = build_goalkeeper_profile(keeper, ten, deep_save_rate=0.80)
        self.assertLess(abs(sparse.penalty_save_rate - GLOBAL_PENALTY_SAVE), 0.10)
        self.assertGreater(sampled.penalty_history_weight, sparse.penalty_history_weight)

    def test_recent_shootout_goalkeeper_evidence_outweighs_old_regular_evidence(self):
        recent = [{
            "goalkeeper_name": "Keeper", "taker_name": "Taker",
            "phase": "shootout", "outcome": "saved", "attempted_on": "2026-06-01",
        }]
        old = [{
            "goalkeeper_name": "Keeper", "taker_name": "Taker",
            "phase": "regular", "outcome": "saved", "attempted_on": "2016-06-01",
        }]

        strong = build_goalkeeper_profile(
            {"player_name": "Keeper"}, recent, as_of=date(2026, 6, 28)
        )
        weak = build_goalkeeper_profile(
            {"player_name": "Keeper"}, old, as_of=date(2026, 6, 28)
        )

        self.assertGreater(strong.effective_attempts, weak.effective_attempts)
        self.assertEqual(1, strong.shootout_attempts)
        self.assertEqual(1, weak.regular_attempts)

    def test_direct_goalkeeper_rows_make_scored_only_sample_valid(self):
        attempts = [
            {
                "goalkeeper_name": "Keeper", "taker_name": f"Taker {index}",
                "phase": "regular", "outcome": "scored", "attempted_on": "2026-01-01",
            }
            for index in range(5)
        ]

        profile = build_goalkeeper_profile(
            {"player_name": "Keeper"}, attempts, as_of=date(2026, 6, 28)
        )

        self.assertEqual(5, profile.faced_penalties)
        self.assertEqual("penalty_history", profile.source)

    def test_goalkeeper_does_not_get_credit_for_off_target_attempt(self):
        keeper = {"player_name": "Keeper"}
        attempts = [
            {"goalkeeper_name": "Keeper", "outcome": "saved"},
            {"goalkeeper_name": "Keeper", "outcome": "off_target"},
        ]
        profile = build_goalkeeper_profile(keeper, attempts)
        expected = (GLOBAL_PENALTY_SAVE * 12.0 + 1.0) / 13.0
        self.assertEqual(1, profile.faced_penalties)
        self.assertEqual(1, profile.off_target_attempts)
        self.assertAlmostEqual(expected, profile.penalty_save_rate)

    def test_goalkeeper_recovers_saved_outcome_from_legacy_raw_json(self):
        keeper = {"player_name": "Keeper"}
        attempts = [{
            "goalkeeper_name": "Keeper",
            "outcome": "missed",
            "raw_json": '{"cells": ["Saved", "Keeper"]}',
        }]
        profile = build_goalkeeper_profile(keeper, attempts)
        expected = (GLOBAL_PENALTY_SAVE * 12.0 + 1.0) / 13.0
        self.assertAlmostEqual(expected, profile.penalty_save_rate)

    def test_goalkeeper_ignores_one_sided_scored_only_history(self):
        keeper = {"player_name": "Keeper", "save_percentage": 75.0}
        attempts = [
            {"goalkeeper_name": "Keeper", "outcome": "scored"}
            for _ in range(20)
        ]
        profile = build_goalkeeper_profile(keeper, attempts)
        self.assertEqual(0, profile.faced_penalties)
        self.assertEqual("general_save_fallback", profile.source)

    def test_profiles_only_consume_attempts_for_the_matching_player(self):
        attempts = [
            {"player_name": "A", "phase": "regular", "outcome": "scored", "attempted_on": "2026-01-01"},
            {"player_name": "B", "phase": "regular", "outcome": "missed", "attempted_on": "2026-01-01"},
        ]
        profiles = build_player_profiles(
            [{"player_name": "A", "position": "FW"}, {"player_name": "B", "position": "DF"}],
            attempts,
            date(2026, 6, 28),
        )
        self.assertGreater(profiles["A"].conversion, profiles["B"].conversion)
        self.assertEqual(1, profiles["A"].attempts)
        self.assertEqual(1, profiles["B"].attempts)


if __name__ == "__main__":
    unittest.main()
