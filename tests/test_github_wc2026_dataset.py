import unittest
from pathlib import Path

from wcpredict.github_wc2026_dataset import (
    parse_events_rows,
    parse_team_stats_rows,
    parse_lineups_rows,
    parse_matches_rows,
    parse_referees_rows,
    parse_player_stats_rows,
    parse_teams_rows,
)


FIXTURES = Path(__file__).parent / "fixtures" / "github_wc2026"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    def test_events(self):
        rows = parse_events_rows(_read("match_events.csv"))
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {
                "external_event_id": 1,
                "external_match_id": 1,
                "minute": 9,
                "event_type": "Goal",
                "external_team_id": 1,
                "external_player_id": 16,
            },
            rows[0],
        )

    def test_team_stats_null_potm_becomes_none(self):
        rows = parse_team_stats_rows(_read("match_team_stats.csv"))
        self.assertEqual(2, len(rows))
        self.assertIsNone(rows[1]["player_of_the_match"])
        self.assertEqual(57.0, rows[0]["possession_pct"])
        self.assertEqual(16, rows[0]["total_shots"])

    def test_lineups(self):
        rows = parse_lineups_rows(_read("match_lineups.csv"))
        self.assertEqual(1, rows[0]["is_starting_xi"])
        self.assertEqual(0, rows[1]["is_starting_xi"])
        self.assertEqual("GK", rows[0]["tactical_position"])
        self.assertEqual(14, rows[1]["minutes_played"])

    def test_matches_optional_score_is_none(self):
        rows = parse_matches_rows(_read("matches_detailed.csv"))
        self.assertEqual(2, len(rows))
        finished, scheduled = rows
        self.assertEqual(2, finished["home_score"])
        self.assertEqual("MEX", finished["home_fifa_code"])
        self.assertEqual("Szymon Marciniak", finished["referee_name"])
        self.assertIsNone(scheduled["home_score"])
        self.assertIsNone(scheduled["away_score"])
        self.assertEqual("Scheduled", scheduled["status"])

    def test_referees(self):
        rows = parse_referees_rows(_read("referees.csv"))
        self.assertEqual(2, len(rows))
        self.assertEqual("Szymon Marciniak", rows[0]["referee_name"])
        self.assertEqual(4.2, rows[0]["avg_cards_per_game"])

    def test_player_stats_optional_numbers(self):
        rows = parse_player_stats_rows(_read("player_stats.csv"))
        self.assertEqual(2, len(rows))
        keeper = rows[0]
        self.assertEqual("GK", keeper["position"])
        self.assertIsNone(keeper["shots"])
        self.assertEqual(4, keeper["clean_sheets"])
        forward = rows[1]
        self.assertEqual(3, forward["goals"])
        self.assertEqual(7.9, forward["average_rating"])

    def test_teams_missing_group_letter(self):
        rows = parse_teams_rows(_read("teams.csv"))
        self.assertEqual("Cabo Verde", rows[1]["team_name"])
        self.assertIsNone(rows[1]["group_letter"])
        self.assertEqual(80, rows[1]["fifa_ranking_pre_tournament"])
