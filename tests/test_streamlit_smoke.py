import unittest

from streamlit.testing.v1 import AppTest


class StreamlitSmokeTests(unittest.TestCase):
    def test_every_navigation_view_renders_without_exception(self):
        app = AppTest.from_file("app.py", default_timeout=120).run()
        self.assertEqual([], list(app.exception))

        for view in [
            "📊 Resumen",
            "🎯 Análisis predictivo",
            "👤 Jugadores",
            "📐 Calibración",
            "🗄️ Calidad de datos",
        ]:
            with self.subTest(view=view):
                app.sidebar.radio[0].set_value(view)
                app.run()
                self.assertEqual([], list(app.exception))

    def test_prediction_workspace_sections_render_without_exception(self):
        app = AppTest.from_file("app.py", default_timeout=120).run()
        prediction_view = next(
            option
            for option in app.sidebar.radio[0].options
            if "Análisis predictivo" in option
        )
        app.sidebar.radio[0].set_value(prediction_view)
        app.run()
        self.assertEqual([], list(app.exception))

        for section in [
            "Modelo",
            "Marcadores",
            "Estadísticas por equipo",
            "Jugadores",
            "Datos y fuentes",
            "Historial",
        ]:
            with self.subTest(section=section):
                control = next(
                    item
                    for item in app.segmented_control
                    if item.label == "Vista de análisis"
                )
                control.set_value(section)
                app.run()
                self.assertEqual([], list(app.exception))


if __name__ == "__main__":
    unittest.main()
