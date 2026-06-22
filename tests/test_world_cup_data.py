import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository
from wcpredict.world_cup_data import dataset_sha256, parse_match_rows, parse_player_rows, parse_team_rows, parse_world_cup_schedule_rows
from wcpredict.daily_refresh import DatasetDownload
from wcpredict.world_cup_data import import_world_cup_download


class WorldCupDataTests(unittest.TestCase):
    def test_player_parser_normalizes_daily_fields_and_preserves_missing_values(self):
        csv_text = (
            "player,team,position,games,starts,minutes,goals,assists,shots,shots on target,passes,yellow cards,save %\n"
            "Aitana Example,Spain,MF,2,2,175,1,2,5,3,118,1,\n"
        )
        rows = parse_player_rows(csv_text)
        self.assertEqual("Aitana Example", rows[0]["player_name"])
        self.assertEqual("Spain", rows[0]["team_name"])
        self.assertEqual(175, rows[0]["minutes"])
        self.assertEqual(2, rows[0]["assists"])
        self.assertEqual(3, rows[0]["shots_on_target"])
        self.assertIsNone(rows[0]["save_percentage"])

    def test_parser_supports_current_fbref_style_dataset_headers(self):
        player = parse_player_rows(
            "player,team,games,games_starts,minutes,cards_yellow,cards_red,gk_save_pct\nKeeper,Canada,2,2,180,1,0,78.6\n"
        )[0]
        team = parse_team_rows(
            "team,games,goals,goals_against,cards_yellow,cards_red,shots\nCanada,2,4,1,3,0,25\n"
        )[0]
        match = parse_match_rows(
            "date,home_team,away_team,home_score,away_score,home_sot,away_sot,home_total_shots,away_total_shots,home_cards_yellow,away_cards_yellow\n"
            "2026-06-18,Canada,Qatar,2,1,6,2,14,7,2,4\n"
        )[0]
        self.assertEqual(2, player["starts"])
        self.assertEqual(1, player["yellow_cards"])
        self.assertEqual(78.6, player["save_percentage"])
        self.assertEqual(4, team["goals_for"])
        self.assertEqual(3, team["yellow_cards"])
        self.assertEqual(6, match["shots_on_target_a"])
        self.assertEqual(14, match["shots_a"])
        self.assertEqual(4, match["yellow_cards_b"])

    def test_match_and_team_parsers_keep_current_tournament_statistics(self):
        match_csv = (
            "date,home team,away team,home score,away score,home xg,away xg,home possession,away possession,home corners,away corners\n"
            "2026-06-18,Canada,Qatar,2,1,1.7,0.8,54,46,6,3\n"
        )
        team_csv = (
            "team,played,goals for,goals against,shots,corners,yellow cards\n"
            "Canada,2,3,1,24,11,3\n"
        )
        match = parse_match_rows(match_csv)[0]
        team = parse_team_rows(team_csv)[0]
        self.assertEqual(("Canada", "Qatar"), (match["team_a"], match["team_b"]))
        self.assertEqual((2, 1), (match["goals_a"], match["goals_b"]))
        self.assertEqual(1.7, match["xg_a"])
        self.assertEqual(6, match["corners_a"])
        self.assertEqual(24, team["shots"])
        self.assertEqual(3, team["yellow_cards"])

    def test_snapshot_ledger_is_idempotent_by_provider_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            digest = dataset_sha256(b"player,team,goals\nOne,Spain,1\n")
            checked = datetime(2026, 6, 19, 8, tzinfo=timezone.utc)
            first = repo.record_dataset_snapshot(
                "swaptr_wc2026_players", "v1", digest, checked, checked, 1, "ready", None
            )
            second = repo.record_dataset_snapshot(
                "swaptr_wc2026_players", "v1", digest, checked, checked, 1, "ready", None
            )
            self.assertEqual(first, second)
            self.assertEqual(1, len(repo.list_dataset_snapshots("swaptr_wc2026_players")))

    def test_daily_player_download_is_idempotently_available_to_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            download = DatasetDownload(
                "swaptr_wc2026_players", "v1",
                b"player,team,position,games,starts,minutes,goals,assists,shots,shots on target\nForward,Canada,FW,2,2,180,2,1,7,4\n",
                datetime(2026, 6, 19, tzinfo=timezone.utc), 1,
            )
            import_world_cup_download(repo, download, datetime(2026, 6, 19, 8, tzinfo=timezone.utc))
            import_world_cup_download(repo, download, datetime(2026, 6, 19, 9, tzinfo=timezone.utc))
            rows = repo.list_current_world_cup_players("Canada")
            self.assertEqual(1, len(rows))
            self.assertEqual(180, rows[0]["minutes"])
            self.assertEqual("swaptr_wc2026_players", rows[0]["provider_id"])
            analytics = repo.list_player_performance_rows()
            self.assertTrue(any(row["player_name"] == "Forward" and row["team_name"] == "Canada" for row in analytics))

    def test_finished_daily_matches_feed_history_immediately_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            canada = repo.upsert_team("Canada")
            qatar = repo.upsert_team("Qatar")
            repo.upsert_match(
                "FIFA World Cup 2026", "Grupo", datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
                canada, qatar, "scheduled", neutral_site=True,
            )
            download = DatasetDownload(
                "swaptr_wc2026_matches", "v1",
                b"date,home_team,away_team,home_score,away_score\n2026-06-18,Canada,Qatar,2,1\n2026-06-19,Spain,Japan,,\n",
                datetime(2026, 6, 19, tzinfo=timezone.utc), 2,
            )
            imported_at = datetime(2026, 6, 19, 8, tzinfo=timezone.utc)
            import_world_cup_download(repo, download, imported_at)
            import_world_cup_download(repo, download, imported_at)
            history = repo.list_historical_results_before(datetime(2026, 6, 20, tzinfo=timezone.utc))
            matching = [row for row in history if row.team_a == "Canada" and row.team_b == "Qatar"]
            self.assertEqual(1, len(matching))
            self.assertEqual((2, 1), (matching[0].goals_a, matching[0].goals_b))
            self.assertFalse(any(row.team_a == "Spain" and row.team_b == "Japan" for row in history))

    def test_daily_match_download_upserts_future_fixtures_and_skips_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            download = DatasetDownload(
                "swaptr_wc2026_matches", "v2",
                b"date,time,stage,home_team,away_team,venue,home_score,away_score\n"
                b"2026-06-21,19:00,Group stage,Spain,Saudi Arabia,Atlanta,,\n"
                b"2026-06-29,18:00,Round of 32,Winner Group A,TBD,Houston,,\n",
                datetime(2026, 6, 20, tzinfo=timezone.utc), 2,
            )
            imported_at = datetime(2026, 6, 20, 8, tzinfo=timezone.utc)
            import_world_cup_download(repo, download, imported_at)
            import_world_cup_download(repo, download, imported_at)

            matches = repo.list_matches()
            self.assertEqual(1, len(matches))
            self.assertEqual("Spain vs Saudi Arabia", matches[0].label)
            self.assertEqual(datetime(2026, 6, 21, 19, tzinfo=timezone.utc), matches[0].kickoff_utc)
            self.assertEqual("Group stage", matches[0].stage)
            self.assertEqual("Atlanta", matches[0].venue)

    def test_parser_keeps_fixture_time_stage_and_status(self):
        row = parse_match_rows(
            "date,kickoff_time,round,home_team,away_team,status\n"
            "2026-06-22,21:30,Group G,France,Iraq,scheduled\n"
        )[0]
        self.assertEqual("2026-06-22T21:30:00+00:00", row["kickoff_utc"])
        self.assertEqual("Group G", row["stage"])
        self.assertEqual("scheduled", row["status"])

    def test_open_results_schedule_keeps_only_2026_world_cup_rows(self):
        rows = parse_world_cup_schedule_rows(
            "date,home_team,away_team,home_score,away_score,tournament,city\n"
            "2026-06-20,Germany,Ivory Coast,NA,NA,FIFA World Cup,Toronto\n"
            "2026-06-20,Other,Team,1,0,Friendly,Madrid\n"
            "2022-12-18,Argentina,France,3,3,FIFA World Cup,Lusail\n"
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("Germany", rows[0]["team_a"])
        self.assertEqual("Toronto", rows[0]["venue"])


if __name__ == "__main__":
    unittest.main()
