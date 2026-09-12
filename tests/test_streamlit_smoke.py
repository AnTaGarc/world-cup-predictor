import unittest

from streamlit.testing.v1 import AppTest


class StreamlitSmokeTests(unittest.TestCase):
    def test_language_control_follows_the_main_navigation_in_sidebar(self):
        app = AppTest.from_file("app.py", default_timeout=120)
        app.session_state["ui_language"] = "es"
        app.run()

        self.assertEqual([], list(app.exception))
        self.assertEqual(["Vista", "Idioma"], [item.label for item in app.sidebar.radio])

    def test_dashboard_renders_contextual_english_copy(self):
        app = AppTest.from_file("app.py", default_timeout=120)
        app.session_state["ui_language"] = "en"
        app.run()
        self.assertEqual([], list(app.exception))
        visible = "\n".join(
            str(getattr(element, "value", ""))
            for element in app
        )
        visible += "\n" + "\n".join(expander.label for expander in app.expander)
        self.assertIn("2026 World Cup Predictive Analytics", visible)
        self.assertIn("Matches today and over the next two days", visible)
        self.assertIn("External dataset", visible)
        self.assertNotIn("Decidir con probabilidades, no con ruido", visible)
        self.assertNotIn("mominullptr", visible)

    def test_every_navigation_view_renders_without_exception(self):
        app = AppTest.from_file("app.py", default_timeout=120)
        app.session_state["ui_language"] = "es"
        app.run()
        self.assertEqual([], list(app.exception))

        for view in [
            "dashboard",
            "analysis",
            "players",
            "calibration",
            "quality",
        ]:
            with self.subTest(view=view):
                navigation = next(
                    item for item in app.sidebar.radio if item.label in {"Vista", "View"}
                )
                navigation.set_value(view)
                app.run()
                self.assertEqual([], list(app.exception))

    def test_prediction_workspace_sections_render_without_exception(self):
        app = AppTest.from_file("app.py", default_timeout=120)
        app.session_state["ui_language"] = "es"
        app.run()
        navigation = next(
            item for item in app.sidebar.radio if item.label in {"Vista", "View"}
        )
        navigation.set_value("analysis")
        app.run()
        self.assertEqual([], list(app.exception))

        for section in [
            "model",
            "scorelines",
            "team_stats",
            "players",
            "sources",
            "history",
        ]:
            with self.subTest(section=section):
                control = next(
                    item
                    for item in app.segmented_control
                    if item.label in {"Vista de análisis", "Analysis view"}
                )
                control.set_value(section)
                app.run()
                self.assertEqual([], list(app.exception))


if __name__ == "__main__":
    unittest.main()
