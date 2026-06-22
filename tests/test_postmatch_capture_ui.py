import unittest

from wcpredict.ui.postmatch_capture import can_finalize, review_sections


class PostmatchCaptureViewTests(unittest.TestCase):
    def test_candidates_are_grouped_and_pending_blocks_save(self):
        rows = [
            {
                "id": 1,
                "subject_type": "event",
                "metric": "final_score",
                "review_status": "confirmed",
            },
            {
                "id": 2,
                "subject_type": "team",
                "metric": "shots",
                "review_status": "pending_review",
            },
            {
                "id": 3,
                "subject_type": "player",
                "metric": "minutes",
                "review_status": "corrected",
            },
        ]
        sections = review_sections(rows)
        self.assertEqual([1], [row["id"] for row in sections["Resultado"]])
        self.assertEqual([2], [row["id"] for row in sections["Equipos"]])
        self.assertFalse(can_finalize(rows))


if __name__ == "__main__":
    unittest.main()
