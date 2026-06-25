import unittest
from datetime import datetime, timezone

from wcpredict.group_context import draw_incentive_for_match
from wcpredict.models import Match, Team
from wcpredict.ratings import MatchResult


def _match(mid: int, stage: str, team_a: str, team_b: str, day: int, status: str = "scheduled") -> Match:
    return Match(
        id=mid,
        competition="FIFA World Cup 2026",
        stage=stage,
        kickoff_utc=datetime(2026, 6, day, 18, tzinfo=timezone.utc),
        team_a=Team(mid * 2, team_a),
        team_b=Team(mid * 2 + 1, team_b),
        status=status,
    )


class GroupContextTests(unittest.TestCase):
    def test_draw_incentive_activates_when_both_teams_clinch_top_two_with_draw(self):
        stage = "Group stage - Group A"
        fixtures = [
            _match(1, stage, "Alpha", "Charlie", 12, "finished"),
            _match(2, stage, "Bravo", "Delta", 12, "finished"),
            _match(3, stage, "Alpha", "Delta", 16, "finished"),
            _match(4, stage, "Bravo", "Charlie", 16, "finished"),
            _match(5, stage, "Alpha", "Bravo", 20),
            _match(6, stage, "Charlie", "Delta", 20),
        ]
        results = [
            MatchResult(fixtures[0].kickoff_utc.date(), "Alpha", "Charlie", 1, 0, "world_cup"),
            MatchResult(fixtures[1].kickoff_utc.date(), "Bravo", "Delta", 2, 0, "world_cup"),
            MatchResult(fixtures[2].kickoff_utc.date(), "Alpha", "Delta", 1, 0, "world_cup"),
            MatchResult(fixtures[3].kickoff_utc.date(), "Bravo", "Charlie", 1, 0, "world_cup"),
        ]

        context = draw_incentive_for_match(fixtures[4], fixtures, results)

        self.assertTrue(context.active)
        self.assertGreater(context.logit_boost, 0.0)
        self.assertIn("clasificados", context.explanation)

    def test_draw_incentive_stays_inactive_without_group_table_pressure(self):
        stage = "Group stage - Group B"
        fixtures = [
            _match(1, stage, "Alpha", "Bravo", 12),
            _match(2, stage, "Charlie", "Delta", 12),
            _match(3, stage, "Alpha", "Charlie", 16),
            _match(4, stage, "Bravo", "Delta", 16),
            _match(5, stage, "Alpha", "Delta", 20),
            _match(6, stage, "Bravo", "Charlie", 20),
        ]

        context = draw_incentive_for_match(fixtures[0], fixtures, [])

        self.assertFalse(context.active)
        self.assertEqual(0.0, context.logit_boost)


if __name__ == "__main__":
    unittest.main()
