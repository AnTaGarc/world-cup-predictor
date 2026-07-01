from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import project_sync


def run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True,
        encoding="utf-8",
    )


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


class ProjectSyncGitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Sync Test")
        self.git("config", "user.email", "sync@example.test")
        (self.root / ".gitignore").write_text(
            "data/cache/\noutput/\n.codex-remote-attachments/\n*.log\n",
            encoding="utf-8",
        )
        for relative in project_sync.DURABLE_PATHS:
            path = self.root / relative
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".sqlite":
                    con = sqlite3.connect(path)
                    con.execute("CREATE TABLE seed(value INTEGER)")
                    con.close()
                else:
                    path.write_text("seed", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / ".keep").write_text("seed", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "seed")

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=check,
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_staging_includes_durable_data_and_excludes_disposable_files(self):
        evidence = self.root / "data/evidence/reviewed-json/new.json"
        evidence.write_text("{}", encoding="utf-8")
        (self.root / "data/worldcup.sqlite").write_bytes(b"updated")
        for relative in ("data/cache/page.html", "output/report.csv", "server.log"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("temporary", encoding="utf-8")

        staged = project_sync.stage_and_validate(self.root)

        self.assertIn("data/worldcup.sqlite", staged)
        self.assertIn("data/evidence/reviewed-json/new.json", staged)
        self.assertNotIn("data/cache/page.html", staged)
        self.assertNotIn("output/report.csv", staged)
        self.assertNotIn("server.log", staged)

    def test_forbidden_tracked_path_blocks_staging(self):
        path = self.root / "output/tracked.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bad", encoding="utf-8")
        self.git("add", "-f", "output/tracked.log")

        with self.assertRaisesRegex(project_sync.SyncError, "prohibid"):
            project_sync.stage_and_validate(self.root)

    def test_durable_roots_must_exist_and_must_not_be_ignored(self):
        (self.root / "data/models/.keep").unlink()
        (self.root / "data/models").rmdir()
        with self.assertRaisesRegex(project_sync.SyncError, "data/models"):
            project_sync.validate_durable_paths(self.root)


class ProjectPullTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.remote = root / "remote.git"
        self.writer = root / "writer"
        self.reader = root / "reader"
        run_git(root, "init", "--bare", str(self.remote))
        run_git(root, "clone", str(self.remote), str(self.writer))
        run_git(self.writer, "switch", "-c", "main")
        self._identity(self.writer)
        (self.writer / ".gitignore").write_text(
            "data/cache/\noutput/\n.codex-remote-attachments/\n*.log\n",
            encoding="utf-8",
        )
        for relative in project_sync.DURABLE_PATHS:
            path = self.writer / relative
            if path.suffix == ".sqlite":
                path.parent.mkdir(parents=True, exist_ok=True)
                con = sqlite3.connect(path)
                con.execute("CREATE TABLE seed(value INTEGER)")
                con.close()
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / ".keep").write_text("seed", encoding="utf-8")
        run_git(self.writer, "add", "-A")
        run_git(self.writer, "commit", "-m", "seed")
        run_git(self.writer, "push", "-u", "origin", "main")
        run_git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        run_git(root, "clone", str(self.remote), str(self.reader))
        self._identity(self.reader)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _identity(path: Path) -> None:
        run_git(path, "config", "user.name", "Sync Test")
        run_git(path, "config", "user.email", "sync@example.test")

    def _advance_remote(self) -> str:
        readme = self.writer / "README.md"
        readme.write_text("remote update", encoding="utf-8")
        run_git(self.writer, "add", "README.md")
        run_git(self.writer, "commit", "-m", "remote update")
        run_git(self.writer, "push")
        return run_git(self.writer, "rev-parse", "HEAD").stdout.strip()

    def test_pull_aborts_when_tracked_data_has_local_changes(self):
        fixture = self.reader / "data/fixtures/.keep"
        fixture.write_text("local stats", encoding="utf-8")

        with self.assertRaisesRegex(project_sync.SyncError, "push_project.ps1"):
            project_sync.pull_project(self.reader)

    def test_pull_aborts_when_new_reviewed_evidence_is_untracked(self):
        evidence = self.reader / "data/evidence/reviewed-json/new.json"
        evidence.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(project_sync.SyncError, "push_project.ps1"):
            project_sync.pull_project(self.reader)

    def test_pull_ignores_cache_and_fast_forwards(self):
        remote_head = self._advance_remote()
        cache = self.reader / "data/cache/page.html"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("cache", encoding="utf-8")

        head = project_sync.pull_project(self.reader)

        self.assertEqual(remote_head, head)
        self.assertTrue(cache.exists())


class ProjectPushTests(ProjectPullTests):
    def test_empty_commit_message_is_rejected(self):
        with self.assertRaisesRegex(project_sync.SyncError, "mensaje"):
            project_sync.push_project(self.reader, "", run_tests=False)

    def test_remote_ahead_is_rejected_before_commit(self):
        self._advance_remote()
        fixture = self.reader / "data/fixtures/.keep"
        fixture.write_text("local update", encoding="utf-8")
        before = run_git(self.reader, "rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(project_sync.SyncError, "pull_project.ps1"):
            project_sync.push_project(
                self.reader, "data: local update", run_tests=False
            )

        self.assertEqual(before, run_git(self.reader, "rev-parse", "HEAD").stdout.strip())

    def test_push_commits_durable_data_and_never_commits_cache(self):
        db = self.reader / "data/worldcup.sqlite"
        con = sqlite3.connect(db)
        con.execute("INSERT INTO seed VALUES(8)")
        con.commit()
        con.close()
        precomputed = self.reader / "data/precomputed/match.json"
        precomputed.write_text('{"probability": 0.6}', encoding="utf-8")
        cache = self.reader / "data/cache/page.html"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("temporary", encoding="utf-8")

        head = project_sync.push_project(
            self.reader, "data: update match statistics", run_tests=False
        )

        tree = project_sync.run_git(
            self.reader, "ls-tree", "-r", "--name-only", head
        ).splitlines()
        self.assertIn("data/worldcup.sqlite", tree)
        self.assertIn("data/precomputed/match.json", tree)
        self.assertNotIn("data/cache/page.html", tree)
        self.assertEqual(
            head,
            run_git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )


class ProjectSyncCliTests(unittest.TestCase):
    def _help(self, *args: str) -> str:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "project_sync.py"), *args, "--help"],
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        return result.stdout

    def test_top_level_help_lists_push_and_pull(self):
        output = self._help()
        self.assertIn("push", output)
        self.assertIn("pull", output)

    def test_push_help_exposes_message_tests_and_dry_run(self):
        output = self._help("push")
        self.assertIn("--message", output)
        self.assertIn("--skip-tests", output)
        self.assertIn("--what-if", output)

    def test_pull_help_exposes_dry_run(self):
        self.assertIn("--what-if", self._help("pull"))

    def test_powershell_launchers_delegate_to_python_core(self):
        push = (SCRIPTS / "push_project.ps1").read_text(encoding="utf-8")
        pull = (SCRIPTS / "pull_project.ps1").read_text(encoding="utf-8")
        self.assertIn('"--message", $Message', push)
        self.assertIn('project_sync.py", "push"', push)
        self.assertIn('project_sync.py", "pull"', pull)


if __name__ == "__main__":
    unittest.main()
