import unittest

from wcpredict.market_catalog import default_market_rows, normalize_market_rows
from wcpredict.ui.translations import canonical_market, canonical_market_family, canonical_selection, localize_market


class MarketCatalogTests(unittest.TestCase):
    def test_default_market_rows_cover_match_volume_and_player_families(self):
        rows = default_market_rows("Spain", "Japan")
        families = {row["market_family"] for row in rows}
        self.assertTrue(
            {
                "match_result",
                "goals",
                "corners",
                "cards",
                "shots",
                "shots_on_target",
                "player_goal",
                "player_shots",
                "player_cards",
            }.issubset(families)
        )

    def test_normalize_market_rows_keeps_only_rows_with_decimal_odds(self):
        rows = [
            {
                "market_family": "match_result",
                "market_name": "1X2",
                "selection_name": "Spain",
                "line": "",
                "decimal_odds": "2.25",
                "bookmaker": "Winamax",
            },
            {
                "market_family": "goals",
                "market_name": "Over/Under 2.5",
                "selection_name": "Over 2.5",
                "line": "2.5",
                "decimal_odds": "",
                "bookmaker": "Winamax",
            },
        ]
        normalized = normalize_market_rows(rows)
        self.assertEqual(1, len(normalized))
        self.assertEqual(2.25, normalized[0]["decimal_odds"])
        self.assertIsNone(normalized[0]["line"])

    def test_spanish_market_labels_roundtrip_to_canonical_model_keys(self):
        self.assertEqual("Double Chance", canonical_market("Doble oportunidad"))
        self.assertEqual("Over/Under 2.5", canonical_market("Más/menos de 2.5"))
        self.assertEqual("Over 2.5", canonical_selection("Más de 2.5"))
        self.assertEqual("Draw", canonical_selection("Empate"))
        self.assertEqual("Tiros a puerta del jugador 0.5", localize_market("Player Shots On Target 0.5"))
        self.assertEqual("player_shots", canonical_market_family("Tiros del jugador"))


if __name__ == "__main__":
    unittest.main()
