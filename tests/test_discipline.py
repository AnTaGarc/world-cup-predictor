import unittest
from datetime import datetime, timezone

from wcpredict.discipline import (
    CardRecord,
    PlayerDisciplineSnapshot,
    snapshot_suspensions,
    suspension_events_for_records,
)


class DisciplineRuleTests(unittest.TestCase):
    def test_two_group_yellows_create_one_match_suspension(self):
        records = [
            CardRecord(1, datetime(2026, 6, 12, tzinfo=timezone.utc), "Group stage - Group A", "Spain", "A Player", 1, 0),
            CardRecord(2, datetime(2026, 6, 18, tzinfo=timezone.utc), "Group stage - Group A", "Spain", "A Player", 1, 0),
        ]
        events = suspension_events_for_records(records, {"Spain": {2: 73}})
        self.assertEqual(1, len(events))
        self.assertEqual("suspension_yellows", events[0].event_type)
        self.assertEqual(73, events[0].affected_match_id)

    def test_group_yellow_does_not_carry_into_round_of_32(self):
        records = [
            CardRecord(1, datetime(2026, 6, 12, tzinfo=timezone.utc), "Group stage - Group A", "Spain", "A Player", 1, 0),
            CardRecord(73, datetime(2026, 6, 28, tzinfo=timezone.utc), "Round of 32", "Spain", "A Player", 1, 0),
        ]
        events = suspension_events_for_records(records, {"Spain": {73: 89}})
        self.assertEqual([], events)

    def test_red_card_creates_next_match_suspension_without_duplicate_yellow_ban(self):
        records = [
            CardRecord(1, datetime(2026, 6, 12, tzinfo=timezone.utc), "Group stage - Group A", "Spain", "A Player", 1, 0),
            CardRecord(2, datetime(2026, 6, 18, tzinfo=timezone.utc), "Group stage - Group A", "Spain", "A Player", 1, 1),
        ]
        events = suspension_events_for_records(records, {"Spain": {2: 73}})
        self.assertEqual(1, len(events))
        self.assertEqual("suspension_red", events[0].event_type)

    def test_player_snapshot_can_create_pending_yellow_and_red_suspensions(self):
        snapshots = [
            PlayerDisciplineSnapshot("Spain", "Yellow Player", 2, 0),
            PlayerDisciplineSnapshot("Spain", "Red Player", 0, 1),
        ]
        events = snapshot_suspensions(snapshots, {"Spain": 73})
        self.assertEqual(["suspension_yellows", "suspension_red"], [event.event_type for event in events])
        self.assertEqual({73}, {event.affected_match_id for event in events})


if __name__ == "__main__":
    unittest.main()
