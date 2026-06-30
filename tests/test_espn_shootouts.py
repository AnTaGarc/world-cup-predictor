import unittest

from wcpredict.espn_shootouts import parse_shootout_shots


class EspnShootoutTests(unittest.TestCase):
    def test_parser_interleaves_team_arrays_and_preserves_unknown_miss_type(self):
        payload = {
            "shootout": [
                {
                    "team": "Morocco",
                    "shots": [
                        {"id": "103", "player": "Hakim Ziyech", "shotNumber": 2, "didScore": True},
                        {"id": "101", "player": "Abdelhamid Sabiri", "shotNumber": 1, "didScore": True},
                    ],
                },
                {
                    "team": "Spain",
                    "shots": [
                        {"id": "104", "player": "Carlos Soler", "shotNumber": 2, "didScore": False},
                        {"id": "102", "player": "Pablo Sarabia", "shotNumber": 1, "didScore": False},
                    ],
                },
            ]
        }

        rows = parse_shootout_shots(payload)

        self.assertEqual([1, 2, 3, 4], [row["sequence_number"] for row in rows])
        self.assertEqual(
            ["Morocco", "Spain", "Morocco", "Spain"],
            [row["team_name"] for row in rows],
        )
        self.assertEqual(["scored", "missed", "scored", "missed"], [row["outcome"] for row in rows])
        self.assertTrue(all(row["goalkeeper_name"] == "" for row in rows))


if __name__ == "__main__":
    unittest.main()
