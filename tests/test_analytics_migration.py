import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from wcpredict.analytics_migration import (
    capture_sports_invariants,
    migrate_snapshot_payload,
    migrate_worldcup_database,
)


class AnalyticsMigrationTests(unittest.TestCase):
    def test_migration_removes_only_odds_and_preserves_sports_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "worldcup.sqlite"
            backup = Path(tmp) / "before.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE teams(id INTEGER PRIMARY KEY, name TEXT);
                    INSERT INTO teams VALUES(1, 'Spain');
                    CREATE TABLE observations(id INTEGER PRIMARY KEY, metric TEXT, value_number REAL);
                    INSERT INTO observations VALUES(1, 'tiros.tiros_a_puerta', 7);
                    CREATE TABLE manual_odds(id INTEGER PRIMARY KEY, bookmaker TEXT);
                    INSERT INTO manual_odds VALUES(1, 'Winamax');
                    CREATE TABLE prediction_snapshots(id INTEGER PRIMARY KEY, payload_json TEXT);
                    """
                )
                payload = {
                    "expected_xg": [1.2, 0.8],
                    "deep_count": 7,
                    "predictions": [
                        {"market_name": "1X2", "selection_name": "Spain", "probability": 0.6},
                        {"market_name": "Over/Under 2.5", "selection_name": "Over 2.5", "probability": 0.5},
                    ],
                }
                connection.execute(
                    "INSERT INTO prediction_snapshots VALUES(1, ?)",
                    (json.dumps(payload),),
                )
                before = capture_sports_invariants(connection)
                connection.commit()
            finally:
                connection.close()

            report = migrate_worldcup_database(database, backup)

            connection = sqlite3.connect(database)
            try:
                after = capture_sports_invariants(connection)
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_odds'"
                ).fetchone()
                migrated = json.loads(
                    connection.execute("SELECT payload_json FROM prediction_snapshots").fetchone()[0]
                )
            finally:
                connection.close()
            self.assertEqual(before, after)
            self.assertIsNone(table)
            self.assertEqual(1, report.odds_rows_removed)
            self.assertEqual([1.2, 0.8], migrated["expected_xg"])
            self.assertEqual(7, migrated["deep_count"])
            self.assertEqual("Resultado probable", migrated["predictions"][0]["label"])
            self.assertEqual(1, len(migrated["predictions"]))
            self.assertTrue(backup.exists())

            second = migrate_worldcup_database(database, Path(tmp) / "second.sqlite")
            self.assertEqual(0, second.odds_rows_removed)

    def test_snapshot_payload_migration_is_idempotent(self):
        payload = {"expected_xg": [1.0, 0.9], "predictions": []}
        once = migrate_snapshot_payload(payload)
        self.assertEqual(once, migrate_snapshot_payload(once))


if __name__ == "__main__":
    unittest.main()
