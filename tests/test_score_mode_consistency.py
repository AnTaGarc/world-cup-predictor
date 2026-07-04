import unittest
from datetime import date

from wcpredict.ratings import MatchResult
from wcpredict.services import _conditional_mode_score, predict_match_markets


class ConditionalModeTests(unittest.TestCase):
    def test_conditional_mode_restricted_to_outcome(self):
        # 0-0 dominates globally, but home's best cell is 1-0.
        matrix = [
            [0.30, 0.10, 0.02],
            [0.20, 0.15, 0.03],
            [0.10, 0.06, 0.04],
        ]
        (score, probability) = _conditional_mode_score(matrix, "home")
        self.assertEqual((1, 0), score)
        self.assertAlmostEqual(0.20, probability)
        (score_away, _) = _conditional_mode_score(matrix, "away")
        self.assertEqual((0, 1), score_away)
        (score_draw, _) = _conditional_mode_score(matrix, "draw")
        self.assertEqual((0, 0), score_draw)


class FavoriteModeRowTests(unittest.TestCase):
    def _low_scoring_favorite_results(self):
        rows = []
        day = 1
        # Alpha edges Beta-like rivals 1-0 or draws 0-0: slight favorite,
        # low expected goals -> global modal score is a draw.
        for _ in range(4):
            rows.append(MatchResult(date(2026, 6, day), "Alpha", "Beta", 1, 0, "world_cup")); day += 1
            rows.append(MatchResult(date(2026, 6, day), "Alpha", "Beta", 0, 0, "world_cup")); day += 1
            rows.append(MatchResult(date(2026, 6, day), "Beta", "Gamma", 0, 0, "world_cup")); day += 1
        return rows

    def test_favorite_conditional_row_appears_when_mode_is_draw(self):
        preds = predict_match_markets(
            "Alpha", "Beta", self._low_scoring_favorite_results(), date(2026, 7, 1)
        )
        x12 = {p.selection_name: p.probability for p in preds if p.market_name == "1X2"}
        exact = next(p for p in preds if p.market_name == "Exact Score")
        favorite = max(x12, key=x12.get)
        goals = [int(x) for x in exact.selection_name.split("-")]
        if favorite != "Draw" and goals[0] == goals[1]:
            conditional = next(
                (p for p in preds if p.market_name == "Exact Score (favorito)"), None
            )
            self.assertIsNotNone(conditional)
            cond_goals = [int(x) for x in conditional.selection_name.split("-")]
            self.assertNotEqual(cond_goals[0], cond_goals[1])
        else:
            self.skipTest("fixture did not produce a draw-mode favorite scenario")
