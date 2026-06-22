import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


class TeamDeduplicationTests(unittest.TestCase):
    def test_deduplicate_teams_merges_duplicates_and_keeps_match_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            # Insert clean canonical first (will be the survivor because more matches).
            usa_canonical = repo.upsert_team("USA")
            australia = repo.upsert_team("Australia")
            canada = repo.upsert_team("Canada")
            # Now force a duplicate by writing the un-canonicalised name directly.
            with repo.session() as con:
                con.execute("INSERT INTO teams(name) VALUES('United States')")
                dup_id = con.execute(
                    "SELECT id FROM teams WHERE name='United States'"
                ).fetchone()["id"]
                # Two matches under the canonical name.
                con.execute(
                    "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status) "
                    "VALUES('FIFA World Cup 2026', 'Group', ?, ?, ?, 'scheduled')",
                    (datetime(2026, 6, 12, 0, tzinfo=timezone.utc).isoformat(), usa_canonical, australia),
                )
                con.execute(
                    "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status) "
                    "VALUES('FIFA World Cup 2026', 'Group', ?, ?, ?, 'scheduled')",
                    (datetime(2026, 6, 18, 0, tzinfo=timezone.utc).isoformat(), usa_canonical, canada),
                )
                # One match under the duplicate name (different opponent so no collision).
                con.execute(
                    "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status) "
                    "VALUES('Friendly', 'Group', ?, ?, ?, 'finished')",
                    (datetime(2026, 6, 1, 0, tzinfo=timezone.utc).isoformat(), dup_id, canada),
                )

            summary = repo.deduplicate_teams()

            with repo.session() as con:
                survivors = con.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
                remaining = con.execute(
                    "SELECT COUNT(*) FROM matches WHERE team_a_id=? OR team_b_id=?",
                    (usa_canonical, usa_canonical),
                ).fetchone()[0]
                still_has_dup = con.execute(
                    "SELECT COUNT(*) FROM teams WHERE name='United States'"
                ).fetchone()[0]

        self.assertEqual(0, still_has_dup)
        # The survivor row keeps the canonical name "USA".
        names = [row["name"] for row in survivors]
        self.assertIn("USA", names)
        self.assertNotIn("United States", names)
        # All three matches now reference the survivor.
        self.assertEqual(3, remaining)
        self.assertEqual(1, summary["groups"])
        self.assertEqual(1, summary["merged_teams"])

    def test_deduplicate_team_match_stats_collision_keeps_survivor_row(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            iran_canonical = repo.upsert_team("Iran")  # → "IR Iran"
            opponent = repo.upsert_team("New Zealand")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 18, 0, tzinfo=timezone.utc),
                iran_canonical, opponent, "finished",
            )
            with repo.session() as con:
                # Force the duplicate row that older code created.
                con.execute("INSERT INTO teams(name) VALUES('Ir Iran')")
                dup_id = con.execute("SELECT id FROM teams WHERE name='Ir Iran'").fetchone()["id"]
                # Survivor already has team_match_stats for this match.
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, goals, xg) VALUES(?, ?, 2, 1.8)",
                    (match_id, iran_canonical),
                )
                # Duplicate also has team_match_stats for the same match (collision).
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, goals, xg) VALUES(?, ?, 1, 0.5)",
                    (match_id, dup_id),
                )

            repo.deduplicate_teams()

            with repo.session() as con:
                rows = con.execute(
                    "SELECT team_id, goals, xg FROM team_match_stats WHERE match_id=?", (match_id,)
                ).fetchall()
        self.assertEqual(1, len(rows))
        # Survivor's row wins (goals=2, xg=1.8), not the duplicate's (goals=1, xg=0.5).
        self.assertEqual(iran_canonical, rows[0]["team_id"])
        self.assertEqual(2, rows[0]["goals"])
        self.assertAlmostEqual(1.8, rows[0]["xg"])


class MatchDeduplicationTests(unittest.TestCase):
    def test_duplicate_fixtures_collapse_into_the_one_with_data(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            mexico = repo.upsert_team("Mexico")
            korea = repo.upsert_team("South Korea")
            keep_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group A",
                datetime(2026, 6, 19, 1, tzinfo=timezone.utc),
                mexico, korea, "scheduled",
            )
            dup_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group A",
                datetime(2026, 6, 18, 12, tzinfo=timezone.utc),
                mexico, korea, "finished",
            )
            # Attach data only to the survivor candidate (keep).
            with repo.session() as con:
                con.execute(
                    "INSERT INTO team_match_stats(match_id, team_id, goals, xg) VALUES(?, ?, 2, 1.8)",
                    (keep_id, mexico),
                )
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, source_id, evidence_status, observed_at_utc) "
                    "VALUES(?, 'team', 'Mexico', 'shots', 17, 'src', 'verified', ?)",
                    (keep_id, datetime.now(timezone.utc).isoformat()),
                )

            summary = repo.deduplicate_matches()

            with repo.session() as con:
                remaining = con.execute(
                    "SELECT id FROM matches WHERE team_a_id=? AND team_b_id=?",
                    (mexico, korea),
                ).fetchall()
                stats_rows = con.execute(
                    "SELECT match_id, goals FROM team_match_stats"
                ).fetchall()
        # Only the keep_id row should survive.
        self.assertEqual(1, len(remaining))
        self.assertEqual(keep_id, remaining[0]["id"])
        self.assertEqual(1, summary["groups"])
        self.assertEqual(1, summary["merged_matches"])
        # Stats still attached to the survivor.
        self.assertEqual(1, len(stats_rows))
        self.assertEqual(keep_id, stats_rows[0]["match_id"])

    def test_fixtures_far_apart_in_time_are_not_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Brazil")
            b = repo.upsert_team("Morocco")
            # Same teams but a friendly months apart and a WC match — different
            # competitions already separate them; even within the same
            # competition, beyond the 48h window they survive independently.
            m1 = repo.upsert_match(
                "FIFA World Cup 2026", "Group",
                datetime(2026, 6, 13, 22, tzinfo=timezone.utc),
                a, b, "finished",
            )
            m2 = repo.upsert_match(
                "FIFA World Cup 2026", "Quarterfinals",
                datetime(2026, 7, 5, 22, tzinfo=timezone.utc),
                a, b, "scheduled",
            )
            summary = repo.deduplicate_matches()
        self.assertEqual(0, summary["merged_matches"])


if __name__ == "__main__":
    unittest.main()
