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
    def test_regulation_path_only_exposes_required_90_minute_total(self):
        state = build_settlement_sections("regulation")

        self.assertEqual(("regulation_total",), state.visible_periods)
        self.assertFalse(state.show_extra_time_score)
        self.assertFalse(state.show_shootout)
        statuses = period_statuses("regulation", set(), [])
        self.assertEqual("pending", statuses["regulation_total"])
        self.assertEqual("not_played", statuses["first_half"])

    def test_shootout_path_exposes_atomic_periods_and_optional_120_total(self):
        state = build_settlement_sections("shootout")

        self.assertEqual(
            (
                "first_half", "second_half", "extra_time_first",
                "extra_time_second", "full_match_total",
            ),
            state.visible_periods,
        )
        self.assertTrue(state.show_extra_time_score)
        self.assertTrue(state.show_shootout)
        statuses = period_statuses("shootout", set(), [])
        self.assertEqual("optional", statuses["full_match_total"])
        self.assertEqual("not_played", statuses["regulation_total"])
        self.assertEqual("not_played", statuses["extra_time_total"])

    def test_hidden_cumulative_periods_do_not_affect_extra_time_statuses(self):
        statuses = period_statuses("extra_time", {"regulation_total", "extra_time_total"}, [])

        self.assertEqual("pending", statuses["first_half"])
        self.assertEqual("not_played", statuses["regulation_total"])
        self.assertEqual("not_played", statuses["extra_time_total"])

    def test_regulation_total_alone_is_enough_to_close_at_90_minutes(self):
        draft = KnockoutSettlementDraft(
            phase_result=MatchPhaseResultInput(2, 0, None, None, None, None, "regulation"),
            kicks=(),
            imported_periods=frozenset({"regulation_total"}),
            goalkeeper_a_id=None,
            goalkeeper_b_id=None,
        )

        self.assertEqual((), validate_settlement_draft(draft))
        statuses = period_statuses("regulation", {"regulation_total"}, [])
        self.assertEqual("imported", statuses["regulation_total"])
        self.assertEqual("not_played", statuses["first_half"])
        self.assertEqual("not_played", statuses["second_half"])

    def test_regulation_requires_total_even_when_both_halves_exist(self):
        draft = KnockoutSettlementDraft(
            phase_result=MatchPhaseResultInput(2, 0, None, None, None, None, "regulation"),
            kicks=(),
            imported_periods=frozenset({"first_half", "second_half"}),
            goalkeeper_a_id=None,
            goalkeeper_b_id=None,
        )

        errors = validate_settlement_draft(draft)

        self.assertTrue(any("acumulado" in error.casefold() for error in errors))

    def test_extra_time_totals_cannot_replace_four_atomic_periods(self):
        draft = KnockoutSettlementDraft(
            phase_result=MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
            kicks=(),
            imported_periods=frozenset({"regulation_total", "extra_time_total"}),
            goalkeeper_a_id=None,
            goalkeeper_b_id=None,
        )

        errors = validate_settlement_draft(draft)

        self.assertTrue(any("primera y segunda parte" in error.casefold() for error in errors))

    def test_extra_time_closes_with_four_atomic_periods_without_totals(self):
        draft = KnockoutSettlementDraft(
            phase_result=MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
            kicks=(),
            imported_periods=frozenset({
                "first_half", "second_half", "extra_time_first", "extra_time_second",
            }),
            goalkeeper_a_id=None,
            goalkeeper_b_id=None,
        )

        self.assertEqual((), validate_settlement_draft(draft))

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
