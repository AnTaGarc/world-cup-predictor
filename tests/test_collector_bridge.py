from pathlib import Path
import tempfile
import unittest

from wcpredict.collector_bridge import find_cached_event, load_collector_export


class CollectorBridgeTests(unittest.TestCase):
    def test_finds_cached_event_by_team_names(self):
        export_dir = Path(__file__).resolve().parents[2] / "sports-data" / "exports"
        bundle = load_collector_export(export_dir)
        event = find_cached_event(bundle, "Netherlands", "Japan")
        self.assertIsNotNone(event)
        self.assertEqual("netherlands", event["participant1_name"])
        self.assertEqual("japan", event["participant2_name"])
        self.assertGreater(len(event["market_comparisons"]), 0)

    def test_missing_cache_returns_none(self):
        export_dir = Path(__file__).resolve().parents[2] / "sports-data" / "exports"
        bundle = load_collector_export(export_dir)
        self.assertIsNone(find_cached_event(bundle, "Mexico", "South Africa"))

    def test_missing_export_dir_is_empty_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = load_collector_export(Path(tmp))
        self.assertEqual([], bundle.events)
        self.assertEqual({}, bundle.coverage)


if __name__ == "__main__":
    unittest.main()
