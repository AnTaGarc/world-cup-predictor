from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from wcpredict.database import initialize_database
from wcpredict.evidence import EvidenceStatus
from wcpredict.repository import Repository


class DatabaseRepositoryTests(unittest.TestCase):
    def test_acquisition_schema_and_evidence_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "worldcup.sqlite"
            initialize_database(db_path)
            with closing(sqlite3.connect(db_path)) as con:
                tables = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
        self.assertTrue(
            {
                "provider_health",
                "provider_entities",
                "historical_matches",
                "screenshot_batches",
                "screenshot_assets",
                "extraction_candidates",
                "review_decisions",
                "settlement_versions",
                "prediction_evaluations",
                "source_catalog",
                "sentiment_snapshots",
                "outcome_model_runs",
                "goalkeeper_penalty_attempts",
            }.issubset(tables)
        )
        self.assertEqual(
            "blocked_by_provider", EvidenceStatus.BLOCKED_BY_PROVIDER.value
        )
        self.assertEqual(
            "verified_user_capture", EvidenceStatus.VERIFIED_USER_CAPTURE.value
        )

    def test_goalkeeper_penalty_attempts_are_separate_idempotent_and_cut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            common = {
                "goalkeeper_name": "Yassine Bounou",
                "transfermarkt_player_id": "207834",
                "competition": "Test",
                "phase": "regular",
                "taker_name": "Taker",
                "opponent_team": "Opponent",
                "match_label": "Test match",
                "source_provider": "transfermarkt",
                "source_url": "https://example.test/keeper",
                "fetched_at_utc": "2026-06-30T10:00:00+00:00",
                "raw": {"cells": ["test"]},
            }
            rows = [
                {
                    **common,
                    "attempted_on": "27/06/2026",
                    "outcome": "saved",
                    "source_row_key": "before",
                },
                {
                    **common,
                    "attempted_on": "01/07/2026",
                    "outcome": "scored",
                    "source_row_key": "after",
                },
            ]

            self.assertEqual(2, repo.save_goalkeeper_penalty_attempts(rows))
            self.assertEqual(2, repo.save_goalkeeper_penalty_attempts(rows))
            stored = repo.list_goalkeeper_penalty_attempts(
                "Yassine Bounou",
                datetime(2026, 6, 30, tzinfo=timezone.utc),
            )
            with repo.session() as con:
                taker_rows = con.execute(
                    "SELECT COUNT(*) FROM penalty_attempts"
                ).fetchone()[0]

        self.assertEqual(["before"], [row["source_row_key"] for row in stored])
        self.assertEqual(0, taker_rows)

    def test_schema_creates_core_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "worldcup.sqlite"
            initialize_database(db_path)
            with closing(sqlite3.connect(db_path)) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"teams", "players", "matches", "team_match_stats", "predictions", "sources"}.issubset(tables))
            self.assertNotIn("manual_odds", tables)

    def test_phase_schema_migrates_without_changing_existing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "worldcup.sqlite"
            initialize_database(db_path)
            with closing(sqlite3.connect(db_path)) as con:
                con.execute("INSERT INTO teams(name) VALUES('Spain')")
                con.execute("INSERT INTO teams(name) VALUES('Germany')")
                team_ids = [row[0] for row in con.execute("SELECT id FROM teams ORDER BY id")]
                con.execute(
                    "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status) "
                    "VALUES('World Cup', 'Round of 32', '2026-06-30T18:00:00+00:00', ?, ?, 'finished')",
                    team_ids,
                )
                match_id = con.execute("SELECT id FROM matches").fetchone()[0]
                con.execute(
                    "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                    "VALUES(?, 2, 1, 'manual', '2026-06-30T21:00:00+00:00')",
                    (match_id,),
                )
                con.commit()

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                score = con.execute(
                    "SELECT goals_a, goals_b FROM match_results WHERE match_id=?", (match_id,)
                ).fetchone()
                observation_columns = {
                    row[1] for row in con.execute("PRAGMA table_info(observations)")
                }

        self.assertTrue(
            {"match_phase_results", "team_match_period_stats", "shootout_kicks"}.issubset(tables)
        )
        self.assertEqual((2, 1), score)
        self.assertIn("period", observation_columns)

    def test_upsert_team_and_match_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            spain_id = repo.upsert_team("Spain", "ESP")
            japan_id = repo.upsert_team("Japan", "JPN")
            match_id = repo.upsert_match(
                competition="FIFA World Cup 2026",
                stage="Group",
                kickoff_utc=datetime(2026, 6, 18, 19, 0, tzinfo=timezone.utc),
                team_a_id=spain_id,
                team_b_id=japan_id,
                status="scheduled",
                venue="Toronto",
            )
            match = repo.get_match(match_id)
            self.assertEqual("Spain vs Japan", match.label)
            self.assertEqual("Toronto", match.venue)

    def test_list_matches_hides_legacy_alias_duplicates_and_keeps_evidence_rich_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            usa_id = repo.upsert_team("USA")
            united_states_id = repo.upsert_team("United States")
            paraguay_id = repo.upsert_team("Paraguay")
            evidence_match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 12, 0, tzinfo=timezone.utc),
                usa_id, paraguay_id, "scheduled",
            )
            empty_duplicate_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
                united_states_id, paraguay_id, "finished",
            )
            with repo.session() as con:
                con.execute(
                    "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) VALUES(?, 4, 1, 'manual', ?)",
                    (evidence_match_id, datetime(2026, 6, 20, tzinfo=timezone.utc).isoformat()),
                )
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, xg, shots, source_id) VALUES(?, ?, 1.42, 16, 'deep')",
                    (evidence_match_id, usa_id),
                )
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, xg, shots, source_id) VALUES(?, ?, 0.54, 9, 'deep')",
                    (evidence_match_id, paraguay_id),
                )

            matches = repo.list_matches()
            labels = [match.label for match in matches]

            self.assertIn("USA vs Paraguay", labels)
            self.assertNotIn("United States vs Paraguay", labels)
            self.assertEqual(1, labels.count("USA vs Paraguay"))
            self.assertEqual(evidence_match_id, matches[0].id)
            self.assertNotEqual(empty_duplicate_id, matches[0].id)

    def test_match_and_evidence_queries_can_filter_by_competition(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            spain_id = repo.upsert_team("Spain")
            japan_id = repo.upsert_team("Japan")
            world_cup_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 18, 19, tzinfo=timezone.utc),
                spain_id, japan_id, "scheduled",
            )
            friendly_id = repo.upsert_match(
                "Friendly", "Friendly",
                datetime(2025, 6, 18, 19, tzinfo=timezone.utc),
                spain_id, japan_id, "finished",
            )

            matches = repo.list_matches(competition="FIFA World Cup 2026")
            evidence = repo.get_all_match_evidence_statuses(
                competition="FIFA World Cup 2026"
            )

        self.assertEqual([world_cup_id], [match.id for match in matches])
        self.assertEqual({world_cup_id}, set(evidence))
        self.assertNotIn(friendly_id, evidence)

    def test_deep_team_metric_observations_can_filter_teams(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            morocco_id = repo.upsert_team("Morocco")
            france_id = repo.upsert_team("France")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 20, 19, tzinfo=timezone.utc),
                morocco_id, france_id, "finished",
            )
            with repo.session() as con:
                for team_name, value in (
                    ("Morocco", 1.4),
                    ("France", 1.1),
                    ("Brazil", 2.2),
                ):
                    con.execute(
                        "INSERT INTO observations("
                        "match_id, subject_type, subject_name, metric, value_number, "
                        "context_json, source_id, evidence_status, observed_at_utc"
                        ") VALUES(?, 'team', ?, 'xg', ?, '{}', ?, "
                        "'verified_user_json', '2026-06-20T22:00:00+00:00')",
                        (match_id, team_name, value, f"source-{team_name}"),
                    )

            cutoff = datetime(2026, 6, 21, tzinfo=timezone.utc)
            filtered = repo.list_deep_team_metric_observations_before(
                cutoff, team_names=("Morocco", "France")
            )
            unfiltered = repo.list_deep_team_metric_observations_before(cutoff)

        self.assertEqual({"Morocco", "France"}, {row["team_name"] for row in filtered})
        self.assertEqual({"Morocco", "France", "Brazil"}, {row["team_name"] for row in unfiltered})

    def test_schema_has_team_profile_hot_path_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "worldcup.sqlite"
            initialize_database(db_path)
            with closing(sqlite3.connect(db_path)) as con:
                indexes = {
                    row[1] for row in con.execute("PRAGMA index_list(observations)")
                }

        self.assertIn("idx_observations_team_profile", indexes)


if __name__ == "__main__":
    unittest.main()
