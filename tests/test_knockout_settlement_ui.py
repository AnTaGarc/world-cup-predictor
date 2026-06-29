import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.match_phases import MatchPhaseResultInput, ShootoutKickInput
from wcpredict.ui.knockout_settlement import (
    KnockoutSettlementDraft,
    build_settlement_sections,
    period_statuses,
    validate_settlement_draft,
)
from wcpredict.repository import Repository


class KnockoutSettlementUiTests(unittest.TestCase):
    def test_regulation_path_hides_extra_time_and_shootout(self):
        state = build_settlement_sections("regulation")

        self.assertEqual(
            ("first_half", "second_half", "regulation_total"),
            state.visible_periods,
        )
        self.assertFalse(state.show_extra_time_score)
        self.assertFalse(state.show_shootout)

    def test_shootout_path_exposes_all_periods_and_editor(self):
        state = build_settlement_sections("shootout")

        self.assertIn("extra_time_first", state.visible_periods)
        self.assertIn("full_match_total", state.visible_periods)
        self.assertTrue(state.show_extra_time_score)
        self.assertTrue(state.show_shootout)

    def test_optional_cumulative_periods_do_not_remain_pending(self):
        statuses = period_statuses(
            "regulation", {"first_half", "second_half"}, []
        )

        self.assertEqual("imported", statuses["first_half"])
        self.assertEqual("optional", statuses["regulation_total"])
        self.assertEqual("not_played", statuses["extra_time_first"])

    def test_shootout_requires_goalkeepers_and_valid_sequence(self):
        draft = KnockoutSettlementDraft(
            phase_result=MatchPhaseResultInput(1, 1, 0, 0, 1, 0, "shootout"),
            kicks=(ShootoutKickInput(1, 1, 10, 20, "scored"),),
            imported_periods=frozenset({"first_half", "second_half"}),
            goalkeeper_a_id=None,
            goalkeeper_b_id=None,
        )

        errors = validate_settlement_draft(draft)

        self.assertTrue(any("portero" in error.casefold() for error in errors))
        self.assertTrue(any("ganador" in error.casefold() or "selecciones" in error.casefold() for error in errors))

    def test_selectable_squad_players_receive_stable_database_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            team_id = repo.upsert_team("Spain")
            repo.replace_current_world_cup_players(
                "test",
                [
                    {"player_name": "Unai Simon", "team_name": "Spain", "position": "GK"},
                    {"player_name": "Mikel Oyarzabal", "team_name": "Spain", "position": "FW"},
                ],
                datetime.now(timezone.utc),
            )

            first = repo.list_selectable_squad_players(team_id, "Spain")
            second = repo.list_selectable_squad_players(team_id, "Spain")

        self.assertEqual(first, second)
        self.assertEqual({"Unai Simon", "Mikel Oyarzabal"}, {row["player_name"] for row in first})
        self.assertTrue(all(isinstance(row["player_id"], int) for row in first))


if __name__ == "__main__":
    unittest.main()
