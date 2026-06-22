import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload, ensure_current_world_cup_data
from wcpredict.repository import Repository


PROVIDERS = ("swaptr_wc2026_matches", "swaptr_wc2026_teams", "swaptr_wc2026_players")


class DailyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def test_recent_successful_check_skips_all_fetches(self):
        for provider in PROVIDERS:
            self.repo.record_dataset_refresh_check(provider, self.now - timedelta(hours=2), "ready", None)
        calls = []

        result = ensure_current_world_cup_data(
            self.repo, lambda provider: calls.append(provider), now=self.now
        )

        self.assertEqual([], calls)
        self.assertEqual("current", result.status)
        self.assertEqual(PROVIDERS, result.skipped_recent)

    def test_stale_check_fetches_and_only_imports_changed_hashes(self):
        for provider in PROVIDERS:
            self.repo.record_dataset_refresh_check(provider, self.now - timedelta(hours=30), "ready", None)
        old = DatasetDownload(PROVIDERS[0], "v2", b"same", self.now - timedelta(days=1), 1)
        self.repo.record_dataset_snapshot(
            old.provider_id, old.version, old.sha256, self.now - timedelta(hours=30), old.updated_at, old.row_count, "ready", None
        )
        downloads = {
            provider: DatasetDownload(provider, "v2", b"same" if provider == PROVIDERS[0] else provider.encode(), self.now, 2)
            for provider in PROVIDERS
        }
        imported = []

        result = ensure_current_world_cup_data(
            self.repo,
            lambda provider: downloads[provider],
            importer=lambda download: imported.append(download.provider_id),
            now=self.now,
        )

        self.assertEqual("updated", result.status)
        self.assertEqual(set(PROVIDERS[1:]), set(imported))
        self.assertEqual((PROVIDERS[0],), result.unchanged)

    def test_changed_import_signature_reimports_even_when_remote_bytes_match(self):
        provider = PROVIDERS[0]
        content = b"same"
        old = DatasetDownload(provider, "dataset-1/parser-1", content, self.now, 1)
        self.repo.record_dataset_snapshot(
            provider, old.version, old.sha256, self.now - timedelta(hours=30), old.updated_at, 1, "ready", None
        )
        imported = []
        result = ensure_current_world_cup_data(
            self.repo,
            lambda _provider: DatasetDownload(provider, "dataset-1/parser-2", content, self.now, 1),
            importer=lambda download: imported.append(download.version),
            now=self.now,
            providers=(provider,),
        )
        self.assertEqual(["dataset-1/parser-2"], imported)
        self.assertEqual("updated", result.status)

    def test_failure_keeps_cached_snapshot_stale_and_records_failed_check(self):
        cached_at = self.now - timedelta(days=2)
        provider = PROVIDERS[0]
        self.repo.record_dataset_snapshot(provider, "v1", "abc", cached_at, cached_at, 10, "ready", None)

        def broken(_provider):
            raise RuntimeError("network unavailable")

        result = ensure_current_world_cup_data(self.repo, broken, now=self.now, providers=(provider,))

        self.assertEqual("stale", result.status)
        self.assertEqual((provider,), result.failed)
        self.assertEqual("failed", self.repo.list_dataset_refresh_checks(provider)[0]["status"])
        self.assertEqual(1, len(self.repo.list_dataset_snapshots(provider)))

    def test_recent_failure_uses_one_hour_backoff_instead_of_retrying_every_rerun(self):
        provider = PROVIDERS[0]
        cached_at = self.now - timedelta(days=1)
        self.repo.record_dataset_snapshot(provider, "v1", "abc", cached_at, cached_at, 3, "ready", None)
        self.repo.record_dataset_refresh_check(provider, self.now - timedelta(minutes=20), "failed", "offline")
        calls = []
        result = ensure_current_world_cup_data(
            self.repo, lambda name: calls.append(name), now=self.now, providers=(provider,)
        )
        self.assertEqual([], calls)
        self.assertEqual("stale", result.status)


if __name__ == "__main__":
    unittest.main()
