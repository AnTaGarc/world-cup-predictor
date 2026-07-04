import unittest
from datetime import datetime, timezone

from wcpredict.opponent_normalization import (
    build_conceded_rates,
    normalization_factor,
)
from wcpredict.team_profile import build_team_profiles


METRIC = "resumen_del_partido.saques_de_esquina"


def _row(kickoff, team, value, metric=METRIC):
    return {
        "kickoff_utc": kickoff,
        "team_name": team,
        "metric": metric,
        "value_number": value,
        "competition": "FIFA World Cup 2026",
    }


class ConcededRateTests(unittest.TestCase):
    def test_each_team_concedes_the_rivals_value(self):
        rows = [
            _row("2026-06-10T20:00:00+00:00", "Leaky", 2, METRIC),
            _row("2026-06-10T20:00:00+00:00", "Alpha", 9, METRIC),
        ]
        rates = build_conceded_rates(rows)
        self.assertEqual(9.0, rates[("Leaky", METRIC)])
        self.assertEqual(2.0, rates[("Alpha", METRIC)])

    def test_single_sided_matches_are_ignored(self):
        rows = [_row("2026-06-10T20:00:00+00:00", "Alpha", 9, METRIC)]
        self.assertEqual({}, build_conceded_rates(rows))


class FactorTests(unittest.TestCase):
    def test_inactive_metric_is_neutral(self):
        self.assertEqual(
            1.0,
            normalization_factor({("X", METRIC): 8.0}, 4.0, "X", METRIC, frozenset()),
        )

    def test_generous_conceder_discounts_value(self):
        factor = normalization_factor(
            {("X", METRIC): 8.0}, 4.0, "X", METRIC, frozenset({METRIC})
        )
        self.assertEqual(0.6, max(0.6, factor))
        self.assertLess(factor, 1.0)

    def test_stingy_conceder_boosts_value_with_clamp(self):
        factor = normalization_factor(
            {("X", METRIC): 1.0}, 4.0, "X", METRIC, frozenset({METRIC})
        )
        self.assertEqual(1.6, factor)


class ProfileIntegrationTests(unittest.TestCase):
    def test_created_value_is_normalized_when_metric_active(self):
        # Alpha bags 9 corners against Leaky (who concedes 9 on average,
        # tournament mean lower) => normalized profile value drops.
        rows = [
            # Backdrop: two mid teams trading modest corner counts.
            _row("2026-06-08T20:00:00+00:00", "MidA", 4),
            _row("2026-06-08T20:00:00+00:00", "MidB", 4),
            # Leaky concedes a lot to MidA too.
            _row("2026-06-09T20:00:00+00:00", "Leaky", 1),
            _row("2026-06-09T20:00:00+00:00", "MidA", 9),
            # Alpha's only match: 9 corners vs Leaky.
            _row("2026-06-10T20:00:00+00:00", "Alpha", 9),
            _row("2026-06-10T20:00:00+00:00", "Leaky", 1),
        ]
        as_of = datetime(2026, 7, 1, tzinfo=timezone.utc)
        raw = build_team_profiles(("Alpha",), rows, as_of, normalized_metrics=frozenset())["Alpha"]
        normalized = build_team_profiles(
            ("Alpha",), rows, as_of, normalized_metrics=frozenset({METRIC})
        )["Alpha"]
        self.assertLess(normalized.get(METRIC), raw.get(METRIC))
