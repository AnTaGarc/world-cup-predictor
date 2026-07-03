import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.github_wc2026_enrichment import (
    GH_TEAM_METRIC_MAP,
    build_synthetic_penalty_attempts,
    goalkeeper_tournament_save_rate,
)
from wcpredict.repository import Repository


class MetricMapTests(unittest.TestCase):
    def test_map_covers_expected_columns(self):
        self.assertEqual(
            {"possession_pct", "total_shots", "shots_on_target", "corners",
             "fouls", "offsides", "saves"},
            set(GH_TEAM_METRIC_MAP),
        )
        self.assertEqual(
            "resumen_del_partido.faltas", GH_TEAM_METRIC_MAP["fouls"]
        )
        self.assertEqual("ataque.fueras_de_juego", GH_TEAM_METRIC_MAP["offsides"])
        self.assertEqual("porteria.paradas", GH_TEAM_METRIC_MAP["saves"])


class SyntheticAttemptTests(unittest.TestCase):
    def test_emits_one_attempt_per_penalty_goal_before_cutoff(self):
        rows = [{
            "external_player_id": 16, "player_name": "Julián Andrés Quinones",
            "team_name": "Mexico", "penalty_goals": 2,
            "last_verified": "2026-07-01",
        }]
        attempts = build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        self.assertEqual(2, len(attempts))
        self.assertEqual("scored", attempts[0]["outcome"])
        self.assertEqual("regular", attempts[0]["phase"])
        self.assertEqual("github_wc2026", attempts[0]["source_provider"])
        self.assertEqual("ghps:16:0", attempts[0]["source_row_key"])
        self.assertEqual("ghps:16:1", attempts[1]["source_row_key"])

    def test_excludes_rows_verified_after_kickoff(self):
        rows = [{
            "external_player_id": 16, "player_name": "A", "team_name": "T",
            "penalty_goals": 1, "last_verified": "2026-07-05",
        }]
        self.assertEqual(
            [], build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        )

    def test_excludes_zero_or_null_penalty_goals(self):
        rows = [
            {"external_player_id": 1, "player_name": "A", "team_name": "T",
             "penalty_goals": 0, "last_verified": "2026-07-01"},
            {"external_player_id": 2, "player_name": "B", "team_name": "T",
             "penalty_goals": None, "last_verified": "2026-07-01"},
        ]
        self.assertEqual(
            [], build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        )


class GoalkeeperRateTests(unittest.TestCase):
    def test_rate_with_enough_sample(self):
        self.assertAlmostEqual(
            5 / 6,
            goalkeeper_tournament_save_rate({"saves": 5, "goals_conceded": 1}),
        )

    def test_small_sample_returns_none(self):
        self.assertIsNone(
            goalkeeper_tournament_save_rate({"saves": 1, "goals_conceded": 1})
        )

    def test_null_fields_return_none(self):
        self.assertIsNone(goalkeeper_tournament_save_rate({"saves": None, "goals_conceded": 2}))
        self.assertIsNone(goalkeeper_tournament_save_rate({"saves": 3, "goals_conceded": None}))
