import unittest
from datetime import date

from wcpredict.ratings import MatchResult, build_team_ratings


AS_OF = date(2026, 7, 4)


def _mr(day, a, b, ga, gb, kind="world_cup"):
    return MatchResult(date(2026, 6, day), a, b, ga, gb, kind)


def _league_context():
    """A balanced backdrop so global averages are stable: four mid teams
    trading 1-1 draws among themselves."""
    rows = []
    mids = ["MidA", "MidB", "MidC", "MidD"]
    day = 1
    for i in range(4):
        for j in range(i + 1, 4):
            rows.append(_mr(day, mids[i], mids[j], 1, 1))
            day += 1
    return rows


class OpponentAdjustedRatingTests(unittest.TestCase):
    def test_equal_opponents_match_single_pass(self):
        rows = _league_context()
        one = build_team_ratings(rows, AS_OF, iterations=1)
        three = build_team_ratings(rows, AS_OF, iterations=3)
        for team in one:
            self.assertAlmostEqual(one[team].attack, three[team].attack, places=6)
            self.assertAlmostEqual(one[team].defense, three[team].defense, places=6)

    def test_scoring_against_leaky_defense_earns_less_attack(self):
        rows = _league_context()
        # Leaky concedes plenty against the mid pack; Solid concedes nothing.
        rows += [
            _mr(10, "Leaky", "MidA", 0, 3),
            _mr(11, "Leaky", "MidB", 0, 3),
            _mr(12, "Solid", "MidC", 1, 0),
            _mr(13, "Solid", "MidD", 1, 0),
        ]
        # Two identical 3-0 wins: one against Leaky, one against Solid.
        rows += [
            _mr(20, "BeatsWeak", "Leaky", 3, 0),
            _mr(20, "BeatsStrong", "Solid", 3, 0),
        ]
        ratings = build_team_ratings(rows, AS_OF, iterations=3)
        self.assertGreater(
            ratings["Beatsstrong"].attack, ratings["Beatsweak"].attack,
        )

    def test_clean_sheet_against_weak_attack_earns_less_defense_credit(self):
        rows = _league_context()
        # Toothless never scores; Sharp scores freely.
        rows += [
            _mr(10, "Toothless", "MidA", 0, 1),
            _mr(11, "Toothless", "MidB", 0, 1),
            _mr(12, "Sharp", "MidC", 3, 1),
            _mr(13, "Sharp", "MidD", 3, 1),
        ]
        rows += [
            _mr(20, "WallVsWeak", "Toothless", 1, 0),
            _mr(20, "WallVsStrong", "Sharp", 1, 0),
        ]
        ratings = build_team_ratings(rows, AS_OF, iterations=3)
        # Lower defense value = concedes less than expected = better.
        self.assertLess(
            ratings["Wallvsstrong"].defense, ratings["Wallvsweak"].defense,
        )

    def test_iterations_converge(self):
        rows = _league_context() + [
            _mr(10, "Leaky", "MidA", 0, 3),
            _mr(20, "BeatsWeak", "Leaky", 3, 0),
        ]
        three = build_team_ratings(rows, AS_OF, iterations=3)
        six = build_team_ratings(rows, AS_OF, iterations=6)
        for team in three:
            self.assertLess(abs(three[team].attack - six[team].attack), 0.02)
            self.assertLess(abs(three[team].defense - six[team].defense), 0.02)
        # Determinism: same inputs, same outputs.
        again = build_team_ratings(rows, AS_OF, iterations=3)
        for team in three:
            self.assertEqual(three[team], again[team])
