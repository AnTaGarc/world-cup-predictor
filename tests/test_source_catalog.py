import unittest
from datetime import datetime, timedelta, timezone

from wcpredict.source_catalog import default_source_catalog, route_observations


class SourceCatalogTests(unittest.TestCase):
    def test_catalog_contains_ranked_costed_sources(self):
        catalog = default_source_catalog()
        by_id = {row.provider_id: row for row in catalog}
        self.assertEqual(by_id["reviewed_capture"].bank, 0)
        self.assertEqual(by_id["martj42"].cost_tier, "free")
        # The catalog only declares free/community sources; no paid APIs
        # should remain after the cleanup of dead provider entries.
        for row in catalog:
            self.assertNotEqual(row.cost_tier, "pay_per_use")
            self.assertFalse(row.requires_credentials)

    def test_current_world_cup_datasets_are_primary_operational_with_community_provenance(self):
        by_id = {row.provider_id: row for row in default_source_catalog()}
        for provider_id in (
            "swaptr_wc2026_matches",
            "swaptr_wc2026_teams",
            "swaptr_wc2026_players",
        ):
            source = by_id[provider_id]
            self.assertEqual(1, source.bank)
            self.assertIn("world_cup_2026", source.domains)
            self.assertIn("comunitaria", source.notes.casefold())

    def test_router_prefers_fresh_higher_bank_and_reports_conflict(self):
        now = datetime(2026, 6, 19, tzinfo=timezone.utc)
        rows = [
            {"provider_id": "martj42", "value": 2, "observed_at_utc": now.isoformat()},
            {"provider_id": "kaggle_mirror", "value": 1, "observed_at_utc": now.isoformat()},
        ]
        result = route_observations("historical_results", rows, now=now)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.selected["provider_id"], "martj42")

        conflict = route_observations(
            "postmatch_stats",
            [
                {"provider_id": "reviewed_capture", "value": 9, "observed_at_utc": now.isoformat()},
                {"provider_id": "official_competition", "value": 8, "observed_at_utc": now.isoformat()},
            ],
            now=now,
        )
        self.assertEqual(conflict.status, "conflicting")
        self.assertIsNone(conflict.selected)

    def test_router_skips_stale_sources(self):
        now = datetime(2026, 6, 19, tzinfo=timezone.utc)
        # kaggle_mirror is free but classified domain "world_cup_2026"; a
        # match domain check (e.g. "sentiment") for a non-listed provider
        # should fall through as not_found.
        rows = [
            {"provider_id": "kaggle_mirror", "value": 1, "observed_at_utc": (now - timedelta(days=365)).isoformat()},
        ]
        result = route_observations("historical_results", rows, now=now)
        self.assertEqual(result.status, "not_found")
        self.assertIn("kaggle_mirror:stale", result.skipped)


if __name__ == "__main__":
    unittest.main()
