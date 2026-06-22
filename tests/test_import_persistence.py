from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.collector_store import CollectorEventBundle
from wcpredict.repository import Repository
from wcpredict.sofascore import SofaScoreImport
from wcpredict.sentiment import normalize_sentiment_snapshot
from wcpredict.source_catalog import default_source_catalog


def bundle() -> CollectorEventBundle:
    now = datetime(2026, 6, 18, 14, 0, tzinfo=timezone.utc)
    return CollectorEventBundle(
        event_id=5,
        canonical_key="football|world cup|2026-06-18|switzerland|bosnia herzegovina",
        team_a="Switzerland",
        team_b="Bosnia and Herzegovina",
        start_time_utc=datetime(2026, 6, 18, 19, 0, tzinfo=timezone.utc),
        status="scheduled",
        venue="SoFi Stadium",
        result={},
        updated_at_utc=now,
        statistics=[{"subject_type": "team", "subject_name": "Switzerland", "metric": "corners_for_avg", "value_number": 5.2, "value_text": None, "unit": "per_match", "context_json": "{}", "source_id": "s1", "evidence_status": "verified", "sample_size": 10, "observed_at_utc": now.isoformat()}],
        lineups=[{"team_name": "Switzerland", "player_name": "Test Player", "lineup_status": "expected", "position": "FW", "shirt_number": "9", "source_id": "s1", "observed_at_utc": now.isoformat()}],
        availability=[],
        sources=[{"id": "s1", "source_kind": "api", "source_url": "https://provider.test/event/5", "retrieved_at_utc": now.isoformat(), "status": "verified"}],
        missing_critical=[],
        missing_optional=["availability"],
    )


class ImportPersistenceTests(unittest.TestCase):
    def test_source_catalog_and_sentiment_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
            repo.sync_source_catalog(default_source_catalog(), now)
            a = repo.upsert_team("Spain")
            b = repo.upsert_team("Japan")
            match_id = repo.upsert_match("World Cup", "Group", now, a, b, "scheduled")
            snapshot = normalize_sentiment_snapshot(
                match_id=match_id, provider_id="x_api",
                window_start_utc=datetime(2026, 6, 18, 12, tzinfo=timezone.utc),
                window_end_utc=now, positive=2, neutral=3, negative=1,
                query="Spain OR Japan", language="en", estimated_cost_usd=0.03,
            )
            repo.save_sentiment_snapshot(snapshot, now)
            repo.save_sentiment_snapshot(snapshot, now)
            catalog = repo.list_source_catalog()
            snapshots = repo.list_sentiment_snapshots(match_id)
        self.assertGreater(len(catalog), 10)
        self.assertEqual(1, len(snapshots))
        self.assertEqual(6, snapshots[0]["sample_size"])

    def test_weather_observation_is_sourced_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026",
                "Group B",
                datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
                a,
                b,
                "scheduled",
            )
            weather = {
                "observed_for_utc": "2026-06-18T22:00",
                "temperature_c": 24.1,
                "precipitation_mm": 0.0,
                "wind_speed_kmh": 12.0,
                "relative_humidity_pct": 60.0,
                "source_id": "open-meteo-toronto-2026-06-18",
            }
            now = datetime.now(timezone.utc)
            repo.save_weather_observation(match_id, weather, now)
            repo.save_weather_observation(match_id, weather, now)
            observations = [
                row
                for row in repo.list_observations(match_id)
                if row["source_id"] == weather["source_id"]
            ]
        self.assertEqual(4, len(observations))

    def test_bundle_import_is_idempotent_and_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Switzerland")
            b = repo.upsert_team("Bosnia and Herzegovina")
            match_id = repo.upsert_match("FIFA World Cup 2026", "Group B", bundle().start_time_utc, a, b, "scheduled")
            repo.import_collector_bundle(match_id, bundle())
            repo.import_collector_bundle(match_id, bundle())
            observations = repo.list_observations(match_id)
            imports = repo.list_import_runs(match_id)

        self.assertEqual(1, len(observations))
        self.assertEqual("s1", observations[0]["source_id"])
        self.assertEqual(1, len(imports))
        self.assertEqual("complete", imports[0]["status"])

    def test_manual_observation_is_marked_and_upserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match("FIFA World Cup 2026", "Group B", datetime(2026, 6, 18, 22, tzinfo=timezone.utc), a, b, "scheduled")
            rows = [{"subject_type": "team", "subject_name": "Canada", "metric": "corners_for_avg", "value_number": 5.5, "value_text": None, "unit": "per_match", "sample_size": 8}]
            repo.save_manual_observations(match_id, rows, datetime(2026, 6, 18, 15, tzinfo=timezone.utc))
            rows[0]["value_number"] = 5.8
            repo.save_manual_observations(match_id, rows, datetime(2026, 6, 18, 16, tzinfo=timezone.utc))
            saved = repo.list_observations(match_id)
        self.assertEqual(1, len(saved))
        self.assertEqual(5.8, saved[0]["value_number"])
        self.assertEqual("manual", saved[0]["evidence_status"])

    def test_sofascore_preview_can_be_persisted_with_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match("FIFA World Cup 2026", "Group B", datetime(2026, 6, 18, 22, tzinfo=timezone.utc), a, b, "scheduled")
            imported = SofaScoreImport(123, "Canada", "Qatar", "notstarted", [{"period": "ALL", "group": "Match overview", "metric": "Corner kicks", "team_a_value": "6", "team_b_value": "3"}], [{"side": "home", "player_name": "A Player", "player_id": 1, "position": "F", "starter": True, "statistics": {}}], "complete", [], "https://www.sofascore.com/x#id:123")
            repo.import_sofascore_preview(match_id, imported, datetime(2026, 6, 18, 16, tzinfo=timezone.utc))
            observations = repo.list_observations(match_id)
            lineups = repo.list_imported_lineups(match_id)
        self.assertEqual(2, len(observations))
        self.assertTrue(all(row["evidence_status"] == "imported" for row in observations))
        self.assertEqual("A Player", lineups[0]["player_name"])


if __name__ == "__main__":
    unittest.main()
