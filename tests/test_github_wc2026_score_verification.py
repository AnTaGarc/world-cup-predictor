import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.github_wc2026_dataset import import_github_wc2026_download
from wcpredict.repository import Repository


def _matches_csv_with(rows):
    header = (
        "match_id,date,kickoff_time_utc,stage_name,stadium_name,city,country,"
        "home_team_name,home_fifa_code,away_team_name,away_fifa_code,"
        "home_score,away_score,status,home_xg,away_xg,home_goalkeeper,away_goalkeeper,"
        "player_of_the_match_name,referee_name\n"
    )
    body = []
    for match_id, home, home_code, away, away_code, hs, aws, status in rows:
        hs_s = "" if hs is None else str(hs)
        aws_s = "" if aws is None else str(aws)
        body.append(
            f"{match_id},2026-06-11,2026-06-11T20:00:00Z,Group Stage,Stadium,City,USA,"
            f"{home},{home_code},{away},{away_code},{hs_s},{aws_s},{status},,,,,,\n"
        )
    return (header + "".join(body)).encode("utf-8")


class ScoreVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status, venue, neutral_site) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (100, "FIFA World Cup 2026", "Group Stage", "2026-06-11T20:00:00+00:00",
                 1, 2, "finished", "Estadio Azteca", 1),
            )
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, "
                "recorded_at_utc) VALUES(?, ?, ?, ?, ?)",
                (100, 2, 0, "verified_user_capture", self.now.isoformat()),
            )

    def tearDown(self):
        self.directory.cleanup()

    def _import(self, content: bytes) -> None:
        import_github_wc2026_download(
            self.repo,
            DatasetDownload("github_wc2026_matches", "sha:abc/parser-1", content, self.now, 1),
            self.now,
        )

    def test_match_when_dataset_agrees(self):
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 2, 0, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        with self.repo.session() as con:
            row = con.execute("SELECT status FROM gh_score_verifications").fetchone()
        self.assertEqual("match", row["status"])
        self.assertEqual([], self.repo.list_score_mismatches())

    def test_mismatch_when_dataset_disagrees(self):
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 3, 1, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        mismatches = self.repo.list_score_mismatches()
        self.assertEqual(1, len(mismatches))
        self.assertEqual(3, mismatches[0]["dataset_home_score"])
        self.assertEqual(2, mismatches[0]["local_home_score"])

    def test_no_local_result_status(self):
        with self.repo.session() as con:
            con.execute("DELETE FROM match_results")
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 2, 0, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        with self.repo.session() as con:
            row = con.execute("SELECT status FROM gh_score_verifications").fetchone()
        self.assertEqual("no_local_result", row["status"])
