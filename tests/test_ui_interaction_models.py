import unittest

from wcpredict.ui.interaction_models import prepare_player_match_context


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
