import unittest

from wcpredict.player_impact import build_team_player_adjustment, adjust_expected_goals


class PlayerImpactTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"player_name": "Striker", "team_name": "Canada", "minutes": 270, "expected_minutes": 80, "starter_probability": 1.0, "goals": 3, "assists": 1, "shots_on_target": 7, "availability": "available"},
            {"player_name": "Creator", "team_name": "Canada", "minutes": 270, "expected_minutes": 75, "starter_probability": 1.0, "goals": 0, "assists": 3, "shots_on_target": 2, "availability": "available"},
        ]

    def test_no_player_evidence_leaves_baseline_unchanged(self):
        adjustment = build_team_player_adjustment([], "Canada")
        adjusted = adjust_expected_goals(1.4, 0.9, adjustment, build_team_player_adjustment([], "Qatar"))
        self.assertEqual(1.0, adjustment.attack_factor)
        self.assertEqual((1.4, 0.9), adjusted[:2])
        self.assertEqual("no_evidence", adjustment.confidence)

    def test_confirmed_absence_reduces_attack_more_than_uncertain_starter(self):
        absent_rows = [{**row, "availability": "out"} if row["player_name"] == "Striker" else row for row in self.rows]
        uncertain_rows = [{**row, "starter_probability": 0.5, "availability": "doubtful"} if row["player_name"] == "Striker" else row for row in self.rows]
        absent = build_team_player_adjustment(absent_rows, "Canada")
        uncertain = build_team_player_adjustment(uncertain_rows, "Canada")
        full = build_team_player_adjustment(self.rows, "Canada")
        self.assertLess(absent.attack_factor, uncertain.attack_factor)
        self.assertLess(uncertain.attack_factor, full.attack_factor)
        self.assertTrue(any("Striker" in row for row in absent.audit))

    def test_adjustments_are_bounded_and_shrunk_for_small_samples(self):
        dominant = [{**self.rows[0], "goals": 30, "minutes": 90}]
        adjustment = build_team_player_adjustment(dominant, "Canada")
        self.assertGreaterEqual(adjustment.attack_factor, 0.85)
        self.assertLessEqual(adjustment.attack_factor, 1.15)
        self.assertEqual("low", adjustment.confidence)

    def test_audit_exposes_availability_in_spanish(self):
        adjustment = build_team_player_adjustment(self.rows, "Canada")
        self.assertTrue(all("disponible" in row for row in adjustment.audit))

    def test_high_confidence_absences_deduct_more_than_low_confidence_absences(self):
        # A confirmed absence on a well-known striker should bite harder when we
        # have a high-confidence sample, because the dynamic cap widens the band
        # symmetrically. The factor itself stays inside the cap, but the
        # difference vs full-availability is larger than in the low-confidence
        # case.
        big_squad_present = [
            {
                "player_name": f"Starter {index}",
                "team_name": "Brazil",
                "minutes": 540,
                "expected_minutes": 85,
                "starter_probability": 1.0,
                "goals": 5 if index < 3 else 0,
                "assists": 3 if index < 6 else 0,
                "shots_on_target": 9 if index < 4 else 1,
                "availability": "available",
            }
            for index in range(11)
        ]
        big_squad_with_absences = [
            {**row, "availability": "out", "starter_probability": 0.0}
            if row["player_name"] in {"Starter 0", "Starter 1"} else row
            for row in big_squad_present
        ]
        small_squad_present = [
            {
                "player_name": "Single starter",
                "team_name": "Qatar",
                "minutes": 90,
                "expected_minutes": 85,
                "starter_probability": 1.0,
                "goals": 1,
                "assists": 0,
                "shots_on_target": 2,
                "availability": "available",
            }
        ]
        small_squad_absent = [
            {**row, "availability": "out", "starter_probability": 0.0}
            for row in small_squad_present
        ]
        high_present = build_team_player_adjustment(big_squad_present, "Brazil")
        high_absent = build_team_player_adjustment(big_squad_with_absences, "Brazil")
        low_present = build_team_player_adjustment(small_squad_present, "Qatar")
        low_absent = build_team_player_adjustment(small_squad_absent, "Qatar")
        self.assertEqual("high", high_present.confidence)
        self.assertEqual("low", low_present.confidence)
        high_drop = high_present.attack_factor - high_absent.attack_factor
        low_drop = low_present.attack_factor - low_absent.attack_factor
        # The dynamic cap means high-confidence deductions hit harder.
        self.assertGreater(high_drop, low_drop)
        # And the floor opens proportionally — never below 2 - cap.
        self.assertGreaterEqual(high_absent.attack_factor, 0.75)

    def test_long_player_audit_is_summarized_for_the_interface(self):
        many = [{**self.rows[0], "player_name": f"Jugador {index}"} for index in range(20)]
        adjustment = build_team_player_adjustment(many, "Canada")
        explanation = adjust_expected_goals(
            1.4, 0.9, adjustment, build_team_player_adjustment([], "Qatar")
        )[2]
        self.assertIn("8 jugadores más", explanation)


if __name__ == "__main__":
    unittest.main()
