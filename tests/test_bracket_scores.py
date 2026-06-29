import unittest

from wcpredict.ui.bracket import bracket_result_display, render_bracket


def _slot(**overrides):
    slot = {
        "match_id": "M89",
        "round": "round_of_16",
        "date": "4 jul",
        "stadium": "Estadio",
        "home": {"name": "España", "crest_html": "", "is_placeholder": False},
        "away": {"name": "Francia", "crest_html": "", "is_placeholder": False},
        "status": "closed",
        "score": [2, 2],
        "winner": "home",
    }
    slot.update(overrides)
    return slot


class BracketScoreRenderingTests(unittest.TestCase):
    def test_phase_result_is_not_double_counted_with_official_match_score(self):
        display = bracket_result_display({
            "goals_a": 2,
            "goals_b": 1,
            "legacy_extra_time_goals_a": None,
            "legacy_extra_time_goals_b": None,
            "legacy_penalty_goals_a": None,
            "legacy_penalty_goals_b": None,
            "regulation_goals_a": 1,
            "regulation_goals_b": 1,
            "phase_extra_time_goals_a": 1,
            "phase_extra_time_goals_b": 0,
            "shootout_goals_a": None,
            "shootout_goals_b": None,
            "decided_in": "extra_time",
        })

        self.assertEqual([2, 1], display["score"])
        self.assertEqual("home", display["winner"])
        self.assertEqual("extra_time", display["decided_in"])

    def test_legacy_result_still_aggregates_separate_extra_time_columns(self):
        display = bracket_result_display({
            "goals_a": 1,
            "goals_b": 1,
            "legacy_extra_time_goals_a": 0,
            "legacy_extra_time_goals_b": 0,
            "legacy_penalty_goals_a": 4,
            "legacy_penalty_goals_b": 5,
            "regulation_goals_a": None,
        })

        self.assertEqual([1, 1], display["score"])
        self.assertEqual([4, 5], display["penalty_score"])
        self.assertEqual("away", display["winner"])
        self.assertEqual("shootout", display["decided_in"])

    def test_shootout_score_is_small_and_parenthesized_per_team(self):
        html = render_bracket([_slot(penalty_score=[5, 4], decided_in="shootout")])

        self.assertIn('<span class="bracket-team-penalty-score">(5)</span>', html)
        self.assertIn('<span class="bracket-team-penalty-score">(4)</span>', html)
        self.assertIn('<span class="bracket-decision-label">Penaltis</span>', html)
        self.assertLess(html.index("bracket-team-score\">2"), html.index("bracket-team-penalty-score\">(5)"))

    def test_extra_time_result_keeps_120_minute_score_and_label(self):
        html = render_bracket([
            _slot(score=[2, 1], penalty_score=None, decided_in="extra_time")
        ])

        self.assertIn('<span class="bracket-team-score">2</span>', html)
        self.assertIn('<span class="bracket-team-score">1</span>', html)
        self.assertIn('<span class="bracket-decision-label">Prórroga</span>', html)
        self.assertNotIn("bracket-team-penalty-score", html)

    def test_regulation_result_does_not_add_a_decision_label(self):
        html = render_bracket([
            _slot(score=[3, 0], penalty_score=None, decided_in="regulation")
        ])

        self.assertNotIn("bracket-decision-label", html)


if __name__ == "__main__":
    unittest.main()
