import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from wcpredict.review import CandidateDecision, ensure_batch_finalizable, normalized_review_value
from wcpredict.repository import Repository


class ReviewTests(unittest.TestCase):
    def test_repository_requires_every_decision_before_finalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            now = datetime.now(timezone.utc)
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group", now, a, b, "finished"
            )
            batch_id = repo.create_screenshot_batch(match_id, None, now)
            asset_id = repo.add_screenshot_asset(
                batch_id, "stats.png", "image/png", 10, "abc", "raw/abc.png", now
            )
            candidate_id = repo.add_extraction_candidates(
                batch_id,
                [{
                    "asset_id": asset_id,
                    "subject_type": "team",
                    "subject_name": "Canada",
                    "metric": "shots",
                    "value_number": 14,
                    "value_text": None,
                    "unit": "match",
                    "period": "ALL",
                    "raw_label": "Total shots",
                    "raw_value": "14",
                    "confidence": .99,
                    "review_status": "pending_review",
                }],
            )[0]
            with self.assertRaises(ValueError):
                repo.finalize_screenshot_batch(batch_id, now)
            repo.review_candidate(candidate_id, CandidateDecision("confirm"), now)
            repo.finalize_screenshot_batch(batch_id, now)
            observations = repo.list_observations(match_id)
        self.assertEqual(1, len(observations))
        self.assertEqual("verified_user_capture", observations[0]["evidence_status"])

    def test_pending_candidate_blocks_finalization(self):
        with self.assertRaises(ValueError):
            ensure_batch_finalizable([
                {"review_status": "confirmed"},
                {"review_status": "pending_review"},
            ])

    def test_correction_preserves_raw_and_uses_corrected_value(self):
        decision = CandidateDecision("correct", corrected_value_number=7.0)
        result = normalized_review_value(
            {"metric": "corners", "value_number": 1.0, "raw_value": "1"},
            decision,
        )
        self.assertEqual(7.0, result["value_number"])
        self.assertEqual("1", result["raw_value"])
        self.assertEqual("verified_user_capture", result["evidence_status"])


if __name__ == "__main__":
    unittest.main()
