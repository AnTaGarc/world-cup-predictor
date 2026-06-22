from datetime import datetime, timezone
import unittest

from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import (
    derive_xg_factors_from_profile,
    predict_team_volume_markets,
)


def _row(team: str, metric: str, value: float, kickoff: str) -> dict:
    return {
        "team_name": team,
        "metric": metric,
        "value_number": value,
        "kickoff_utc": kickoff,
    }


class TeamProfileTests(unittest.TestCase):
    def test_profile_shrinks_toward_tournament_mean_with_small_sample(self):
        # Spain has 1 match with 10 corners; tournament average is 4.
        deep_rows = [
            _row("Spain", "resumen_del_partido.saques_de_esquina", 10, "2026-06-21T22:00:00+00:00"),
            _row("Brazil", "resumen_del_partido.saques_de_esquina", 4, "2026-06-15T19:00:00+00:00"),
            _row("France", "resumen_del_partido.saques_de_esquina", 3, "2026-06-15T19:00:00+00:00"),
            _row("Germany", "resumen_del_partido.saques_de_esquina", 5, "2026-06-15T19:00:00+00:00"),
            _row("England", "resumen_del_partido.saques_de_esquina", 4, "2026-06-15T19:00:00+00:00"),
        ]
        as_of = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
        profile = build_team_profile("Spain", deep_rows, as_of)
        spain_corners = profile.get("resumen_del_partido.saques_de_esquina")
        # Tournament mean = (10+4+3+5+4)/5 = 5.2. Spain has 1 weighted match.
        # With prior=4, shrunk = (1*10 + 4*5.2) / 5 ≈ 6.16
        self.assertLess(spain_corners, 10.0)
        self.assertGreater(spain_corners, 5.2)

    def test_recency_decay_reduces_weight_of_old_matches(self):
        deep_rows = [
            _row("Spain", "resumen_del_partido.saques_de_esquina", 10, "2024-06-01T00:00:00+00:00"),
            _row("Spain", "resumen_del_partido.saques_de_esquina", 10, "2026-06-21T22:00:00+00:00"),
            _row("Brazil", "resumen_del_partido.saques_de_esquina", 4, "2026-06-15T19:00:00+00:00"),
        ]
        as_of = datetime(2026, 6, 22, tzinfo=timezone.utc)
        # 1-year half-life: 2-year-old match contributes only ~0.25 weight.
        profile = build_team_profile("Spain", deep_rows, as_of, half_life_days=365)
        spain = profile.metrics["resumen_del_partido.saques_de_esquina"]
        # Effective sample weight is ~1.25 (one recent + 0.25 from old).
        self.assertLess(spain.sample_size, 2.0)
        self.assertGreater(spain.sample_size, 1.0)

    def test_dimension_score_positive_when_team_above_mean(self):
        deep_rows = [
            _row("Spain", "tiros.tiros_a_puerta", 10, "2026-06-21T22:00:00+00:00"),
            _row("Spain", "ataque.toques_dentro_del_area", 40, "2026-06-21T22:00:00+00:00"),
            _row("Brazil", "tiros.tiros_a_puerta", 3, "2026-06-15T19:00:00+00:00"),
            _row("Brazil", "ataque.toques_dentro_del_area", 15, "2026-06-15T19:00:00+00:00"),
        ]
        as_of = datetime(2026, 6, 22, tzinfo=timezone.utc)
        spain = build_team_profile("Spain", deep_rows, as_of, shrinkage_prior_matches=0.5)
        score = spain.dimension_score("offense")
        self.assertGreater(score, 0.0)

    def test_predict_team_volume_markets_returns_both_teams(self):
        deep_rows = [
            _row("Spain", "resumen_del_partido.saques_de_esquina", 8, "2026-06-21T22:00:00+00:00"),
            _row("Brazil", "resumen_del_partido.saques_de_esquina", 3, "2026-06-15T19:00:00+00:00"),
        ]
        as_of = datetime(2026, 6, 22, tzinfo=timezone.utc)
        a = build_team_profile("Spain", deep_rows, as_of)
        b = build_team_profile("Brazil", deep_rows, as_of)
        lines = predict_team_volume_markets(a, b)
        teams = {line.team_name for line in lines}
        self.assertIn("Spain", teams)
        self.assertIn("Brazil", teams)
        markets = {line.market for line in lines}
        self.assertIn("corners", markets)
        self.assertIn("yellow_cards", markets)
        # Spain should have higher expected corners than Brazil given inputs.
        spain_corners = next(l.expected for l in lines if l.team_name == "Spain" and l.market == "corners")
        brazil_corners = next(l.expected for l in lines if l.team_name == "Brazil" and l.market == "corners")
        self.assertGreater(spain_corners, brazil_corners)

    def test_xg_factors_within_bounds(self):
        deep_rows = [
            _row("Spain", "resumen_del_partido.goles_esperados_xg", 3.0, "2026-06-21T22:00:00+00:00"),
            _row("Brazil", "resumen_del_partido.goles_esperados_xg", 0.5, "2026-06-15T19:00:00+00:00"),
        ]
        as_of = datetime(2026, 6, 22, tzinfo=timezone.utc)
        a = build_team_profile("Spain", deep_rows, as_of)
        b = build_team_profile("Brazil", deep_rows, as_of)
        fa, fb, explanation = derive_xg_factors_from_profile(a, b)
        self.assertGreaterEqual(fa, 0.77)
        self.assertLessEqual(fa, 1.30)
        self.assertGreaterEqual(fb, 0.77)
        self.assertLessEqual(fb, 1.30)
        self.assertIn("Perfil profundo", explanation)


if __name__ == "__main__":
    unittest.main()
