import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.repository import Repository
from wcpredict.squad_context import apply_squad_context


class SquadContextTests(unittest.TestCase):
    def test_named_suspension_removes_player_for_affected_match(self):
        kickoff = datetime(2026, 6, 24, 18, tzinfo=timezone.utc)
        players = [{
            "player_name": "Jugador Uno", "team_name": "Canada", "minutes": 180,
            "expected_minutes": 90, "starter_probability": 1.0, "availability": "available",
        }]
        events = [{
            "team_name": "Canada", "player_name": "Jugador Uno",
            "event_type": "suspension_red", "starts_at_utc": (kickoff - timedelta(days=2)).isoformat(),
            "ends_at_utc": (kickoff + timedelta(hours=3)).isoformat(), "affected_match_id": 7,
        }]
        adjusted, notes = apply_squad_context(players, events, kickoff, match_id=7)
        self.assertEqual("out", adjusted[0]["availability"])
        self.assertEqual(0.0, adjusted[0]["starter_probability"])
        self.assertIn("sanción por roja", notes[0])

    def test_coach_change_is_context_only(self):
        kickoff = datetime(2026, 6, 24, 18, tzinfo=timezone.utc)
        players = [{"player_name": "A", "team_name": "Qatar", "starter_probability": 1.0}]
        adjusted, notes = apply_squad_context(players, [{
            "team_name": "Qatar", "player_name": None, "event_type": "coach_change",
            "starts_at_utc": (kickoff - timedelta(days=5)).isoformat(), "ends_at_utc": None,
            "affected_match_id": None,
        }], kickoff, match_id=3)
        self.assertEqual(players, adjusted)
        self.assertIn("cambio de entrenador", notes[0])

    def test_repository_returns_only_active_reviewed_events(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            kickoff = datetime(2026, 6, 24, 18, tzinfo=timezone.utc)
            repo.save_squad_context_event({
                "team_name": "Canada", "player_name": "Jugador Uno", "event_type": "injury",
                "starts_at_utc": (kickoff - timedelta(days=1)).isoformat(),
                "ends_at_utc": (kickoff + timedelta(days=1)).isoformat(),
                "affected_match_id": None, "source_id": "manual-news", "evidence_status": "reviewed",
                "notes": "parte médico",
            }, kickoff - timedelta(days=1))
            active = repo.list_active_squad_context_events(("Canada", "Qatar"), kickoff, None)
        self.assertEqual(1, len(active))
        self.assertEqual("injury", active[0]["event_type"])


if __name__ == "__main__":
    unittest.main()
