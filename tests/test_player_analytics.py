import unittest

from wcpredict.player_analytics import build_player_profiles, cluster_player_styles


class PlayerAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for player, goals, assists, shots, passes in [
            ("A", 5, 1, 22, 120), ("B", 1, 6, 9, 260),
            ("C", 0, 1, 3, 330), ("D", 3, 2, 18, 150),
            ("E", 0, 4, 5, 290), ("F", 4, 0, 25, 90),
        ]:
            self.rows.append({"player_name": player, "team_name": "T", "minutes": 360, "goals": goals, "assists": assists, "shots": shots, "shots_on_target": shots // 2, "passes": passes, "yellow_cards": 1})

    def test_profiles_include_per90_impact_and_sample(self):
        profiles = build_player_profiles(self.rows, min_minutes=180)
        self.assertEqual(len(profiles), 6)
        self.assertIn("goals_per90", profiles[0])
        self.assertIn("impact", profiles[0])
        self.assertEqual(profiles[0]["matches"], 1)

    def test_clustering_is_deterministic_and_guarded(self):
        profiles = build_player_profiles(self.rows, min_minutes=180)
        first = cluster_player_styles(profiles, requested_clusters=3)
        second = cluster_player_styles(profiles, requested_clusters=3)
        self.assertEqual([x["style_cluster"] for x in first], [x["style_cluster"] for x in second])
        self.assertEqual(cluster_player_styles(profiles[:2], requested_clusters=3), [])

    def test_missing_passes_stay_unknown_instead_of_becoming_zero(self):
        rows = [
            {"player_name": "A", "team_name": "T", "minutes": 180, "goals": 2, "assists": 1, "shots": 8, "shots_on_target": 4, "passes": None, "yellow_cards": 0},
            {"player_name": "B", "team_name": "T", "minutes": 180, "goals": 1, "assists": 0, "shots": 5, "shots_on_target": 2, "passes": None, "yellow_cards": 1},
        ]

        profiles = build_player_profiles(rows, min_minutes=90)

        self.assertEqual(2, len(profiles))
        self.assertNotIn("passes", profiles[0])
        self.assertNotIn("passes_per90", profiles[0])
        self.assertIn("impact", profiles[0])

    def test_zero_minutes_rows_are_not_divided_by_zero(self):
        rows = [
            {"player_name": "Unused", "team_name": "T", "minutes": 0, "goals": 0, "passes": None},
            {"player_name": "Used", "team_name": "T", "minutes": 90, "goals": 1, "passes": 40},
        ]

        profiles = build_player_profiles(rows, min_minutes=0)

        self.assertEqual(["Used"], [row["player_name"] for row in profiles])


if __name__ == "__main__":
    unittest.main()
