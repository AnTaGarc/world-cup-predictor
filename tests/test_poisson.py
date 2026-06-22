import unittest

from wcpredict.poisson import (
    expected_score,
    most_probable_score,
    poisson_probability,
    score_matrix,
    score_matrix_negative_binomial,
    summarize_score_matrix,
    top_n_scores,
)


class PoissonTests(unittest.TestCase):
    def test_poisson_probability(self):
        self.assertAlmostEqual(0.3542913, poisson_probability(1, 1.3), places=6)

    def test_score_matrix_sums_close_to_one(self):
        matrix = score_matrix(1.4, 0.9, max_goals=10)
        total = sum(sum(row) for row in matrix)
        self.assertGreater(total, 0.995)
        self.assertLessEqual(total, 1.0)

    def test_summary_contains_1x2_over_and_btts(self):
        summary = summarize_score_matrix(score_matrix(1.5, 1.0, max_goals=10), total_line=2.5)
        self.assertGreater(summary.team_a_win, summary.team_b_win)
        self.assertGreater(summary.over_total, 0.0)
        self.assertGreater(summary.both_teams_to_score, 0.0)
        self.assertAlmostEqual(1.0, summary.team_a_win + summary.draw + summary.team_b_win, places=3)

    def test_most_probable_score_uses_normalized_score_matrix(self):
        matrix = score_matrix(1.5, 1.0, max_goals=10)
        result = most_probable_score(matrix)
        self.assertEqual((1, 1), (result.team_a_goals, result.team_b_goals))
        self.assertAlmostEqual(
            result.probability,
            matrix[1][0] / sum(sum(row) for row in matrix),
        )

    def test_top_n_scores_returns_descending_alternatives(self):
        matrix = score_matrix(1.5, 1.0, max_goals=10)
        scores = top_n_scores(matrix, n=3)
        self.assertEqual(3, len(scores))
        for previous, current in zip(scores, scores[1:]):
            self.assertGreaterEqual(previous.probability, current.probability)
        # All probabilities should be normalised against the total mass.
        for score in scores:
            self.assertGreater(score.probability, 0.0)
            self.assertLess(score.probability, 1.0)

    def test_expected_score_recovers_input_xg(self):
        matrix = score_matrix(1.4, 0.9, max_goals=10)
        expected_a, expected_b = expected_score(matrix)
        # max_goals=10 captures > 99% of the mass; expectation should be within 1%.
        self.assertAlmostEqual(expected_a, 1.4, places=2)
        self.assertAlmostEqual(expected_b, 0.9, places=2)

    def test_dixon_coles_rho_inflates_1_1_and_deflates_1_0(self):
        plain = score_matrix(1.4, 0.9, max_goals=10)
        adjusted = score_matrix(1.4, 0.9, max_goals=10, rho=-0.10)
        self.assertGreater(adjusted[1][1], plain[1][1])
        self.assertLess(adjusted[1][0], plain[1][0])
        # Each cell stays a valid probability.
        for row in adjusted:
            for value in row:
                self.assertGreaterEqual(value, 0.0)

    def test_negative_binomial_with_zero_dispersion_matches_poisson(self):
        plain = score_matrix(1.4, 0.9, max_goals=8)
        nb = score_matrix_negative_binomial(1.4, 0.9, dispersion=0.0, max_goals=8)
        for a in range(9):
            for b in range(9):
                self.assertAlmostEqual(plain[a][b], nb[a][b], places=10)

    def test_negative_binomial_with_positive_dispersion_lifts_high_score_cells(self):
        plain = score_matrix(1.6, 1.3, max_goals=10)
        fat_tail = score_matrix_negative_binomial(1.6, 1.3, dispersion=0.20, max_goals=10)
        # Tail cells (combined goal count >= 4) should accumulate more mass with NB.
        plain_tail = sum(plain[a][b] for a in range(11) for b in range(11) if a + b >= 4)
        nb_tail = sum(fat_tail[a][b] for a in range(11) for b in range(11) if a + b >= 4)
        self.assertGreater(nb_tail, plain_tail)


if __name__ == "__main__":
    unittest.main()
