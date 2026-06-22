from pathlib import Path
from datetime import datetime, timezone
import tempfile
import unittest

from wcpredict.screenshot_evidence import (
    ScreenshotUpload,
    classify_sofascore_player_table,
    classify_player_tables_from_ocr_rows,
    classify_sofascore_tokens,
    store_upload,
)
from wcpredict.repository import Repository


class ScreenshotEvidenceTests(unittest.TestCase):
    def test_repository_deduplicates_asset_and_requires_pending_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            team_a = repo.upsert_team("Canada")
            team_b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026",
                "Group",
                datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
                team_a,
                team_b,
                "finished",
            )
            now = datetime.now(timezone.utc)
            batch_id = repo.create_screenshot_batch(match_id, None, now)
            first = repo.add_screenshot_asset(
                batch_id, "stats.png", "image/png", 10, "abc", "raw/abc.png", now
            )
            second = repo.add_screenshot_asset(
                batch_id, "copy.png", "image/png", 10, "abc", "raw/abc.png", now
            )
            ids = repo.add_extraction_candidates(
                batch_id,
                [
                    {
                        "asset_id": first,
                        "subject_type": "team",
                        "subject_name": "Canada",
                        "metric": "shots",
                        "value_number": 14,
                        "value_text": None,
                        "unit": "match",
                        "period": "ALL",
                        "raw_label": "Total shots",
                        "raw_value": "14",
                        "confidence": 0.99,
                        "review_status": "pending_review",
                    }
                ],
            )
        self.assertEqual(first, second)
        self.assertEqual(1, len(ids))

    def test_duplicate_bytes_have_same_hash_and_safe_path(self):
        upload = ScreenshotUpload("stats.png", "image/png", b"same-image")
        with tempfile.TemporaryDirectory() as tmp:
            first = store_upload(upload, Path(tmp))
            second = store_upload(upload, Path(tmp))
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.stored_path.name, second.stored_path.name)

    def test_tokens_create_pending_team_candidates(self):
        candidates = classify_sofascore_tokens(
            [("Ball possession", 0.99), ("54%", 0.98), ("46%", 0.98)],
            "Canada",
            "Qatar",
            asset_id=7,
        )
        self.assertEqual(2, len(candidates))
        self.assertTrue(
            all(row.review_status == "pending_review" for row in candidates)
        )
        self.assertEqual(
            {"Canada", "Qatar"}, {row.subject_name for row in candidates}
        )

    def test_player_table_creates_reviewable_candidates_with_warnings(self):
        candidates = classify_sofascore_player_table(
            ["Player", "Min", "Rating", "Goals", "Assists", "Shots on target", "Accurate passes"],
            [[("Example Forward", 0.99), ("87", 0.98), ("7.8", 0.97), ("1", 0.99), ("0", 0.99), ("3", 0.96), ("18/22 (82%)", 0.74)]],
            "Canada",
            asset_id=9,
        )
        by_metric = {row.metric: row for row in candidates}
        self.assertEqual(87, by_metric["minutes"].value_number)
        self.assertEqual(7.8, by_metric["rating"].value_number)
        self.assertEqual(82, by_metric["pass_accuracy"].value_number)
        self.assertTrue(by_metric["pass_accuracy"].warnings)
        self.assertTrue(all(row.subject_type == "player" for row in candidates))
        self.assertTrue(all(row.review_status == "pending_review" for row in candidates))

    def test_ocr_rows_detect_team_and_player_table_without_silent_guessing(self):
        rows = [
            [("Canada", 0.99)],
            [("Player", 0.99), ("Min", 0.98), ("Rating", 0.98), ("Goals", 0.99)],
            [("Example Forward", 0.97), ("87", 0.98), ("7.8", 0.97), ("1", 0.99)],
        ]
        candidates = classify_player_tables_from_ocr_rows(rows, "Canada", "Qatar", asset_id=11)
        self.assertEqual({"minutes", "rating", "goals"}, {row.metric for row in candidates})
        self.assertTrue(all("team:Canada" in row.warnings for row in candidates))


if __name__ == "__main__":
    unittest.main()
