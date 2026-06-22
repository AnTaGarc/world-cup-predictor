from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.repository import Repository


class PredictionPersistenceTests(unittest.TestCase):
    def test_corrected_settlement_deactivates_old_evaluation_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "db.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026",
                "Group",
                datetime(2026, 6, 18, tzinfo=timezone.utc),
                a,
                b,
                "scheduled",
            )
            repo.add_prediction(
                match_id,
                "match_result",
                "1X2",
                "Canada",
                None,
                0.55,
                "medium",
                datetime.now(timezone.utc),
                "test",
            )
            first = repo.settle_match_versioned(
                match_id, 2, 1, None, datetime.now(timezone.utc)
            )
            second = repo.settle_match_versioned(
                match_id, 1, 2, None, datetime.now(timezone.utc)
            )
            evaluations = repo.list_prediction_evaluations(
                match_id, active_only=False
            )
            active_backtests = repo.list_backtests(match_id)
            with repo.session() as con:
                history = con.execute("SELECT goals_a, goals_b FROM historical_matches WHERE source_id='reviewed_settlement'").fetchall()
                model_runs = con.execute("SELECT status, sample_size FROM outcome_model_runs").fetchall()
        self.assertNotEqual(first, second)
        self.assertEqual(2, len(evaluations))
        self.assertEqual(1, sum(row["active"] for row in evaluations))
        self.assertFalse(
            bool(next(row for row in evaluations if row["active"])["hit"])
        )
        self.assertEqual(1, len(active_backtests))
        self.assertFalse(bool(active_backtests[0]["hit"]))
        self.assertEqual([(1, 2)], [tuple(row) for row in history])
        self.assertEqual(2, len(model_runs))
        self.assertTrue(all(row["status"] == "insufficient_data" for row in model_runs))

    def test_predictions_and_odds_roundtrip_for_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "worldcup.sqlite")
            repo.initialize()
            team_a = repo.upsert_team("Spain", "ESP")
            team_b = repo.upsert_team("Japan", "JPN")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026",
                "Group",
                datetime(2026, 6, 18, tzinfo=timezone.utc),
                team_a,
                team_b,
                "scheduled",
            )
            repo.add_prediction(
                match_id=match_id,
                market_family="match_result",
                market_name="1X2",
                selection_name="Spain",
                line=None,
                probability=0.52,
                confidence="medium",
                generated_at_utc=datetime(2026, 6, 18, 10, tzinfo=timezone.utc),
                explanation="test",
            )
            repo.add_manual_odds(
                match_id=match_id,
                market_family="match_result",
                market_name="1X2",
                selection_name="Spain",
                line=None,
                decimal_odds=2.25,
                bookmaker="Winamax",
                captured_at_utc=datetime(2026, 6, 18, 11, tzinfo=timezone.utc),
            )
            self.assertEqual(1, len(repo.list_predictions(match_id)))
            self.assertEqual(1, len(repo.list_manual_odds(match_id)))
            prediction_id = repo.list_predictions(match_id)[0]["id"]
            repo.add_backtest(
                prediction_id=prediction_id,
                result_value=1.0,
                brier_score=0.2304,
                hit=True,
                evaluated_at_utc=datetime(2026, 6, 19, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(1, len(repo.list_backtests(match_id)))

    def test_settlement_is_idempotent_and_evaluates_saved_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "db.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            match_id = repo.upsert_match("FIFA World Cup 2026", "Group B", datetime(2026, 6, 18, 22, tzinfo=timezone.utc), a, b, "scheduled")
            repo.add_prediction(match_id, "match_result", "1X2", "Canada", None, .55, "medium", datetime.now(timezone.utc), "test")
            repo.settle_match(match_id, 2, 1, [], datetime.now(timezone.utc))
            repo.settle_match(match_id, 2, 1, [], datetime.now(timezone.utc))
            self.assertEqual(1, len(repo.list_backtests(match_id)))
            result = repo.get_match_result(match_id)
            form_results = repo.list_match_results_before(datetime(2026, 6, 19, tzinfo=timezone.utc))
        self.assertEqual((2, 1), (result["goals_a"], result["goals_b"]))
        self.assertEqual(1, len(form_results))
        self.assertEqual("Canada", form_results[0].team_a)

if __name__ == "__main__":
    unittest.main()
