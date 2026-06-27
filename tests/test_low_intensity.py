"""Phase 7 tests: MD3 dead-rubber detector + downstream weight cut."""
import unittest
from datetime import datetime, timezone

from wcpredict.historical_relevance import (
    LOW_INTENSITY_FACTOR,
    compute_match_weight,
)
from wcpredict.low_intensity import (
    is_low_intensity_match,
    mark_low_intensity_rows,
)


class _StubTeam:
    def __init__(self, name): self.name = name


class _StubMatch:
    def __init__(self, mid, team_a, team_b, stage="Group stage - Group A"):
        self.id = mid
        self.team_a = _StubTeam(team_a)
        self.team_b = _StubTeam(team_b)
        self.stage = stage


class IsLowIntensityTests(unittest.TestCase):
    def _fixtures(self):
        # Group A: A, B, C, D — 6 fixtures: AB, CD, AC, BD, AD, BC.
        return [
            {"id": 1, "team_a": "A", "team_b": "B"},
            {"id": 2, "team_a": "C", "team_b": "D"},
            {"id": 3, "team_a": "A", "team_b": "C"},
            {"id": 4, "team_a": "B", "team_b": "D"},
            {"id": 5, "team_a": "A", "team_b": "D"},
            {"id": 6, "team_a": "B", "team_b": "C"},
        ]

    def test_md3_with_team_already_qualified_marks_them(self):
        # A: wins MD1 (vs B 2-0), wins MD2 (vs C 2-0) → 6 pts, top-2 guaranteed.
        # On MD3 (match 5: A vs D) A is already classified.
        # D: draws MD1 (vs C), wins MD2 (vs B) → 4 pts.
        # In match 6 (B vs C) D doesn't play, max C reaches is 4 pts.
        # So D is also guaranteed top-2 with 4 pts.
        results = {
            1: (2, 0, "A", "B"),
            2: (1, 1, "C", "D"),
            3: (2, 0, "A", "C"),
            4: (0, 2, "B", "D"),
        }
        match_ad = _StubMatch(5, "A", "D")
        a_low, d_low = is_low_intensity_match(match_ad, self._fixtures(), results)
        self.assertTrue(a_low, "A classified — top-2 guaranteed regardless of MD3")
        self.assertTrue(d_low, "D also classified with 4 pts and only B/C left to play")

    def test_md3_with_team_uncertain_not_marked(self):
        # All four teams on 3 pts after MD2 → tightly contested.
        results = {
            1: (1, 0, "A", "B"),
            2: (1, 0, "C", "D"),
            3: (0, 1, "A", "C"),
            4: (1, 0, "B", "D"),
        }
        # A: 3, B: 3, C: 6, D: 0. C is qualified, D is eliminated.
        # In match AD (id 5), A's fate depends on match 6 (BC).
        match_ad = _StubMatch(5, "A", "D")
        a_low, d_low = is_low_intensity_match(match_ad, self._fixtures(), results)
        self.assertFalse(a_low, "A on 3 pts could climb or fall depending on MD3")
        self.assertTrue(d_low, "D on 0 pts cannot reach top-2 if it doesn't play")

    def test_team_eliminated_also_flagged(self):
        # B is on 0 pts after 2 games: even winning MD3 keeps it bottom.
        results = {
            1: (2, 0, "A", "B"),
            2: (0, 0, "C", "D"),
            3: (3, 0, "A", "C"),
            4: (0, 2, "B", "D"),
        }
        match_bc = _StubMatch(6, "B", "C")
        b_low, c_low = is_low_intensity_match(match_bc, self._fixtures(), results)
        # B has 0 pts, plays C: best case 3 pts. A has 6, D has 4 → B stays bottom-2.
        self.assertTrue(b_low)

    def test_no_group_returns_false(self):
        match = _StubMatch(99, "A", "B", stage="Round of 32")
        a_low, b_low = is_low_intensity_match(match, [], {})
        self.assertFalse(a_low)
        self.assertFalse(b_low)


class MarkLowIntensityRowsTests(unittest.TestCase):
    def test_mark_only_listed_pairs(self):
        rows = [
            {"kickoff_utc": "2026-06-26T17:00:00+00:00", "team_name": "Spain"},
            {"kickoff_utc": "2026-06-26T17:00:00+00:00", "team_name": "Italy"},
            {"kickoff_utc": "2026-06-20T17:00:00+00:00", "team_name": "Spain"},
        ]
        marked = mark_low_intensity_rows(rows, {("2026-06-26", "Spain")})
        self.assertTrue(marked[0]["_low_intensity"])
        self.assertNotIn("_low_intensity", marked[1])
        self.assertNotIn("_low_intensity", marked[2])


class CompositeWeightWithLowIntensityTests(unittest.TestCase):
    def test_low_intensity_cuts_to_thirty_percent(self):
        played = datetime(2026, 6, 25, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 26, tzinfo=timezone.utc)
        full = compute_match_weight(
            "resumen_del_partido.goles_esperados_xg",
            played, as_of,
            competition="FIFA World Cup 2026",
        )
        reduced = compute_match_weight(
            "resumen_del_partido.goles_esperados_xg",
            played, as_of,
            competition="FIFA World Cup 2026",
            low_intensity=True,
        )
        self.assertAlmostEqual(reduced, full * LOW_INTENSITY_FACTOR, places=4)


if __name__ == "__main__":
    unittest.main()
