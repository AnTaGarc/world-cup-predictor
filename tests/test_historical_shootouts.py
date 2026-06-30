from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.historical_shootouts import import_historical_shootout_csv
from wcpredict.repository import Repository


class HistoricalShootoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.repo = Repository(root / "app.sqlite")
        self.repo.initialize()
        self.coverage = root / "coverage.csv"
        self.kicks = root / "kicks.csv"
        self.coverage.write_text(
            "team_name,competition,competition_edition,competition_end_on,senior,official,source_provider,source_url,retrieved_at_utc\n"
            "Morocco,Africa Cup of Nations,2025,2026-01-18,true,true,caf,https://example.test/afcon-2025,2026-06-30T10:00:00+00:00\n"
            "Morocco,World Cup,2022,2022-12-18,true,true,fifa,https://example.test/wc-2022,2026-06-30T10:00:00+00:00\n"
            "Morocco,Africa Cup of Nations,2023,2024-02-11,true,true,caf,https://example.test/afcon-2023,2026-06-30T10:00:00+00:00\n"
            "Morocco,Africa Cup of Nations,2021,2022-02-06,true,true,caf,https://example.test/afcon-2021,2026-06-30T10:00:00+00:00\n"
            "Netherlands,EURO,2024,2024-07-14,true,true,uefa,https://example.test/euro-2024,2026-06-30T10:00:00+00:00\n",
            encoding="utf-8",
        )
        self.kicks.write_text(
            "played_on,competition,competition_edition,round_name,team_a,team_b,winner_team,sequence_number,team_name,player_name,goalkeeper_name,outcome,source_provider,source_url,source_row_key,retrieved_at_utc\n"
            "2022-12-06,World Cup,2022,Round of 16,Morocco,Spain,Morocco,1,Morocco,Sabiri,Unai Simon,scored,fifa,https://example.test/wc-2022,morocco-spain-1,2026-06-30T10:00:00+00:00\n"
            "2022-12-06,World Cup,2022,Round of 16,Morocco,Spain,Morocco,2,Spain,Soler,Yassine Bounou,saved,fifa,https://example.test/wc-2022,morocco-spain-2,2026-06-30T10:00:00+00:00\n"
            "2024-07-10,EURO,2024,Semi-final,Netherlands,England,England,1,Netherlands,Player,Keeper,scored,uefa,https://example.test/euro-2024,netherlands-england-1,2026-06-30T10:00:00+00:00\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_cli_uses_dynamic_active_teams_and_supports_dry_run(self):
        source = (
            Path(__file__).parents[1] / "scripts" / "import_historical_shootouts.py"
        ).read_text(encoding="utf-8")
        self.assertIn("active_knockout_teams(repo)", source)
        self.assertIn('parser.add_argument("--dry-run"', source)

    def test_import_keeps_three_latest_official_senior_competitions_for_active_team(self):
        summary = import_historical_shootout_csv(
            self.repo,
            self.coverage,
            self.kicks,
            active_teams={"Morocco"},
        )

        coverage = self.repo.list_historical_shootout_coverage("Morocco")
        self.assertEqual(3, len(coverage))
        self.assertNotIn("2021", {row["competition_edition"] for row in coverage})
        self.assertEqual(3, summary.coverage_rows)
        self.assertEqual(2, summary.kick_rows)

    def test_import_is_idempotent_and_does_not_refresh_eliminated_team(self):
        import_historical_shootout_csv(
            self.repo,
            self.coverage,
            self.kicks,
            active_teams={"Netherlands"},
        )
        before = self.repo.list_historical_shootout_coverage("Netherlands")

        import_historical_shootout_csv(
            self.repo,
            self.coverage,
            self.kicks,
            active_teams={"Morocco"},
        )
        import_historical_shootout_csv(
            self.repo,
            self.coverage,
            self.kicks,
            active_teams={"Morocco"},
        )

        after = self.repo.list_historical_shootout_coverage("Netherlands")
        morocco_kicks = self.repo.list_historical_shootout_kicks(
            ("Morocco",), datetime(2026, 6, 28, tzinfo=timezone.utc)
        )
        self.assertEqual(before, after)
        self.assertEqual(2, len(morocco_kicks))

    def test_dry_run_validates_without_writing(self):
        summary = import_historical_shootout_csv(
            self.repo,
            self.coverage,
            self.kicks,
            active_teams={"Morocco"},
            dry_run=True,
        )

        self.assertEqual(3, summary.coverage_rows)
        self.assertEqual(2, summary.kick_rows)
        self.assertEqual([], self.repo.list_historical_shootout_coverage("Morocco"))


if __name__ == "__main__":
    unittest.main()
