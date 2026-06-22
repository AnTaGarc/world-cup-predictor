from pathlib import Path
import tempfile
import unittest

from wcpredict.historical_import import (
    merge_international_sources,
    read_results_csv,
)


FIXTURES = Path(__file__).parent / "fixtures"


class HistoricalImportTests(unittest.TestCase):
    def test_unplayed_rows_with_na_scores_are_excluded(self):
        content = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n2026-06-19,A,B,NA,NA,World Cup,X,Y,TRUE\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            path.write_text(content, encoding="utf-8")
            rows = read_results_csv(path, "martj42")
        self.assertEqual([], rows)

    def test_alias_overlap_is_deduplicated_and_sources_are_retained(self):
        primary = read_results_csv(
            FIXTURES / "international_results.csv", "martj42"
        )
        validation = read_results_csv(
            FIXTURES / "openfootball_results.csv", "openfootball"
        )
        merged = merge_international_sources(primary, validation)
        self.assertEqual(3, len(merged))
        czech = next(row for row in merged if row.team_a == "Czechia")
        self.assertEqual({"martj42", "openfootball"}, set(czech.source_ids))
        self.assertFalse(czech.neutral_site)


if __name__ == "__main__":
    unittest.main()
