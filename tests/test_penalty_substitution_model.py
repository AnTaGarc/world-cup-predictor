from random import Random
import unittest

from wcpredict.penalty_substitution_model import (
    MatchWindowState,
    SubstitutionConfig,
    normalize_role,
    simulate_substitution_path,
)


def _squad():
    roles = ["GK", "CB", "CB", "LB", "RB", "DM", "CM", "AM", "LW", "RW", "ST"]
    players = [
        {"player_name": f"Starter {idx}", "position": role, "starts": 3, "games": 3, "minutes": 250}
        for idx, role in enumerate(roles)
    ]
    bench_roles = ["GK", "CB", "LB", "DM", "CM", "AM", "LW", "RW", "ST", "ST"]
    players.extend(
        {"player_name": f"Bench {idx}", "position": role, "starts": 0, "games": 2, "minutes": 55}
        for idx, role in enumerate(bench_roles)
    )
    return players


class PenaltySubstitutionModelTests(unittest.TestCase):
    def setUp(self):
        self.squad = _squad()
        self.lineup = [f"Starter {idx}" for idx in range(11)]
        self.config = SubstitutionConfig(change_probability=1.0, max_per_window=1)

    def test_position_normalization_handles_detailed_and_coarse_roles(self):
        self.assertEqual("FB", normalize_role("Left-Back"))
        self.assertEqual("ST", normalize_role("Centre-Forward"))
        self.assertEqual("CM", normalize_role("MF"))

    def test_neutral_changes_are_role_preserving_and_players_cannot_return(self):
        windows = [MatchWindowState(minute=minute, score_delta=0) for minute in (60, 70, 82, 98, 112)]
        state = simulate_substitution_path(self.squad, self.lineup, windows, Random(7), self.config)
        self.assertEqual(11, len(state.players))
        self.assertEqual(1, sum(player.role == "GK" for player in state.players))
        self.assertTrue(all(event.role_distance <= 1 for event in state.events))
        self.assertTrue(set(event.out_player for event in state.events).isdisjoint(state.player_names))

    def test_trailing_state_increases_attacking_changes(self):
        def attacking_changes(score_delta):
            count = 0
            for seed in range(150):
                windows = [MatchWindowState(minute=m, score_delta=score_delta) for m in (60, 70, 82)]
                state = simulate_substitution_path(self.squad, self.lineup, windows, Random(seed), self.config)
                count += sum(event.attacking_change for event in state.events)
            return count

        self.assertGreater(attacking_changes(-1), attacking_changes(1))

    def test_substitution_limits_and_extra_time_change_are_enforced(self):
        windows = [MatchWindowState(minute=m, score_delta=0) for m in (56, 62, 68, 74, 80, 88, 95, 104, 111, 118)]
        state = simulate_substitution_path(self.squad, self.lineup, windows, Random(11), self.config)
        self.assertLessEqual(state.regulation_substitutions, 5)
        self.assertLessEqual(state.extra_time_substitutions, 1)
        self.assertLessEqual(len(state.events), 6)

    def test_unused_regulation_substitutions_carry_into_extra_time(self):
        windows = [MatchWindowState(minute=m, score_delta=0) for m in (60, 70, 80, 95, 105, 115)]
        state = simulate_substitution_path(self.squad, self.lineup, windows, Random(21), self.config)
        self.assertEqual(5, state.regulation_substitutions)
        self.assertEqual(1, state.extra_time_substitutions)
        self.assertEqual(6, len(state.events))


if __name__ == "__main__":
    unittest.main()
