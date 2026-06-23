import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.knockout_bracket import (
    COMPETITION,
    bracket_view,
    list_bracket_slots,
    resolve_knockout_bracket,
    seed_knockout_bracket,
)
from wcpredict.knockout_model import predict_knockout_match
from wcpredict.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
KNOCKOUT_CSV = ROOT / "data" / "fixtures" / "world_cup_2026_knockouts.csv"


def _seed_group(repo: Repository, group: str, teams: list[str]) -> dict[str, int]:
    """Create 4 teams + 6 matches for a group, marking them all finished so
    the standings can be derived deterministically."""
    ids = {name: repo.upsert_team(name) for name in teams}
    schedule = [
        (teams[0], teams[1], 2, 0),
        (teams[2], teams[3], 1, 1),
        (teams[0], teams[2], 1, 1),
        (teams[3], teams[1], 0, 2),
        (teams[2], teams[1], 0, 1),
        (teams[0], teams[3], 3, 0),
    ]
    with sqlite3.connect(repo.path) as con:
        for idx, (a, b, ga, gb) in enumerate(schedule):
            kickoff = f"2026-06-1{idx + 1}T18:00:00+00:00"
            con.execute(
                "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                "VALUES(?, ?, ?, ?, ?, 'finished', NULL, 1)",
                (COMPETITION, f"Group stage - Group {group}", kickoff, ids[a], ids[b]),
            )
            mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                "VALUES(?, ?, ?, 'manual', ?)",
                (mid, ga, gb, datetime.now(timezone.utc).isoformat()),
            )
        con.commit()
    return ids


class KnockoutBracketTests(unittest.TestCase):
    def test_seed_inserts_all_thirty_two_slots(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            n = seed_knockout_bracket(repo, KNOCKOUT_CSV)
        self.assertEqual(32, n)

    def test_resolution_does_nothing_before_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            summary = resolve_knockout_bracket(repo)
        self.assertEqual(0, summary["resolved"])
        self.assertEqual(0, summary["matches_created"])

    def test_resolution_fills_r32_slot_when_two_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            # Slot R32-1 wants 1A vs 2B. Seed both groups.
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            _seed_group(repo, "B", ["Canada", "Bosnia and Herzegovina", "Germany", "Japan"])
            summary = resolve_knockout_bracket(repo)
            # At least the R32-1 slot should be resolved (1A=Mexico, 2B=Bosnia).
            self.assertGreaterEqual(summary["resolved"], 1)
            self.assertGreaterEqual(summary["matches_created"], 1)
            view = bracket_view(repo)
            r32_1 = next(slot for slot in view if slot["slot_id"] == "R32-1")
            self.assertFalse(r32_1["home_pending"])
            self.assertFalse(r32_1["away_pending"])

    def test_resolution_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            _seed_group(repo, "B", ["Canada", "Bosnia and Herzegovina", "Germany", "Japan"])
            resolve_knockout_bracket(repo)
            again = resolve_knockout_bracket(repo)
        self.assertEqual(0, again["matches_created"])


class KnockoutModelTests(unittest.TestCase):
    def test_advance_probabilities_sum_to_one(self):
        pred = predict_knockout_match(1.55, 1.30, dispersion=0.08, rho=-0.16)
        self.assertAlmostEqual(pred.home_advances + pred.away_advances, 1.0, places=5)

    def test_better_team_advances_more_often(self):
        pred = predict_knockout_match(2.0, 0.8, dispersion=0.08, rho=-0.16)
        self.assertGreater(pred.home_advances, 0.65)

    def test_even_match_close_to_fifty(self):
        pred = predict_knockout_match(1.4, 1.4, dispersion=0.08, rho=-0.16)
        self.assertAlmostEqual(pred.home_advances, 0.5, delta=0.02)

    def test_methods_sum_to_total_advance(self):
        pred = predict_knockout_match(1.8, 1.2, dispersion=0.08, rho=-0.16)
        home_total = pred.home_wins_90 + pred.home_wins_et + pred.home_wins_penalties
        self.assertAlmostEqual(home_total, pred.home_advances, places=5)


if __name__ == "__main__":
    unittest.main()
