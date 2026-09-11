import unittest

from wcpredict.ui.i18n import (
    CATALOGUES,
    localize_controlled,
    localize_table_columns,
    normalise_language,
    placeholders,
    translate,
)


class I18nTests(unittest.TestCase):
    def test_catalogues_have_identical_keys_and_placeholders(self):
        self.assertEqual(set(CATALOGUES["es"]), set(CATALOGUES["en"]))
        for key in CATALOGUES["es"]:
            self.assertEqual(
                placeholders(CATALOGUES["es"][key]),
                placeholders(CATALOGUES["en"][key]),
                key,
            )

    def test_translate_uses_contextual_football_terms(self):
        self.assertEqual(translate("metric.shots_on_target", language="es"), "Tiros a puerta")
        self.assertEqual(translate("metric.shots_on_target", language="en"), "Shots on target")
        self.assertEqual(translate("score.most_likely", language="en"), "Most likely scoreline")

    def test_invalid_language_is_not_accepted(self):
        self.assertIsNone(normalise_language("fr"))
        self.assertIsNone(normalise_language(None))
        self.assertEqual(normalise_language("ES"), "es")

    def test_plural_forms_are_language_specific(self):
        self.assertEqual(translate("count.matches", language="es", count=1), "1 partido")
        self.assertEqual(translate("count.matches", language="en", count=2), "2 matches")

    def test_missing_interpolation_is_reported_with_message_key(self):
        with self.assertRaisesRegex(ValueError, "message.welcome"):
            translate("message.welcome", language="en")

    def test_controlled_values_are_language_aware(self):
        self.assertEqual(localize_controlled("status", "updated", language="es"), "Actualizado")
        self.assertEqual(localize_controlled("status", "updated", language="en"), "Updated")
        self.assertEqual(localize_controlled("metric", "shots_on_target", language="en"), "Shots on target")
        self.assertEqual(localize_controlled("unknown", "raw_value", language="en"), "raw_value")

    def test_table_columns_change_without_mutating_values(self):
        rows = [{"player_name": "Diego Gomez", "shots_on_target": 3}]
        english = localize_table_columns(rows, language="en")
        self.assertEqual(english, [{"Player": "Diego Gomez", "Shots on target": 3}])
        self.assertEqual(rows, [{"player_name": "Diego Gomez", "shots_on_target": 3}])


if __name__ == "__main__":
    unittest.main()
