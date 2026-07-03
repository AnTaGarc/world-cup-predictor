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


class ObservationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc).isoformat()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status) VALUES(100, 'FIFA World Cup 2026', 'Group Stage', "
                "'2026-06-11T20:00:00+00:00', 1, 2, 'finished')"
            )
            con.execute(
                "INSERT INTO gh_match_team_stats(provider_id, external_match_id, match_id, "
                "external_team_id, team_id, possession_pct, total_shots, shots_on_target, "
                "corners, fouls, offsides, saves, last_updated, imported_at_utc) "
                "VALUES('github_wc2026_team_stats', 1, 100, 1, 1, 57, 16, 4, 6, 11, 2, 1, "
                "'2026-06-24', ?)",
                (self.now,),
            )

    def tearDown(self):
        self.directory.cleanup()

    def test_sync_creates_observations_with_mapped_metrics(self):
        count = self.repo.sync_gh_team_stats_to_observations(self.now)
        self.assertEqual(7, count)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT value_number, evidence_status, source_id FROM observations "
                "WHERE match_id=100 AND subject_name='Mexico' "
                "AND metric='resumen_del_partido.faltas'"
            ).fetchone()
        self.assertEqual(11, row["value_number"])
        self.assertEqual("verified_external", row["evidence_status"])
        self.assertEqual("github_wc2026", row["source_id"])

    def test_sync_is_idempotent(self):
        self.repo.sync_gh_team_stats_to_observations(self.now)
        self.repo.sync_gh_team_stats_to_observations(self.now)
        with self.repo.session() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026'"
            ).fetchone()[0]
        self.assertEqual(7, count)

    def test_unresolved_rows_are_skipped(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO gh_match_team_stats(provider_id, external_match_id, "
                "external_team_id, fouls, imported_at_utc) "
                "VALUES('github_wc2026_team_stats', 2, 9, 20, ?)",
                (self.now,),
            )
        self.repo.sync_gh_team_stats_to_observations(self.now)
        with self.repo.session() as con:
            orphan = con.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026' "
                "AND match_id NOT IN (SELECT id FROM matches)"
            ).fetchone()[0]
        self.assertEqual(0, orphan)
