from datetime import date, datetime, timedelta, timezone
import unittest

from wcpredict.services import predict_match_markets
from wcpredict.ui.view_models import (
    coverage_summary,
    dataset_freshness_rows,
    ml_probability_rows,
    model_comparison_rows,
    model_disagreement_note,
    model_policy_rows,
    postmatch_queue_message,
    probability_chart_rows,
)
from wcpredict.ui.translations import (
    localize_confidence,
    localize_market,
    localize_market_family,
    localize_metric,
    localize_model,
    localize_resource_tier,
    localize_selection,
    localize_status,
    localize_table_columns,
)


class ViewModelTests(unittest.TestCase):
    def test_ml_probabilities_are_displayed_as_percentage_points(self):
        rows = ml_probability_rows(
            "Czechia", "South Africa",
            {"home": 0.5964, "draw": 0.2167, "away": 0.1869},
        )
        self.assertEqual([59.64, 21.67, 18.69], [row["Probabilidad (%)"] for row in rows])
        self.assertAlmostEqual(100.0, sum(row["Probabilidad (%)"] for row in rows), places=6)
    def test_probability_chart_keeps_exact_value_and_range_visible(self):
        predictions = predict_match_markets("Canada", "Qatar", [], date(2026, 6, 18))
        rows = probability_chart_rows(predictions, market_name="1X2")
        self.assertEqual(3, len(rows))
        self.assertIn("Probabilidad", rows[0])
        self.assertIn("Minimo", rows[0])
        self.assertIn("Maximo", rows[0])
        self.assertTrue(rows[0]["Etiqueta"].endswith("%"))

    def test_dataset_freshness_has_direct_current_stale_and_offline_labels(self):
        now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        rows = dataset_freshness_rows(
            [
                {"provider_id": "players", "status": "ready", "checked_at_utc": (now - timedelta(hours=2)).isoformat(), "data_updated_at_utc": now.isoformat(), "row_count": 700},
                {"provider_id": "matches", "status": "ready", "checked_at_utc": (now - timedelta(hours=40)).isoformat(), "data_updated_at_utc": (now - timedelta(hours=40)).isoformat(), "row_count": 12},
            ],
            [{"provider_id": "teams", "status": "failed", "checked_at_utc": now.isoformat(), "error_message": "offline"}],
            now,
        )
        by_id = {row["Proveedor"]: row for row in rows}
        self.assertEqual("Actual", by_id["players"]["Estado"])
        self.assertEqual("Obsoleto", by_id["matches"]["Estado"])
        self.assertEqual("Sin conexión", by_id["teams"]["Estado"])

    def test_model_policy_rows_expose_active_challenger_and_fallback(self):
        rows = model_policy_rows()
        corners = next(row for row in rows if row["Mercado"] == "córners")
        self.assertEqual("Binomial negativa", corners["Activo"])
        self.assertEqual("Poisson", corners["Fallback"])

    def test_visible_prediction_terms_are_localized_to_spanish(self):
        self.assertEqual("Empate", localize_selection("Draw"))
        self.assertEqual("Doble oportunidad", localize_market("Double Chance"))
        self.assertEqual("Baja", localize_confidence("low"))
        self.assertEqual("Matriz de marcadores", localize_model("score_matrix"))
        self.assertEqual("Actualizado", localize_status("updated"))
        self.assertEqual("Tiros a puerta del jugador", localize_market_family("player_shots_on_target"))
        self.assertEqual("Córners", localize_metric("corners"))
        self.assertEqual("Bajo", localize_resource_tier("low"))

    def test_internal_table_columns_are_hidden_behind_spanish_labels(self):
        localized = localize_table_columns(
            [{"player_name": "Aitana", "team_name": "España", "minutes": 90, "source_id": "open"}]
        )
        self.assertEqual(
            [{"Jugador": "Aitana", "Selección": "España", "Minutos": 90, "Fuente": "open"}],
            localized,
        )

    def test_coverage_counts_daily_players_when_lineup_is_missing(self):
        summary = coverage_summary(
            collector_statistics=1158,
            imported_lineups=0,
            daily_players=52,
            sources=4,
            deep_statistics=118,
        )

        self.assertEqual(52, summary["Jugadores disponibles"])
        self.assertEqual("No confirmada", summary["Alineación"])
        self.assertNotIn("Jugadores/alineación", summary)

    def test_model_comparison_uses_one_normalized_scale(self):
        rows = model_comparison_rows(
            "Czechia",
            "South Africa",
            {"home": 0.358, "draw": 0.299, "away": 0.343},
            {"home": 0.596, "draw": 0.217, "away": 0.187},
            {"home": 0.548, "draw": 0.233, "away": 0.219},
        )

        self.assertEqual(["Czechia", "Empate", "South Africa"], [row["Resultado"] for row in rows])
        self.assertAlmostEqual(100.0, sum(row["Modelo unificado 1X2 (%)"] for row in rows))
        self.assertAlmostEqual(100.0, sum(row["Matriz de marcadores (%)"] for row in rows))
        self.assertAlmostEqual(100.0, sum(row["ML cronológico (%)"] for row in rows))
        self.assertGreater(rows[0]["Modelo unificado 1X2 (%)"], rows[2]["Modelo unificado 1X2 (%)"])
        self.assertAlmostEqual(23.8, rows[0]["Diferencia (pp)"])
        note = model_disagreement_note(rows)
        self.assertIn("modelo operativo", note)
        self.assertIn("modelo unificado", note)
        self.assertNotIn("modelo de marcadores es el modelo operativo", note)

    def test_postmatch_queue_distinguishes_scores_from_imported_statistics(self):
        message = postmatch_queue_message(
            pending_scores=28,
            with_imported_statistics=28,
            missing_statistics=0,
        )

        self.assertIn("28 partidos necesitan marcador final", message)
        self.assertIn("28 ya tienen estadísticas importadas", message)
        self.assertIn("se usan en la forma de partidos posteriores", message)
        self.assertNotIn("pendientes de resultado/estadísticas", message)


if __name__ == "__main__":
    unittest.main()
