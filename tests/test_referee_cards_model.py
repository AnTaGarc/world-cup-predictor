import tempfile
import unittest
from pathlib import Path

from wcpredict.referee_cards_model import (
    blend_referee_rate,
    referee_card_multiplier_for_match,
)
from wcpredict.repository import Repository


class BlendTests(unittest.TestCase):
    def test_no_evidence_returns_neutral(self):
        multiplier = blend_referee_rate(
            internal_cards=0, internal_matches=0,
            upstream_avg=None, tournament_mean=4.0,
        )
        self.assertEqual(1.0, multiplier)

    def test_upstream_prior_only_is_heavily_shrunk(self):
        # Referee books 5.5/game upstream vs tournament mean 4.0 (+37%),
        # but with zero internal matches the multiplier stays close to 1.
        multiplier = blend_referee_rate(
            internal_cards=0, internal_matches=0,
            upstream_avg=5.5, tournament_mean=4.0,
        )
        self.assertGreater(multiplier, 1.0)
        self.assertLess(multiplier, 1.15)

    def test_internal_evidence_moves_multiplier_further(self):
        low = blend_referee_rate(0, 0, 5.5, 4.0)
        high = blend_referee_rate(33, 6, 5.5, 4.0)  # 5.5/game observed too
        self.assertGreater(high, low)

    def test_multiplier_is_clamped(self):
        extreme = blend_referee_rate(90, 6, 9.0, 3.0)
        self.assertLessEqual(extreme, 1.25)
        soft = blend_referee_rate(0, 6, 0.5, 5.0)
        self.assertGreaterEqual(soft, 0.80)


class MatchMultiplierTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        now = "2026-07-03T12:00:00+00:00"
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Spain', 'ESP')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'Portugal', 'POR')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status) VALUES(500, 'FIFA World Cup 2026', 'Round of 16', "
                "'2026-07-05T17:00:00+00:00', 1, 2, 'scheduled')"
            )
            con.execute(
                "INSERT INTO gh_referees(provider_id, external_referee_id, referee_name, "
                "country, avg_cards_per_game, imported_at_utc) "
                "VALUES('github_wc2026_referees', 13, 'Ma Ning', 'China', 5.5, ?)",
                (now,),
            )
            # Assigned to our match (match_id resolved), plus two completed
            # matches he refereed with cards in the timeline.
            con.execute(
                "INSERT INTO gh_matches(provider_id, external_match_id, match_id, "
                "referee_name, external_referee_id, imported_at_utc) "
                "VALUES('github_wc2026_matches', 93, 500, 'Ma Ning', 13, ?)",
                (now,),
            )
            for ext_id, cards, ref_name, ref_id in (
                (10, 6, "Ma Ning", 13), (11, 5, "Ma Ning", 13),
                (12, 2, "Calm Ref", 14), (13, 2, "Calm Ref", 14),
            ):
                con.execute(
                    "INSERT INTO gh_matches(provider_id, external_match_id, referee_name, "
                    "external_referee_id, home_score, away_score, imported_at_utc) "
                    "VALUES('github_wc2026_matches', ?, ?, ?, 1, 0, ?)",
                    (ext_id, ref_name, ref_id, now),
                )
                for i in range(cards if ref_name == "Ma Ning" else 2):
                    con.execute(
                        "INSERT INTO gh_match_events(provider_id, external_event_id, "
                        "external_match_id, minute, period, event_type, external_team_id, "
                        "external_player_id, provider_version, imported_at_utc) "
                        "VALUES('github_wc2026_events', ?, ?, 50, 'second_half', "
                        "'Yellow Card', 1, 0, 'v', ?)",
                        (ext_id * 100 + i, ext_id, now),
                    )

    def tearDown(self):
        self.directory.cleanup()

    def test_assigned_referee_produces_multiplier_above_one(self):
        multiplier, referee_name = referee_card_multiplier_for_match(self.repo, 500)
        self.assertEqual("Ma Ning", referee_name)
        self.assertGreater(multiplier, 1.0)
        self.assertLessEqual(multiplier, 1.25)

    def test_match_without_assigned_referee_is_neutral(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status) VALUES(501, 'FIFA World Cup 2026', 'Round of 16', "
                "'2026-07-05T20:00:00+00:00', 2, 1, 'scheduled')"
            )
        multiplier, referee_name = referee_card_multiplier_for_match(self.repo, 501)
        self.assertEqual(1.0, multiplier)
        self.assertIsNone(referee_name)
