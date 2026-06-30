from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.penalty_context_cache import (
    group_stage_complete,
    load_precomputed_context,
    save_precomputed_context,
    repository_penalty_input_fingerprint,
)
from wcpredict.penalty_history_model import (
    PenaltyCoverage,
    PenaltyPlayerContribution,
    build_penalty_match_context,
)
from wcpredict.repository import Repository


class PenaltyContextCacheTests(unittest.TestCase):
    def test_goalkeeper_evidence_changes_repository_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            morocco = repo.upsert_team("Morocco")
            spain = repo.upsert_team("Spain")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Round of 16",
                datetime(2026, 7, 4, tzinfo=timezone.utc),
                morocco, spain, "scheduled",
            )
            repo.replace_current_world_cup_players(
                "test",
                [
                    {"player_name": "Bounou", "team_name": "Morocco", "position": "GK"},
                    {"player_name": "Spain Keeper", "team_name": "Spain", "position": "GK"},
                ],
                datetime(2026, 6, 30, tzinfo=timezone.utc),
            )
            match = repo.get_match(match_id)
            before = repository_penalty_input_fingerprint(repo, match)
            repo.save_goalkeeper_penalty_attempts([{
                "goalkeeper_name": "Bounou", "attempted_on": "2026-01-01",
                "phase": "shootout", "outcome": "saved", "taker_name": "Taker",
                "source_provider": "test", "source_url": "https://example.test",
                "source_row_key": "bounou-1",
                "fetched_at_utc": "2026-06-30T10:00:00+00:00",
            }])
            after = repository_penalty_input_fingerprint(repo, match)

        self.assertNotEqual(before, after)

    def test_context_round_trips_through_versioned_atomic_json(self):
        context = build_penalty_match_context("Spain", "Japan", [])
        context = replace(
            context,
            player_rows=(PenaltyPlayerContribution(
                "Taker", "Spain", "ST", 0.8, 0.6, 0.62, 0.79, 5, "medium"
            ),),
            coverage=PenaltyCoverage(46, 12, 70, 23, 23, 7, 5),
            simulations=25_000,
            standard_error=0.003,
        )
        with tempfile.TemporaryDirectory() as directory:
            target = save_precomputed_context(
                Path(directory), 73, "Spain", "Japan", context,
                input_fingerprint="abc123", model_version="model-v1",
            )
            loaded = load_precomputed_context(
                Path(directory), "Spain", "Japan", model_version="model-v1"
            )
            leftovers = list(Path(directory).glob("*.tmp"))
        self.assertEqual(context, loaded)
        self.assertEqual("spain--japan.json", target.name)
        self.assertEqual([], leftovers)

    def test_team_or_model_mismatch_does_not_load_stale_artifact(self):
        context = build_penalty_match_context("Spain", "Japan", [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_precomputed_context(
                root, 73, "Spain", "Japan", context,
                input_fingerprint="abc123", model_version="model-v1",
            )
            self.assertIsNone(load_precomputed_context(root, "Spain", "Japan", model_version="model-v2"))
            self.assertIsNone(load_precomputed_context(root, "Japan", "Spain", model_version="model-v1"))
            self.assertIsNone(load_precomputed_context(
                root, "Spain", "Japan", model_version="model-v1",
                expected_input_fingerprint="changed-inputs",
            ))

    def test_group_stage_gate_requires_three_completed_team_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            team = repo.upsert_team("Spain")
            opponents = [repo.upsert_team(name) for name in ("Japan", "Ghana", "Mexico")]
            kickoff = datetime(2026, 6, 10, tzinfo=timezone.utc)
            for index, opponent in enumerate(opponents):
                match_id = repo.upsert_match(
                    "FIFA World Cup 2026", "Group stage - Group H",
                    kickoff.replace(day=10 + index), team, opponent, "finished",
                )
                if index < 2:
                    with repo.session() as con:
                        con.execute(
                            "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                            "VALUES(?, 1, 0, 'test', ?)",
                            (match_id, kickoff.isoformat()),
                        )
            self.assertFalse(group_stage_complete(repo, "Spain"))
            with repo.session() as con:
                con.execute(
                    "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                    "VALUES(?, 1, 0, 'test', ?)",
                    (match_id, kickoff.isoformat()),
                )
            self.assertTrue(group_stage_complete(repo, "Spain"))


if __name__ == "__main__":
    unittest.main()
