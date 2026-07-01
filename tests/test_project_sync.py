from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import project_sync


class ProjectSyncPolicyTests(unittest.TestCase):
    def test_durable_data_is_allowed_and_disposable_data_is_forbidden(self):
        for path in (
            "data/worldcup.sqlite",
            "data/models/model.joblib",
            "data/fixtures/stats.csv",
            "data/evidence/reviewed-json/a.json",
            "data/precomputed/penalties/a.json",
        ):
            self.assertFalse(project_sync.is_forbidden_path(path), path)
        for path in (
            "data/cache/a.html",
            "output/a.csv",
            ".codex-remote-attachments/a.png",
            "server.log",
            "data/worldcup.sqlite-wal",
            "data/worldcup.sqlite-shm",
        ):
            self.assertTrue(project_sync.is_forbidden_path(path), path)

    def test_checkpoint_moves_wal_data_into_valid_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "worldcup.sqlite"
            con = sqlite3.connect(db)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE stats(value INTEGER)")
            con.execute("INSERT INTO stats VALUES(7)")
            con.commit()
            con.close()

            project_sync.checkpoint_and_validate_sqlite(db)

            con = sqlite3.connect(db)
            self.assertEqual(
                "ok", con.execute("PRAGMA integrity_check").fetchone()[0]
            )
            self.assertEqual(7, con.execute("SELECT value FROM stats").fetchone()[0])
            con.close()

    def test_missing_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(project_sync.SyncError, "no existe"):
                project_sync.checkpoint_and_validate_sqlite(
                    Path(directory) / "worldcup.sqlite"
                )


if __name__ == "__main__":
    unittest.main()
