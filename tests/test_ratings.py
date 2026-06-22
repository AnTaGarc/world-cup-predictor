from datetime import date, timedelta
import unittest

from wcpredict.ratings import MatchResult, build_team_ratings, deduplicate_results, expected_goals_for_match, explain_team_form


class RatingsTests(unittest.TestCase):
    def test_duplicate_sources_and_team_aliases_count_once(self):
        played = date(2026, 6, 11)
        rows = [
            MatchResult(played, "Mexico", "South Africa", 2, 0, "world_cup"),
            MatchResult(played, "México", "Sudáfrica", 2, 0, "world_cup"),
            MatchResult(played, "Korea Republic", "Czechia", 2, 1, "world_cup"),
            MatchResult(played, "South Korea", "Czech Republic", 2, 1, "world_cup"),
        ]
        unique = deduplicate_results(rows)
        self.assertEqual(2, len(unique))
        self.assertEqual({"Mexico", "South Korea"}, {row.team_a for row in unique})

    def test_recent_results_have_more_weight(self):
        today = date(2026, 6, 18)
        results = [
            MatchResult(today - timedelta(days=900), "A", "B", 0, 3, "competitive"),
            MatchResult(today - timedelta(days=5), "A", "C", 3, 0, "world_cup"),
        ]
        ratings = build_team_ratings(results, as_of=today)
        self.assertGreater(ratings["A"].attack, 1.0)

    def test_friendlies_are_down_weighted(self):
        today = date(2026, 6, 18)
        results = [
            MatchResult(today - timedelta(days=10), "A", "B", 6, 0, "friendly"),
            MatchResult(today - timedelta(days=10), "C", "D", 2, 0, "competitive"),
        ]
        ratings = build_team_ratings(results, as_of=today)
        self.assertLess(ratings["A"].sample_weight, ratings["C"].sample_weight)

    def test_expected_goals_use_attack_and_defense(self):
        today = date(2026, 6, 18)
        results = [
            MatchResult(today - timedelta(days=20), "A", "B", 2, 0, "competitive"),
            MatchResult(today - timedelta(days=15), "A", "C", 3, 1, "world_cup"),
            MatchResult(today - timedelta(days=15), "B", "C", 0, 1, "competitive"),
        ]
        ratings = build_team_ratings(results, as_of=today)
        xg_a, xg_b = expected_goals_for_match("A", "B", ratings, base_goals_per_team=1.25)
        self.assertGreater(xg_a, xg_b)

    def test_form_ledger_uses_every_past_match_and_excludes_future(self):
        as_of = date(2026, 6, 18)
        results = [
            MatchResult(as_of - timedelta(days=30), "Canada", "Mexico", 1, 2, "competitive"),
            MatchResult(as_of - timedelta(days=5), "Canada", "Qatar", 3, 0, "world_cup"),
            MatchResult(as_of + timedelta(days=1), "Canada", "Spain", 0, 5, "world_cup"),
        ]
        ledger = explain_team_form("Canada", results, as_of)
        self.assertEqual(2, len(ledger))
        self.assertEqual({"Mexico", "Qatar"}, {row.opponent for row in ledger})
        self.assertGreater(ledger[1].total_weight, ledger[0].total_weight)
        self.assertTrue(all(row.explanation for row in ledger))

    def test_form_ledger_canonicalizes_requested_team_alias(self):
        as_of = date(2026, 6, 19)
        results = [
            MatchResult(as_of - timedelta(days=7), "USA", "Paraguay", 4, 1, "world_cup"),
        ]
        ledger = explain_team_form("United States", results, as_of)
        self.assertEqual(1, len(ledger))
        self.assertEqual("Paraguay", ledger[0].opponent)
        self.assertEqual(4, ledger[0].goals_for)


if __name__ == "__main__":
    unittest.main()
