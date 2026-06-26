"""Phase 0 regression tests.

Two invariants this module enforces:

  * A player listed in BOTH ``current_wc_player_stats`` and
    ``player_match_stats`` must never have their cumulative metrics summed
    (was the Diego Gómez bug: daily=1 yellow + manual=1 yellow → 2 yellows).
  * ``save_prediction_snapshot`` persists immutable pre-kickoff payloads
    idempotently and the snapshot framework can be queried back.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


def _make_match(repo: Repository) -> int:
    a = repo.upsert_team("Argentina")
    b = repo.upsert_team("Mexico")
    return repo.upsert_match(
        "FIFA World Cup 2026", "Group stage - Group L",
        datetime(2026, 6, 25, 22, tzinfo=timezone.utc),
        a, b, "scheduled",
    )


class PlayerPerformanceDeduplicationTests(unittest.TestCase):
    def test_daily_takes_precedence_over_player_match_stats(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            match_id = _make_match(repo)
            # Daily bank: cumulative WC totals (truth).
            with repo.session() as con:
                con.execute(
                    "INSERT INTO current_wc_player_stats(provider_id, player_name, team_name, "
                    "position, minutes, goals, assists, shots, shots_on_target, passes, "
                    "yellow_cards, red_cards, tackles_won, interceptions, save_percentage, imported_at_utc) "
                    "VALUES('swaptr', 'Diego Gomez', 'Argentina', 'MF', 180, 1, 0, 5, 2, 60, 1, 0, 4, 3, NULL, ?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                # Same player ALSO in player_match_stats (manual import).
                argentina_id = con.execute("SELECT id FROM teams WHERE name='Argentina'").fetchone()[0]
                con.execute(
                    "INSERT INTO players(name, team_id, position) VALUES('Diego Gomez', ?, 'MF') "
                    "ON CONFLICT(name, team_id) DO NOTHING",
                    (argentina_id,),
                )
                player_id = con.execute(
                    "SELECT id FROM players WHERE name='Diego Gomez' AND team_id=?",
                    (argentina_id,),
                ).fetchone()[0]
                con.execute(
                    "INSERT INTO player_match_stats(match_id, player_id, minutes, goals, yellow_cards, source_id) "
                    "VALUES(?, ?, 90, 1, 1, 'manual')",
                    (match_id, player_id),
                )
                con.commit()
            rows = repo.list_player_performance_rows()
            gomez = [r for r in rows if str(r.get("player_name") or "") == "Diego Gomez"]
            # Exactly one row must survive: the daily one.
            self.assertEqual(1, len(gomez))
            # Daily values (not summed) must be the ones returned.
            self.assertEqual(1, int(gomez[0].get("goals") or 0))
            self.assertEqual(1, int(gomez[0].get("yellow_cards") or 0))
            self.assertEqual(180, int(gomez[0].get("minutes") or 0))

    def test_player_only_in_player_match_stats_still_appears(self):
        """Historical/non-WC2026 players must keep being returned even though
        they are NOT in the daily WC bank."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            match_id = _make_match(repo)
            with repo.session() as con:
                argentina_id = con.execute("SELECT id FROM teams WHERE name='Argentina'").fetchone()[0]
                con.execute("INSERT INTO players(name, team_id, position) VALUES('Legacy Striker', ?, 'FW')",
                            (argentina_id,))
                player_id = con.execute(
                    "SELECT id FROM players WHERE name='Legacy Striker' AND team_id=?",
                    (argentina_id,),
                ).fetchone()[0]
                con.execute(
                    "INSERT INTO player_match_stats(match_id, player_id, minutes, goals, source_id) "
                    "VALUES(?, ?, 90, 2, 'historical')",
                    (match_id, player_id),
                )
                con.commit()
            rows = repo.list_player_performance_rows()
            legacy = [r for r in rows if str(r.get("player_name") or "") == "Legacy Striker"]
            self.assertEqual(1, len(legacy))
            self.assertEqual(2, int(legacy[0].get("goals") or 0))


class PredictionSnapshotTests(unittest.TestCase):
    def test_save_and_list_snapshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            match_id = _make_match(repo)
            now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
            payload = {
                "team_a": "Argentina",
                "team_b": "Mexico",
                "primary": [{"market_name": "1X2", "selection_name": "Argentina", "probability": 0.62}],
            }
            snapshot_id = repo.save_prediction_snapshot(
                match_id=match_id, payload=payload,
                data_as_of_utc=now, model_version="test-v1",
                generated_at_utc=now,
            )
            self.assertIsNotNone(snapshot_id)
            snapshots = repo.list_prediction_snapshots(match_id, model_version="test-v1")
            self.assertEqual(1, len(snapshots))
            self.assertEqual("test-v1", snapshots[0]["model_version"])
            roundtrip = json.loads(snapshots[0]["payload_json"])
            self.assertEqual("Argentina", roundtrip["team_a"])

    def test_snapshot_is_idempotent_on_same_inputs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            match_id = _make_match(repo)
            now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
            payload = {"team_a": "Argentina", "team_b": "Mexico"}
            repo.save_prediction_snapshot(
                match_id=match_id, payload=payload, data_as_of_utc=now,
                model_version="v1", generated_at_utc=now,
            )
            # Same (match, version, data_as_of) → must not duplicate.
            second = repo.save_prediction_snapshot(
                match_id=match_id, payload=payload, data_as_of_utc=now,
                model_version="v1", generated_at_utc=now,
            )
            self.assertIsNone(second, "Second insert should be a no-op")
            snapshots = repo.list_prediction_snapshots(match_id, model_version="v1")
            self.assertEqual(1, len(snapshots))

    def test_latest_snapshot_returns_most_recent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            match_id = _make_match(repo)
            earlier = datetime(2026, 6, 22, 12, tzinfo=timezone.utc)
            later = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
            repo.save_prediction_snapshot(
                match_id=match_id, payload={"v": 1},
                data_as_of_utc=earlier, model_version="v1", generated_at_utc=earlier,
            )
            repo.save_prediction_snapshot(
                match_id=match_id, payload={"v": 2},
                data_as_of_utc=later, model_version="v1", generated_at_utc=later,
            )
            latest = repo.latest_prediction_snapshot(match_id, model_version="v1")
            self.assertIsNotNone(latest)
            self.assertEqual({"v": 2}, json.loads(latest["payload_json"]))


if __name__ == "__main__":
    unittest.main()
