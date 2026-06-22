import unittest

from wcpredict.prediction_report import build_prediction_report


class PredictionReportTests(unittest.TestCase):
    def test_report_uses_structured_skill_style_and_discloses_uncertainty(self):
        report = build_prediction_report(
            team_a="Canada",
            team_b="Qatar",
            probabilities={"Canada": 0.52, "Draw": 0.27, "Qatar": 0.21},
            form_notes=["Canada: 2 victorias recientes"],
            player_notes=["Delantero de Canada: duda, 50%"],
            context_notes=["Sede neutral; 29 °C"],
            sources=[{"label": "World Cup players", "status": "current", "updated_at": "2026-06-19"}],
            model={"active": "score_matrix", "challenger": "calibrated_multinomial_blend"},
            missing_data=["Árbitro no confirmado"],
        )
        for heading in (
            "## Conclusión principal", "## Probabilidades", "## Estado de forma",
            "## Jugadores y alineación", "## Contexto del partido", "## Incertidumbre",
            "## Fuentes", "## Modelo y calibración",
        ):
            self.assertIn(heading, report)
        self.assertIn("52.0%", report)
        self.assertIn("Árbitro no confirmado", report)


if __name__ == "__main__":
    unittest.main()
