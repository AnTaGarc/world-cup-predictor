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

    def test_goalkeeper_penalty_history_dominates_general_rate_only_with_sample(self):
        keeper = {"player_name": "Keeper", "save_percentage": 80.0}
        one = [{"goalkeeper_name": "Keeper", "outcome": "missed"}]
        ten = [{"goalkeeper_name": "Keeper", "outcome": "missed"} for _ in range(10)]
        sparse = build_goalkeeper_profile(keeper, one, deep_save_rate=0.80)
        sampled = build_goalkeeper_profile(keeper, ten, deep_save_rate=0.80)
        self.assertLess(abs(sparse.penalty_save_rate - GLOBAL_PENALTY_SAVE), 0.10)
        self.assertGreater(sampled.penalty_history_weight, sparse.penalty_history_weight)

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
