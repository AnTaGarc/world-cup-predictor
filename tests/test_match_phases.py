import unittest

from wcpredict.match_phases import (
    MatchPhaseResultInput,
    ShootoutKickInput,
    summarize_shootout,
    validate_phase_result,
    validate_shootout_sequence,
)


class MatchPhaseTests(unittest.TestCase):
    def test_shootout_requires_level_score_after_extra_time(self):
        value = MatchPhaseResultInput(1, 1, 1, 0, 5, 4, "shootout")

        errors = validate_phase_result(value)

        self.assertTrue(any("empate al 120" in error for error in errors))

    def test_regulation_rejects_extra_time_and_shootout_values(self):
        value = MatchPhaseResultInput(2, 0, 0, 0, None, None, "regulation")

        errors = validate_phase_result(value)

        self.assertTrue(any("prórroga" in error for error in errors))

    def test_only_saved_credits_goalkeeper_but_every_kick_is_faced(self):
        kicks = (
            ShootoutKickInput(1, 1, 10, 20, "saved"),
            ShootoutKickInput(2, 2, 11, 21, "off_target_or_woodwork"),
            ShootoutKickInput(3, 1, 12, 20, "scored"),
        )

        summary = summarize_shootout(kicks)

        self.assertEqual(1, summary.goalkeeper_saves[20])
        self.assertEqual(2, summary.goalkeeper_faced[20])
        self.assertEqual(0, summary.goalkeeper_saves[21])
        self.assertEqual(1, summary.goalkeeper_faced[21])

    def test_shootout_stops_when_remaining_kicks_cannot_change_winner(self):
        kicks = tuple(
            ShootoutKickInput(index + 1, team, 100 + index, 200 + team, outcome)
            for index, (team, outcome) in enumerate(
                ((1, "scored"), (2, "saved"), (1, "scored"),
                 (2, "saved"), (1, "scored"), (2, "saved"))
            )
        )

        summary = validate_shootout_sequence(kicks)

        self.assertEqual(1, summary.winner_team_id)
        self.assertEqual((), summary.errors)


if __name__ == "__main__":
    unittest.main()
