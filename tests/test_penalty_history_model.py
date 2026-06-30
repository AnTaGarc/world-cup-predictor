import unittest

from wcpredict.knockout_model import predict_knockout_match
from wcpredict.penalty_profiles import GoalkeeperPenaltyProfile
from wcpredict.penalty_history_model import (
    _starting_goalkeeper_profile,
    build_penalty_match_context,
)
from wcpredict.penalty_substitution_model import SubstitutionConfig


class PenaltyHistoryModelTests(unittest.TestCase):
    @staticmethod
    def _squads():
        def squad(prefix):
            roles = ["GK", "CB", "CB", "LB", "RB", "DM", "CM", "AM", "LW", "RW", "ST"]
            players = [
                {"player_name": f"{prefix}{idx}", "position": role, "starts": 3, "games": 3, "minutes": 250}
                for idx, role in enumerate(roles)
            ]
            players.extend(
                {"player_name": f"{prefix}B{idx}", "position": role, "starts": 0, "games": 2, "minutes": 50}
                for idx, role in enumerate(("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"))
            )
            return players
        return {"A": squad("A"), "B": squad("B")}

    def test_no_penalty_history_keeps_shootout_symmetric(self):
        context = build_penalty_match_context("Canada", "South Africa", [])
        self.assertAlmostEqual(0.5, context.team_a_shootout_win_probability, places=6)
        self.assertIn("Sin penalty_history", context.explanation)

    def test_team_conversion_moves_shootout_probability_but_is_capped(self):
        attempts = []
        for idx in range(10):
            attempts.append({
                "team_name": "Canada",
                "outcome": "scored",
                "attempted_on": f"2026-06-{10+idx:02d}",
                "source_row_key": f"canada-{idx}",
            })
        for idx in range(10):
            attempts.append({
                "team_name": "South Africa",
                "outcome": "missed" if idx < 6 else "scored",
                "attempted_on": f"2026-06-{10+idx:02d}",
                "source_row_key": f"sa-{idx}",
            })
        context = build_penalty_match_context("Canada", "South Africa", attempts)
        self.assertGreater(context.team_a_shootout_win_probability, 0.55)
        self.assertLessEqual(context.team_a_shootout_win_probability, 0.64)

    def test_knockout_model_accepts_penalty_history_probability(self):
        neutral = predict_knockout_match(1.2, 1.2)
        adjusted = predict_knockout_match(1.2, 1.2, home_penalty_win_probability=0.64)
        self.assertGreater(adjusted.home_advances, neutral.home_advances)
        self.assertAlmostEqual(adjusted.home_advances + adjusted.away_advances, 1.0, places=6)

    def test_full_context_is_deterministic_and_probabilities_sum_to_one(self):
        squads = self._squads()
        first = build_penalty_match_context("A", "B", [], squads=squads, seed=77, simulations=500)
        second = build_penalty_match_context("A", "B", [], squads=squads, seed=77, simulations=500)
        self.assertEqual(first, second)
        self.assertAlmostEqual(
            1.0,
            first.team_a_shootout_win_probability + first.team_b_shootout_win_probability,
        )
        self.assertEqual(500, first.simulations)
        self.assertEqual(38, len(first.player_rows))

    def test_empty_taker_history_still_uses_goalkeeper_signal(self):
        squads = self._squads()
        weak = GoalkeeperPenaltyProfile("A0", 0.12, 10, 0.5, "test")
        strong = GoalkeeperPenaltyProfile("B0", 0.38, 10, 0.5, "test")
        context = build_penalty_match_context(
            "A", "B", [], squads=squads,
            goalkeeper_profiles={"A": weak, "B": strong},
            seed=9, simulations=800,
        )
        self.assertLess(context.team_a_shootout_win_probability, 0.43)
        self.assertGreater(context.coverage.squad_players, 22)

    def test_starting_goalkeeper_is_fixed_from_confirmed_lineup(self):
        squad = [
            {"player_name": "Usual Starter", "position": "GK", "starter_probability": 0.9},
            {"player_name": "Confirmed Starter", "position": "GK", "starter_probability": 0.1},
        ]

        profile = _starting_goalkeeper_profile(
            "Team", squad, ["Confirmed Starter"], [], None,
            {"Confirmed Starter": 0.80, "Usual Starter": 0.60},
        )

        self.assertEqual("Confirmed Starter", profile.player_name)

    def test_starting_goalkeeper_falls_back_to_highest_start_probability(self):
        squad = [
            {"player_name": "Starter", "position": "GK", "starter_probability": 0.85},
            {"player_name": "Backup", "position": "GK", "starter_probability": 0.15},
        ]

        profile = _starting_goalkeeper_profile(
            "Team", squad, [], [], None,
            {"Starter": 0.70, "Backup": 0.90},
        )

        self.assertEqual("Starter", profile.player_name)

    def test_starting_goalkeeper_remains_on_field_for_every_shootout_path(self):
        squads = self._squads()
        context = build_penalty_match_context(
            "A",
            "B",
            [],
            squads=squads,
            lineups={"A": ["A0"], "B": ["B0"]},
            seed=4,
            simulations=300,
            substitution_config=SubstitutionConfig(
                change_probability=1.0,
                max_per_window=2,
            ),
        )

        goalkeeper_rows = {
            (row.team_name, row.player_name): row.on_field_probability
            for row in context.player_rows
            if row.role == "GK"
        }
        self.assertEqual(1.0, goalkeeper_rows[("A", "A0")])
        self.assertEqual(0.0, goalkeeper_rows[("A", "AB0")])
        self.assertEqual(1.0, goalkeeper_rows[("B", "B0")])
        self.assertEqual(0.0, goalkeeper_rows[("B", "BB0")])
        self.assertIn("porteros titulares fijos (A0 y B0)", context.explanation)


if __name__ == "__main__":
    unittest.main()
