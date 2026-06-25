import unittest

from wcpredict.names import canonical_team_name, same_team


class TeamNameTests(unittest.TestCase):
    def test_provider_aliases_share_one_canonical_name(self):
        self.assertEqual("Czechia", canonical_team_name("Czech Republic"))
        self.assertEqual("South Korea", canonical_team_name("Korea Republic"))
        self.assertEqual("USA", canonical_team_name("United States"))
        self.assertEqual("Cote d'Ivoire", canonical_team_name("Ivory Coast"))
        self.assertTrue(same_team("Ivory Coast", "Cote d'Ivoire"))
        self.assertEqual(
            "Bosnia and Herzegovina",
            canonical_team_name("Bosnia & Herzegovina"),
        )
        self.assertEqual(
            "Bosnia and Herzegovina",
            canonical_team_name("Bosnia y Herzegovina"),
        )

    def test_matching_ignores_accents_and_provider_aliases(self):
        self.assertTrue(same_team("México", "Mexico"))
        self.assertTrue(same_team("Czech Republic", "Czechia"))
        self.assertFalse(same_team("South Korea", "South Africa"))

    def test_canonical_names_are_self_idempotent(self):
        """Calling canonical_team_name on its own output must return the same
        canonical name. Otherwise we end up with duplicate rows in the DB
        (e.g. 'IR Iran' falling through to 'Ir Iran')."""
        canonicals = [
            "IR Iran", "Congo DR", "USA", "Cote d'Ivoire",
            "South Korea", "Czechia", "Bosnia and Herzegovina",
            "Saudi Arabia", "South Africa", "New Zealand", "Turkiye",
        ]
        for name in canonicals:
            with self.subTest(name=name):
                self.assertEqual(name, canonical_team_name(name))
                self.assertTrue(same_team(name, canonical_team_name(name)))

    def test_spanish_world_cup_names_resolve_to_schedule_entities(self):
        expected = {
            "México": "Mexico", "Sudáfrica": "South Africa",
            "Corea del Sur": "South Korea", "Bosnia": "Bosnia and Herzegovina",
            "Estados Unidos": "USA", "Suiza": "Switzerland",
            "RD Congo": "Congo DR", "Irán": "IR Iran",
        }
        for supplied, canonical in expected.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(canonical, canonical_team_name(supplied))


if __name__ == "__main__":
    unittest.main()
