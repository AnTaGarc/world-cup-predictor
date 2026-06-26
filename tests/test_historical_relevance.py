import unittest
from datetime import datetime, timezone

from wcpredict.historical_relevance import (
    competition_weight,
    compute_match_weight,
    metric_family,
    opponent_weight,
    recency_weight,
)


class MetricFamilyTests(unittest.TestCase):
    def test_xg_falls_into_offense(self):
        self.assertEqual("offense", metric_family("resumen_del_partido.goles_esperados_xg"))

    def test_cards_fall_into_discipline(self):
        self.assertEqual("discipline", metric_family("resumen_del_partido.tarjetas_amarillas"))
        self.assertEqual("discipline", metric_family("resumen_del_partido.faltas"))

    def test_possession_falls_into_tactical(self):
        self.assertEqual("tactical", metric_family("resumen_del_partido.posesion_de_balon_pct"))

    def test_defense_stays_defense(self):
        self.assertEqual("defense", metric_family("defensa.intercepciones"))


class RecencyTests(unittest.TestCase):
    def test_tactical_decays_slower_than_offense_over_two_years(self):
        played = datetime(2024, 6, 1, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        tactical = recency_weight(played, as_of, family="tactical")
        offense = recency_weight(played, as_of, family="offense")
        # tactical half-life 900d vs offense 540d: tactical retains more weight.
        self.assertGreater(tactical, offense)
        self.assertGreater(tactical, 0.4)
        self.assertLess(offense, 0.5)

    def test_future_match_returns_zero(self):
        played = datetime(2027, 1, 1, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(0.0, recency_weight(played, as_of, family="offense"))

    def test_override_half_life_takes_precedence(self):
        played = datetime(2025, 6, 1, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        w_default = recency_weight(played, as_of, family="offense")
        w_short = recency_weight(played, as_of, half_life_days=100)
        self.assertLess(w_short, w_default)


class CompetitionTests(unittest.TestCase):
    def test_world_cup_2026_is_strongest(self):
        as_of = datetime(2026, 6, 22, tzinfo=timezone.utc)
        wc = competition_weight("FIFA World Cup 2026", as_of)
        qual = competition_weight("WC Qualification UEFA", as_of)
        friendly = competition_weight("International Friendly", as_of)
        self.assertGreater(wc, qual)
        self.assertGreater(qual, friendly)
        self.assertAlmostEqual(wc, 3.00)
        self.assertAlmostEqual(qual, 1.00)
        self.assertAlmostEqual(friendly, 0.55)

    def test_friendlies_in_non_wc_year_decay_further(self):
        as_of_2025 = datetime(2025, 5, 1, tzinfo=timezone.utc)
        as_of_2026 = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.assertGreater(
            competition_weight("International Friendly", as_of_2026),
            competition_weight("International Friendly", as_of_2025),
        )

    def test_nations_league_tier_aware(self):
        as_of = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.assertGreater(
            competition_weight("UEFA Nations League A", as_of),
            competition_weight("UEFA Nations League C", as_of),
        )


class OpponentTests(unittest.TestCase):
    def test_unknown_opponent_returns_one(self):
        self.assertEqual(1.0, opponent_weight(None, 1.5))

    def test_strong_opponent_boosts_weight(self):
        self.assertGreater(opponent_weight(2.0, 1.0), 1.0)

    def test_capped_in_range(self):
        self.assertLessEqual(opponent_weight(100.0, 1.0), 2.5)
        self.assertGreaterEqual(opponent_weight(0.01, 1.0), 0.4)


class CompositeTests(unittest.TestCase):
    def test_combines_all_factors(self):
        played = datetime(2025, 11, 1, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 15, tzinfo=timezone.utc)
        w = compute_match_weight(
            "resumen_del_partido.goles_esperados_xg",
            played, as_of,
            competition="WC Qualification UEFA",
            opponent_strength=1.5, mean_strength=1.0,
        )
        # ~0.62 recency × 1.0 comp × 1.5 opp × 1.0 roster ≈ 0.93
        self.assertGreater(w, 0.6)
        self.assertLess(w, 1.3)

    def test_returns_zero_for_future_match(self):
        played = datetime(2027, 1, 1, tzinfo=timezone.utc)
        as_of = datetime(2026, 6, 15, tzinfo=timezone.utc)
        self.assertEqual(0.0, compute_match_weight(
            "resumen_del_partido.goles_esperados_xg", played, as_of,
            competition="FIFA World Cup 2026",
        ))


if __name__ == "__main__":
    unittest.main()
