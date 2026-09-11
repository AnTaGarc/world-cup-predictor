import unittest
from pathlib import Path

from wcpredict.ui.language_preference import (
    COMPONENT_JS,
    STORAGE_KEY,
    PreferenceState,
    resolve_preference,
)


class LanguagePreferenceTests(unittest.TestCase):
    def test_selector_keeps_both_options_visible_with_subtle_active_state(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "language_preference.py").read_text(encoding="utf-8")
        self.assertIn('[aria-pressed="false"]', source)
        self.assertIn('[aria-pressed="true"]', source)
        self.assertIn("#f4f7fb", source)
        self.assertIn("#e8f1fd", source)

    def test_unresolved_component_withholds_the_application(self):
        self.assertEqual(resolve_preference(None, None), PreferenceState("resolving", None))

    def test_missing_storage_requires_first_visit_choice(self):
        state = resolve_preference({"ready": True, "language": None}, None)
        self.assertEqual(state, PreferenceState("unselected", None))

    def test_stored_language_bypasses_gate_in_new_session(self):
        state = resolve_preference({"ready": True, "language": "en"}, None)
        self.assertEqual(state, PreferenceState("resolved", "en"))

    def test_session_value_avoids_waiting_during_reruns(self):
        state = resolve_preference(None, "es")
        self.assertEqual(state, PreferenceState("resolved", "es"))

    def test_invalid_component_and_session_values_never_resolve(self):
        state = resolve_preference({"ready": True, "language": "fr"}, "de")
        self.assertEqual(state, PreferenceState("unselected", None))

    def test_component_reads_and_writes_namespaced_local_storage(self):
        self.assertEqual(STORAGE_KEY, "wcpredict.language.v1")
        self.assertIn("localStorage.getItem", COMPONENT_JS)
        self.assertIn("localStorage.setItem", COMPONENT_JS)
        self.assertIn("['es', 'en']", COMPONENT_JS)


if __name__ == "__main__":
    unittest.main()
