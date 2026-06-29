from random import Random
import unittest

from wcpredict.penalty_profiles import GoalkeeperPenaltyProfile, PenaltyPlayerProfile
from wcpredict.penalty_shootout_simulator import simulate_scripted_shootout, simulate_shootout
from wcpredict.penalty_substitution_model import ScenarioPlayer


def _profile(name, conversion=0.76, propensity=1.0):
    return PenaltyPlayerProfile(
        player_name=name, position="MF", attempts=0, shootout_attempts=0,
        conversion=conversion, low=0.5, high=0.9, effective_attempts=0.0,
        taker_propensity=propensity, confidence="low",
    )


def _keeper(name, save_rate=0.24):
    return GoalkeeperPenaltyProfile(name, save_rate, 0, 0.0, "global_prior")


class PenaltyShootoutSimulatorTests(unittest.TestCase):
    def test_shootout_stops_when_remaining_kicks_cannot_change_winner(self):
        result = simulate_scripted_shootout([1, 1, 1], [0, 0, 0])
        self.assertEqual("A", result.winner)
        self.assertLess(result.total_kicks, 10)

    def test_sudden_death_uses_each_eligible_player_before_repeating(self):
        result = simulate_scripted_shootout([1] * 7, [1] * 6 + [0])
        self.assertEqual(7, result.team_a_kicks)
        self.assertEqual(result.team_a_kicks, len(result.team_a_unique_takers))
        self.assertEqual(result.team_b_kicks, len(result.team_b_unique_takers))

    def test_same_seed_produces_identical_taker_order_and_result(self):
        players_a = [ScenarioPlayer(f"A{idx}", "GK" if idx == 0 else "CM") for idx in range(11)]
        players_b = [ScenarioPlayer(f"B{idx}", "GK" if idx == 0 else "CM") for idx in range(11)]
        profiles_a = {player.player_name: _profile(player.player_name, propensity=idx + 1) for idx, player in enumerate(players_a)}
        profiles_b = {player.player_name: _profile(player.player_name, propensity=idx + 1) for idx, player in enumerate(players_b)}

        def run_once():
            return simulate_shootout(
                players_a, players_b, profiles_a, profiles_b,
                _keeper("A0"), _keeper("B0"), Random(123),
            )

        self.assertEqual(run_once(), run_once())

    def test_stronger_opposing_keeper_reduces_kick_probability_and_wins_more(self):
        players_a = [ScenarioPlayer(f"A{idx}", "GK" if idx == 0 else "CM") for idx in range(11)]
        players_b = [ScenarioPlayer(f"B{idx}", "GK" if idx == 0 else "CM") for idx in range(11)]
        profiles_a = {player.player_name: _profile(player.player_name) for player in players_a}
        profiles_b = {player.player_name: _profile(player.player_name) for player in players_b}
        wins_a = 0
        for seed in range(400):
            result = simulate_shootout(
                players_a, players_b, profiles_a, profiles_b,
                _keeper("A0", 0.34), _keeper("B0", 0.14), Random(seed),
            )
            wins_a += result.winner == "A"
        self.assertGreater(wins_a, 220)


if __name__ == "__main__":
    unittest.main()
