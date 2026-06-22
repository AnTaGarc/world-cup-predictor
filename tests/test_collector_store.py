from datetime import date
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from wcpredict.collector_store import CollectorStore


SCHEMA = """
CREATE TABLE participants(id INTEGER PRIMARY KEY, canonical_name TEXT, participant_type TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY, canonical_key TEXT, start_time_utc TEXT, status TEXT, venue TEXT, participant1_id INTEGER, participant2_id INTEGER, result_json TEXT, updated_at_utc TEXT);
CREATE TABLE sources(id TEXT PRIMARY KEY, retrieved_at_utc TEXT, status TEXT, confidence REAL, source_url TEXT);
CREATE TABLE statistics(id INTEGER PRIMARY KEY, event_id INTEGER, subject_type TEXT, subject_id INTEGER, metric TEXT, value_number REAL, value_text TEXT, unit TEXT, context_json TEXT, source_id TEXT, evidence_status TEXT, sample_size INTEGER, observed_at_utc TEXT);
CREATE TABLE lineups(id INTEGER PRIMARY KEY, event_id INTEGER, participant_id INTEGER, player_id INTEGER, lineup_status TEXT, position TEXT, shirt_number TEXT, source_id TEXT, observed_at_utc TEXT);
CREATE TABLE availability(id INTEGER PRIMARY KEY, event_id INTEGER, participant_id INTEGER, availability_type TEXT, status TEXT, detail TEXT, source_id TEXT, observed_at_utc TEXT);
"""


class CollectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sports.db"
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript(SCHEMA)
            con.executemany(
                "INSERT INTO participants VALUES(?, ?, ?)",
                [(1, "czechia", "team"), (2, "south africa", "team"), (3, "Jan Player", "player"), (4, "germany", "team"), (5, "curacao", "team")],
            )
            con.execute(
                "INSERT INTO events VALUES(10, ?, ?, ?, ?, 1, 2, ?, ?)",
                ("football|world cup|2026-06-18|czechia|south africa", "2026-06-18T16:00:00+00:00", "scheduled", "Atlanta", "{}", "2026-06-18T14:00:00+00:00"),
            )
            con.execute(
                "INSERT INTO events VALUES(11, ?, ?, ?, ?, 4, 5, ?, ?)",
                ("football|world cup|2026-06-14|germany|curacao", "2026-06-14T17:00:00+00:00", "finished", "Houston", '{"home": 7, "away": 1}', "2026-06-14T20:00:00+00:00"),
            )
            con.execute("INSERT INTO sources VALUES('s1', ?, 'verified', .9, ?)", ("2026-06-18T14:00:00+00:00", "https://provider.test/event/10"))
            con.execute(
                "INSERT INTO statistics VALUES(1, 10, 'team', 1, 'corners_for_avg', 5.2, NULL, 'per_match', '{}', 's1', 'verified', 10, ?)",
                ("2026-06-18T14:00:00+00:00",),
            )
            con.execute(
                "INSERT INTO lineups VALUES(1, 10, 1, 3, 'expected', 'FW', '9', 's1', ?)",
                ("2026-06-18T14:00:00+00:00",),
            )
            con.execute(
                "INSERT INTO availability VALUES(1, 10, 3, 'injury', 'doubtful', 'ankle', 's1', ?)",
                ("2026-06-18T14:00:00+00:00",),
            )
            con.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_event_using_provider_alias(self):
        bundle = CollectorStore(self.path).find_event(
            "Czech Republic", "South Africa", date(2026, 6, 18)
        )
        self.assertIsNotNone(bundle)
        self.assertEqual(10, bundle.event_id)
        self.assertEqual("Czechia", bundle.team_a)
        self.assertEqual(1, len(bundle.statistics))
        self.assertEqual(1, len(bundle.lineups))
        self.assertEqual(1, len(bundle.availability))
        self.assertEqual([], bundle.missing_critical)

    def test_returns_none_without_compatible_event(self):
        self.assertIsNone(
            CollectorStore(self.path).find_event(
                "Canada", "Qatar", date(2026, 6, 18)
            )
        )

    def test_partial_bundle_reports_missing_evidence(self):
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("DELETE FROM statistics")
            con.execute("DELETE FROM lineups")
            con.commit()
        bundle = CollectorStore(self.path).find_event(
            "Czechia", "South Africa", date(2026, 6, 18)
        )
        self.assertIn("team_statistics", bundle.missing_critical)
        self.assertIn("players", bundle.missing_optional)

    def test_finished_results_feed_current_form(self):
        rows = CollectorStore(self.path).list_finished_results(date(2026, 6, 18))
        self.assertEqual(1, len(rows))
        self.assertEqual(("Germany", "Curacao", 7, 1), (rows[0].team_a, rows[0].team_b, rows[0].goals_a, rows[0].goals_b))


if __name__ == "__main__":
    unittest.main()
