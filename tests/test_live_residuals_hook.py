"""Phase 7: settle_match_versioned must persist a live 1X2 residual
into backtest_runs with label='live-wc2026-v1' when a prediction
snapshot exists for the match."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


class LiveResidualHookTests(unittest.TestCase):
    def test_settle_writes_live_residual_when_snapshot_present(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Argentina")
            b = repo.upsert_team("Mexico")
            mid = repo.upsert_match(
                "FIFA World Cup 2026", "Group stage - Group L",
                datetime(2026, 6, 25, 22, tzinfo=timezone.utc),
                a, b, "scheduled",
            )
            # Pre-kickoff snapshot mimicking what the bundle saves.
            payload = {
                "team_a": "Argentina",
                "team_b": "Mexico",
                "primary": [
                    {"market_name": "1X2", "selection_name": "Argentina", "probability": 0.55},
                    {"market_name": "1X2", "selection_name": "Draw", "probability": 0.25},
                    {"market_name": "1X2", "selection_name": "Mexico", "probability": 0.20},
                ],
            }
            repo.save_prediction_snapshot(
                match_id=mid, payload=payload,
                data_as_of_utc=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
                model_version="test-v1",
            )
            repo.settle_match_versioned(
                mid, 2, 1, None, datetime(2026, 6, 26, tzinfo=timezone.utc),
            )
            with repo.session() as con:
                con.row_factory = __import__("sqlite3").Row
                rows = list(con.execute(
                    "SELECT * FROM backtest_runs WHERE run_label='live-wc2026-v1' AND match_id=?",
                    (mid,),
                ))
            self.assertEqual(3, len(rows), "one row per 1X2 selection")
            arg = next(r for r in rows if r["selection"] == "Argentina")
            # Home win observed → outcome_observed = 1, prob was 0.55, brier = 0.2025.
            self.assertEqual(1, arg["outcome_observed"])
            self.assertAlmostEqual(0.55, float(arg["prob_predicted"]), places=4)
            draw = next(r for r in rows if r["selection"] == "Draw")
            self.assertEqual(0, draw["outcome_observed"])

    def test_settle_without_snapshot_is_silent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("A")
            b = repo.upsert_team("B")
            mid = repo.upsert_match(
                "FIFA World Cup 2026", "Group stage - Group A",
                datetime(2026, 6, 12, 18, tzinfo=timezone.utc),
                a, b, "scheduled",
            )
            # No snapshot → settle must not raise nor insert rows.
            repo.settle_match_versioned(mid, 1, 0, None, datetime(2026, 6, 13, tzinfo=timezone.utc))
            with repo.session() as con:
                count = con.execute(
                    "SELECT COUNT(*) FROM backtest_runs WHERE run_label='live-wc2026-v1'"
                ).fetchone()[0]
            self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
