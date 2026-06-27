"""Annex C CSV loader: parses an optional FIFA matrix that overrides the
bipartite solver. Falls back silently when the file is empty or malformed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wcpredict import knockout_bracket as kb


class AnnexCLoaderTests(unittest.TestCase):
    def _write_csv(self, body: str) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "annex_c.csv"
        tmp.write_text(body, encoding="utf-8")
        return tmp

    def test_missing_file_returns_empty(self):
        with patch.object(kb, "_ANNEX_C_CSV", Path("/non/existent.csv")):
            self.assertEqual({}, kb._load_annex_c_table())

    def test_header_only_returns_empty(self):
        path = self._write_csv("thirds_combo,1A,1B,1D,1E,1G,1I,1K,1L\n")
        with patch.object(kb, "_ANNEX_C_CSV", path):
            self.assertEqual({}, kb._load_annex_c_table())

    def test_comments_are_ignored(self):
        body = (
            "# this is a comment\n"
            "thirds_combo,1A,1B,1D,1E,1G,1I,1K,1L\n"
            "# another comment\n"
            "ABCDEFGH,H,A,E,D,F,B,C,G\n"
        )
        path = self._write_csv(body)
        with patch.object(kb, "_ANNEX_C_CSV", path):
            table = kb._load_annex_c_table()
        self.assertEqual(1, len(table))
        key = frozenset("ABCDEFGH")
        self.assertEqual("H", table[key]["1A"])
        self.assertEqual("G", table[key]["1L"])

    def test_row_with_invalid_combo_skipped(self):
        # 7 letters (not 8) → invalid; valid row stays.
        body = (
            "thirds_combo,1A,1B,1D,1E,1G,1I,1K,1L\n"
            "ABCDEFG,H,A,E,D,F,B,C,G\n"        # invalid
            "ABCDEFGH,H,A,E,D,F,B,C,G\n"        # valid
        )
        path = self._write_csv(body)
        with patch.object(kb, "_ANNEX_C_CSV", path):
            table = kb._load_annex_c_table()
        self.assertEqual(1, len(table))
        self.assertIn(frozenset("ABCDEFGH"), table)

    def test_row_with_unused_group_skipped(self):
        # Combo says ABCDEFGH; row assigns Z (not in combo) → invalid.
        body = (
            "thirds_combo,1A,1B,1D,1E,1G,1I,1K,1L\n"
            "ABCDEFGH,Z,A,E,D,F,B,C,G\n"
        )
        path = self._write_csv(body)
        with patch.object(kb, "_ANNEX_C_CSV", path):
            self.assertEqual({}, kb._load_annex_c_table())

    def test_row_with_duplicated_third_assignment_skipped(self):
        # Each group must appear exactly once across the 8 winner cells.
        body = (
            "thirds_combo,1A,1B,1D,1E,1G,1I,1K,1L\n"
            "ABCDEFGH,H,H,E,D,F,B,C,G\n"        # H repeated, A missing
        )
        path = self._write_csv(body)
        with patch.object(kb, "_ANNEX_C_CSV", path):
            self.assertEqual({}, kb._load_annex_c_table())


if __name__ == "__main__":
    unittest.main()
