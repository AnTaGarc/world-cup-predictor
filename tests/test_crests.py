import unittest

from wcpredict.ui.crests import (
    TEAM_TO_FILE,
    crest_data_uri,
    crest_html,
    crest_path,
    team_with_crest_html,
)


class CrestMappingTests(unittest.TestCase):
    def test_canonical_names_resolve_to_files(self):
        for name in (
            "Spain", "Mexico", "South Korea", "USA", "Cote d'Ivoire",
            "Bosnia and Herzegovina", "Congo DR", "IR Iran", "Turkiye",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(crest_path(name), f"crest not found for {name}")

    def test_alias_resolution_falls_back_to_canonical(self):
        # 'Korea Republic' isn't a canonical name but is in the alias map.
        self.assertIsNotNone(crest_path("Korea Republic"))
        # Anything wcpredict.names normalises should also resolve via canonical_team_name.
        self.assertIsNotNone(crest_path("Estados Unidos"))

    def test_unknown_team_returns_none(self):
        self.assertIsNone(crest_path("Wakanda"))
        self.assertIsNone(crest_path(None))
        self.assertEqual("", crest_html(None))

    def test_html_renders_as_inline_img(self):
        html = crest_html("Mexico", size=24)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn('width="24"', html)
        self.assertIn('alt="Mexico"', html)

    def test_data_uri_is_cached_across_calls(self):
        first = crest_data_uri("Spain")
        second = crest_data_uri("Spain")
        self.assertIs(first, second)

    def test_team_with_crest_includes_name(self):
        html = team_with_crest_html("Japan")
        self.assertIn("Japan", html)
        self.assertIn("data:image/png;base64,", html)

    def test_mapping_covers_all_qualified_teams(self):
        # At minimum the 48 distinct canonical files must be present.
        unique_files = set(TEAM_TO_FILE.values())
        self.assertGreaterEqual(len(unique_files), 48)


if __name__ == "__main__":
    unittest.main()
