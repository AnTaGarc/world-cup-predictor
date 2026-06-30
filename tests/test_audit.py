import unittest

from wcpredict.audit import (
    SEVERITY_COLORS,
    audit_rows_to_records,
    build_match_audit,
    build_per_team_audit,
)


class AuditTests(unittest.TestCase):
    def test_outcome_row_marks_a_correct_prediction_as_good(self):
        audit = build_match_audit(
            team_a="Brazil", team_b="Haiti", goals_a=2, goals_b=0,
            primary_1x2={"home": 0.74, "draw": 0.16, "away": 0.10},
            mode_score=(2, 0), expected_score=(2.6, 0.7),
            team_a_stats=None, team_b_stats=None,
        )
        self.assertEqual(1, len(audit["outcome"]))
        self.assertEqual("good", audit["outcome"][0].severity)
        self.assertIn("Brazil", audit["outcome"][0].predicted)
        self.assertIn("Brazil", audit["outcome"][0].actual)

    def test_outcome_row_marks_a_moderate_miss_as_warning(self):
        audit = build_match_audit(
            team_a="USA", team_b="Australia", goals_a=0, goals_b=2,
            primary_1x2={"home": 0.48, "draw": 0.24, "away": 0.28},
            mode_score=(1, 0), expected_score=(1.4, 0.9),
            team_a_stats=None, team_b_stats=None,
        )
        self.assertEqual("warn", audit["outcome"][0].severity)
        self.assertIn("Australia", audit["outcome"][0].actual)

    def test_outcome_row_treats_a_near_coin_flip_miss_as_ok(self):
        audit = build_match_audit(
            team_a="A", team_b="B", goals_a=0, goals_b=1,
            primary_1x2={"home": 0.46, "draw": 0.10, "away": 0.44},
            mode_score=(1, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )

        self.assertEqual("ok", audit["outcome"][0].severity)

    def test_outcome_row_reserves_bad_for_a_high_confidence_miss(self):
        audit = build_match_audit(
            team_a="A", team_b="B", goals_a=0, goals_b=2,
            primary_1x2={"home": 0.80, "draw": 0.10, "away": 0.10},
            mode_score=(3, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )

        self.assertEqual("bad", audit["outcome"][0].severity)
        self.assertEqual("bad", audit["score"][0].severity)

    def test_mode_score_severity_grades_distance_in_goals(self):
        exact = build_match_audit(
            team_a="A", team_b="B", goals_a=1, goals_b=0,
            primary_1x2={"home": 0.5, "draw": 0.3, "away": 0.2},
            mode_score=(1, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )
        miss_by_one = build_match_audit(
            team_a="A", team_b="B", goals_a=2, goals_b=0,
            primary_1x2={"home": 0.5, "draw": 0.3, "away": 0.2},
            mode_score=(1, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )
        miss_by_two = build_match_audit(
            team_a="A", team_b="B", goals_a=3, goals_b=0,
            primary_1x2={"home": 0.5, "draw": 0.3, "away": 0.2},
            mode_score=(1, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )
        miss_by_four = build_match_audit(
            team_a="A", team_b="B", goals_a=4, goals_b=1,
            primary_1x2={"home": 0.5, "draw": 0.3, "away": 0.2},
            mode_score=(1, 0), expected_score=None,
            team_a_stats=None, team_b_stats=None,
        )
        self.assertEqual("good", exact["score"][0].severity)
        self.assertEqual("ok", miss_by_one["score"][0].severity)
        self.assertEqual("ok", miss_by_two["score"][0].severity)
        self.assertEqual("warn", miss_by_four["score"][0].severity)

    def test_volume_rows_combine_team_stats_into_match_total(self):
        audit = build_match_audit(
            team_a="A", team_b="B", goals_a=2, goals_b=1,
            primary_1x2={"home": 0.55, "draw": 0.25, "away": 0.20},
            mode_score=(2, 1), expected_score=(2.0, 1.1),
            team_a_stats={"corners": 6, "shots": 14, "shots_on_target": 5, "cards": 2, "possession": 55},
            team_b_stats={"corners": 4, "shots": 9, "shots_on_target": 3, "cards": 3, "possession": 45},
            predicted_volume={"corners": 11.0, "shots": 22.0, "shots_on_target": 8.0, "cards": 4.5, "possession": 50.0},
        )
        labels = {row.label for row in audit["volume"]}
        self.assertIn("Córners", labels)
        self.assertIn("Posesión", labels)
        corners = next(row for row in audit["volume"] if row.label == "Córners")
        self.assertEqual("10.0", corners.actual)
        self.assertEqual("11.0", corners.predicted)
        # Possession averages, not sums.
        possession = next(row for row in audit["volume"] if row.label == "Posesión")
        self.assertEqual("50.0", possession.actual)

    def test_records_carry_severity_for_ui_colouring(self):
        audit = build_match_audit(
            team_a="A", team_b="B", goals_a=1, goals_b=0,
            primary_1x2={"home": 0.6, "draw": 0.2, "away": 0.2},
            mode_score=(1, 0), expected_score=(1.2, 0.7),
            team_a_stats=None, team_b_stats=None,
        )
        records = audit_rows_to_records(audit["score"])
        for record in records:
            self.assertIn("_severity", record)
            self.assertIn(record["_severity"], SEVERITY_COLORS)


class PerTeamAuditTests(unittest.TestCase):
    def test_per_team_audit_compares_xg_and_volume_for_each_side(self):
        rows = build_per_team_audit(
            team_a="Korea Republic", team_b="Czechia",
            goals_a=2, goals_b=1,
            expected_xg=(1.67, 1.09),
            team_volume_predictions={
                "shots": {"Korea Republic": 13.5, "Czechia": 10.5},
                "shots_on_target": {"Korea Republic": 5.0, "Czechia": 4.0},
                "corners": {"Korea Republic": 5.0, "Czechia": 4.0},
                "cards": {"Korea Republic": 2.0, "Czechia": 2.5},
            },
            team_a_stats={
                "xg": 1.95, "shots": 17, "shots_on_target": 7,
                "corners": 6, "yellow_cards": 2, "red_cards": 0, "possession": 58,
            },
            team_b_stats={
                "xg": 0.81, "shots": 9, "shots_on_target": 3,
                "corners": 3, "yellow_cards": 3, "red_cards": 0, "possession": 42,
            },
        )
        labels = [row["label"] for row in rows]
        self.assertIn("xG", labels)
        self.assertIn("Goles", labels)
        self.assertIn("Tiros", labels)
        self.assertIn("Tarjetas", labels)
        self.assertIn("Posesión %", labels)

        xg_row = next(row for row in rows if row["label"] == "xG")
        self.assertEqual("1.67", xg_row["team_a"]["predicted"])
        self.assertEqual("1.95", xg_row["team_a"]["actual"])
        self.assertEqual("+0.28", xg_row["team_a"]["delta_label"])
        # Czechia under-performed xG → negative delta.
        self.assertTrue(xg_row["team_b"]["delta_label"].startswith("-"))

        cards_row = next(row for row in rows if row["label"] == "Tarjetas")
        self.assertEqual("2.00", cards_row["team_a"]["actual"])
        self.assertEqual("3.00", cards_row["team_b"]["actual"])

    def test_per_team_audit_skips_when_no_evidence_is_available(self):
        rows = build_per_team_audit(
            team_a="A", team_b="B", goals_a=0, goals_b=0,
            expected_xg=(), team_volume_predictions={},
            team_a_stats=None, team_b_stats=None,
        )
        # Without any data, the goals row at least uses the actual score; xG row
        # with no prediction and no stats is skipped.
        labels = [row["label"] for row in rows]
        self.assertNotIn("xG", labels)
        self.assertIn("Goles", labels)


if __name__ == "__main__":
    unittest.main()
