import unittest
from datetime import datetime, timezone

from wcpredict.sentiment import normalize_sentiment_snapshot, x_collection_gate


class SentimentTests(unittest.TestCase):
    def test_x_requires_key_and_positive_budget(self):
        self.assertEqual(x_collection_gate(None, 10).status, "missing_credentials")
        self.assertEqual(x_collection_gate("secret", 0).status, "zero_budget")
        self.assertEqual(x_collection_gate("secret", 5).status, "ready")

    def test_snapshot_is_bounded_and_experimental(self):
        row = normalize_sentiment_snapshot(
            match_id=7,
            provider_id="x_api",
            window_start_utc=datetime(2026, 6, 18, tzinfo=timezone.utc),
            window_end_utc=datetime(2026, 6, 19, tzinfo=timezone.utc),
            positive=25,
            neutral=50,
            negative=25,
            query="Spain OR España",
            language="es",
            estimated_cost_usd=0.5,
        )
        self.assertEqual(row["sample_size"], 100)
        self.assertEqual(row["sentiment_score"], 0.0)
        self.assertFalse(row["eligible_for_model"])


if __name__ == "__main__":
    unittest.main()
