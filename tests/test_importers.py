from pathlib import Path
import unittest

from wcpredict.importers import import_provider_export, parse_sofascore_html


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ImporterTests(unittest.TestCase):
    def test_import_provider_export(self):
        imported = import_provider_export(FIXTURES / "provider_export.json")
        self.assertEqual(1, len(imported.matches))
        self.assertEqual("Spain", imported.matches[0]["team_a"])
        self.assertEqual(1, len(imported.team_stats))

    def test_parse_sofascore_minimal_html(self):
        parsed = parse_sofascore_html((FIXTURES / "sofascore_minimal.html").read_text(encoding="utf-8"))
        self.assertEqual("Spain", parsed["team_a"])
        self.assertEqual("Japan", parsed["team_b"])

    def test_parse_sofascore_empty_html_fails_cleanly(self):
        with self.assertRaises(ValueError):
            parse_sofascore_html((FIXTURES / "sofascore_empty.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
