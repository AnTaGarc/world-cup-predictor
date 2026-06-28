import unittest

from wcpredict.models import MarketFamily
from wcpredict.quality import Confidence
from wcpredict.services import MarketPrediction
from wcpredict.ui.interaction_models import (
    evaluate_odds_rows,
    localized_default_odds_rows,
    prepare_player_match_context,
)


class OddsInteractionTests(unittest.TestCase):
    def setUp(self):
        self.predictions = [
            MarketPrediction(
                MarketFamily.MATCH_RESULT,
                "1X2",
                "Spain",
                None,
                0.50,
                Confidence.HIGH,
                "test",
            ),
            MarketPrediction(
                MarketFamily.MATCH_RESULT,
                "1X2",
                "Draw",
                None,
                0.25,
                Confidence.HIGH,
                "test",
            ),
            MarketPrediction(
                MarketFamily.DRAW_NO_BET,
                "Draw No Bet",
                "Spain",
                None,
                2 / 3,
                Confidence.HIGH,
                "test",
            ),
        ]

    def test_default_rows_are_localized_without_changing_canonical_source(self):
        rows = localized_default_odds_rows("Spain", "Japan")

        self.assertEqual("Resultado del partido", rows[0]["market_family"])
        self.assertEqual("1X2", rows[0]["market_name"])
        self.assertEqual("Spain", rows[0]["selection_name"])

    def test_entered_odds_preserve_current_ev_and_draw_no_bet_push_math(self):
        edited = [
            {
                "market_family": "Resultado del partido",
                "market_name": "1X2",
                "selection_name": "Spain",
                "line": None,
                "decimal_odds": 2.20,
                "bookmaker": "Winamax",
            },
            {
                "market_family": "Empate no válido",
                "market_name": "Empate no válido",
                "selection_name": "Spain",
                "line": None,
                "decimal_odds": 1.80,
                "bookmaker": "Winamax",
            },
        ]

        result = evaluate_odds_rows(self.predictions, edited)

        self.assertEqual(2, len(result.entered))
        self.assertEqual(2, len(result.comparisons))
        self.assertAlmostEqual(0.10, result.comparisons[0].expected_value)
        self.assertAlmostEqual(0.50, result.comparisons[1].probability)
        self.assertAlmostEqual(0.15, result.comparisons[1].expected_value)


class PlayerInteractionTests(unittest.TestCase):
    def test_context_filters_zero_minutes_and_prebuilds_team_rosters(self):
        players = [
            {
                "team_name": "Spain",
                "player_name": "Forward",
                "position": "FW",
                "minutes": 180,
                "games": 2,
                "starts": 2,
                "goals": 1,
                "assists": 0,
                "shots": 5,
                "shots_on_target": 2,
            },
            {
                "team_name": "Spain",
                "player_name": "Keeper",
                "position": "GK",
                "minutes": 180,
                "games": 2,
                "starts": 2,
                "save_percentage": 75.0,
                "saves": 6,
                "goals_conceded": 2,
            },
            {
                "team_name": "Japan",
                "player_name": "Unused",
                "position": "FW",
                "minutes": 0,
            },
        ]

        context = prepare_player_match_context(
            "Spain",
            "Japan",
            players,
            [],
            {"shots_on_target": {"Spain": 5.0, "Japan": 3.0}},
            {"Spain": object(), "Japan": object()},
        )

        spain = context.by_team["Spain"]
        self.assertEqual(
            ["Forward", "Keeper"],
            [row["player_name"] for row in spain.players],
        )
        self.assertEqual(
            ["Forward"],
            [row["player_name"] for row in spain.field_players],
        )
        self.assertEqual(
            ["Keeper"],
            [row["player_name"] for row in spain.goalkeepers],
        )
        self.assertEqual(3.0, spain.opponent_sot_per90)
        self.assertEqual(2, len(spain.roster_rows))
        self.assertEqual(0, len(context.by_team["Japan"].players))

    def test_player_context_cache_builds_once_for_repeated_warm_interactions(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from wcpredict.ui import pages

        repo = Mock()
        repo.list_imported_lineups.return_value = []
        auxiliary = SimpleNamespace(
            team_volume_predictions={},
            goalkeeper_baselines={},
        )
        pages._player_match_context_cached.clear()
        try:
            with patch.object(pages, "_repo", return_value=repo):
                first = pages._player_match_context_cached(
                    77,
                    (100, 200),
                    "test-engine",
                    "Spain",
                    "Japan",
                    [],
                    auxiliary,
                )
                second = pages._player_match_context_cached(
                    77,
                    (100, 200),
                    "test-engine",
                    "Spain",
                    "Japan",
                    [],
                    auxiliary,
                )
            self.assertIs(first, second)
            self.assertEqual(1, repo.list_imported_lineups.call_count)
        finally:
            pages._player_match_context_cached.clear()


if __name__ == "__main__":
    unittest.main()
