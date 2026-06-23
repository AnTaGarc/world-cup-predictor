from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.collector_store import CollectorEventBundle
from wcpredict.repository import Repository
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
    def test_source_catalog_persists_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
            repo.sync_source_catalog(default_source_catalog(), now)
            repo.sync_source_catalog(default_source_catalog(), now)
            catalog = repo.list_source_catalog()
        self.assertGreater(len(catalog), 5)

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


if __name__ == "__main__":
    unittest.main()
