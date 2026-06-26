import unittest

from wcpredict.team_corrections import (
    compute_team_market_shifts,
    describe_team_shifts,
)


def _row(team_a, team_b, market, selection, prob, outcome):
    return {
        "team_a": team_a, "team_b": team_b,
        "market": market, "selection": selection,
        "prob_predicted": prob, "outcome_observed": outcome,
    }


class TeamMarketShiftsTests(unittest.TestCase):
    def test_below_min_n_returns_empty(self):
        rows = [_row("A", "B", "1X2", "A", 0.6, 0)]
        self.assertEqual({}, compute_team_market_shifts(rows, min_n=3))

    def test_consistent_overestimation_produces_positive_shift(self):
        rows = [
            _row("A", f"B{i}", "1X2", "A", 0.7, 0)
            for i in range(5)
        ]
        shifts = compute_team_market_shifts(rows, min_n=3, prior_strength=5)
        self.assertIn(("A", "1X2"), shifts)
        # Mean residual = 0.7. Shrunk by 5/(5+5) = 0.5 → 0.35
        self.assertAlmostEqual(shifts[("A", "1X2")], 0.35, places=4)

    def test_symmetric_market_credits_both_teams(self):
        rows = [
            _row("X", "Y", "BTTS", "Yes", 0.55, 1) for _ in range(4)
        ]
        shifts = compute_team_market_shifts(rows, min_n=3, prior_strength=3)
        # Both X and Y should be credited.
        self.assertIn(("X", "BTTS"), shifts)
        self.assertIn(("Y", "BTTS"), shifts)
        # 0.55 - 1 = -0.45 residual, shrunk by 4/(4+3)
        expected = -0.45 * 4 / 7
        self.assertAlmostEqual(shifts[("X", "BTTS")], expected, places=4)

    def test_describe_returns_sorted_lines(self):
        shifts = {
            ("A", "1X2"): 0.10,
            ("B", "BTTS"): -0.30,
            ("C", "O/U"): 0.05,
        }
        lines = describe_team_shifts(shifts, top=2)
        # Largest |shift| first.
        self.assertEqual(2, len(lines))
        self.assertIn("B", lines[0])


if __name__ == "__main__":
    unittest.main()
