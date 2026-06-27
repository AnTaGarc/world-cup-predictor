"""End-to-end check that the EFGHIJKL combination produces the official
FIFA pairing the user verified against the regulations.

Tests the solver directly with synthetic third-place inputs, bypassing the
group-stage seeding plumbing (which has its own coverage in test_knockout_bracket).
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wcpredict import knockout_bracket as kb
from wcpredict.knockout_bracket import seed_knockout_bracket
from wcpredict.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
KNOCKOUT_CSV = ROOT / "data" / "fixtures" / "world_cup_2026_knockouts.csv"


class EFGHIJKLPairingTests(unittest.TestCase):
    def _stub_repo(self):
        """Fresh repo with the knockout bracket seeded and 12 dummy teams
        named 'group_X' so the third for group X is unambiguous."""
        tmp = Path(tempfile.mkdtemp())
        repo = Repository(tmp / "app.sqlite")
        repo.initialize()
        seed_knockout_bracket(repo, KNOCKOUT_CSV)
        with sqlite3.connect(repo.path) as con:
            for letter in "ABCDEFGHIJKL":
                con.execute(
                    "INSERT INTO teams(name) VALUES(?) ON CONFLICT(name) DO NOTHING",
                    (f"group_{letter}_third",),
                )
            con.commit()
        return repo

    def test_efghijkl_combination_matches_fifa_official(self):
        repo = self._stub_repo()
        # Build synthetic _third_place_ranking output: thirds from E..L.
        with sqlite3.connect(repo.path) as con:
            team_ids = {
                row[1]: row[0]
                for row in con.execute("SELECT id, name FROM teams")
            }
        ranking = [
            (team_ids[f"group_{letter}_third"], f"group_{letter}_third", letter)
            for letter in "EFGHIJKL"
        ]
        with patch.object(kb, "_third_place_ranking", return_value=ranking), \
             patch.object(kb, "_group_standings", return_value=[(1, "x"), (2, "y"), (3, "z"), (4, "w")]):
            slots = kb.list_bracket_slots(repo)
            with sqlite3.connect(repo.path) as con:
                con.row_factory = sqlite3.Row
                assignment = kb._assign_thirds_annex_c(con, slots)
        # Expected official pairing for EFGHIJKL:
        #   1A → 3E,  1B → 3J,  1D → 3I,  1E → 3F
        #   1G → 3H,  1I → 3G,  1K → 3L,  1L → 3K
        # Slots ↔ winners: M79=1A, M85=1B, M81=1D, M74=1E,
        #                  M82=1G, M77=1I, M87=1K, M80=1L
        expected_third_letter = {
            "M79": "E",  # 1A vs 3E
            "M85": "J",  # 1B vs 3J
            "M81": "I",  # 1D vs 3I
            "M74": "F",  # 1E vs 3F
            "M82": "H",  # 1G vs 3H
            "M77": "G",  # 1I vs 3G
            "M87": "L",  # 1K vs 3L
            "M80": "K",  # 1L vs 3K
        }
        for slot_id, expected_letter in expected_third_letter.items():
            assigned_tid = assignment.get(slot_id)
            self.assertIsNotNone(assigned_tid, f"Slot {slot_id} not assigned")
            expected_tid = team_ids[f"group_{expected_letter}_third"]
            self.assertEqual(
                expected_tid, assigned_tid,
                f"Slot {slot_id}: expected 3{expected_letter}, got tid={assigned_tid}",
            )


if __name__ == "__main__":
    unittest.main()
