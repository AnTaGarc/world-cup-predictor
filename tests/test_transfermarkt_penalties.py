from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wcpredict.repository import Repository
from wcpredict.transfermarkt_penalties import (
    eligible_penalty_teams,
    parse_penalty_attempts,
    player_targets_for_teams,
    slugify_player_name,
)


class TransfermarktPenaltyTests(unittest.TestCase):
    def test_slugify_player_name_for_transfermarkt_url(self):
        self.assertEqual("harry-kane", slugify_player_name("Harry Kane"))
        self.assertEqual("matej-kovar", slugify_player_name("Matej Kovar"))

    def test_parse_penalty_attempts_from_cached_html(self):
        html = """
        <html><body>
          <table>
            <tr><th>Date</th><th>Competition</th><th>Minute</th><th>Result</th><th>Goalkeeper</th></tr>
            <tr><td>Jun 20, 2026</td><td>World Cup</td><td>54'</td><td>Scored</td><td>Keeper One</td></tr>
            <tr><td>Jun 24, 2026</td><td>World Cup</td><td>89'</td><td>Missed</td><td>Keeper Two</td></tr>
          </table>
        </body></html>
        """
        attempts = parse_penalty_attempts(
            html,
            player_name="Harry Kane",
            team_name="England",
            transfermarkt_player_id="132098",
            source_url="https://www.transfermarkt.com/harry-kane/elfmetertore/spieler/132098",
            fetched_at_utc=datetime(2026, 6, 25, tzinfo=timezone.utc),
        )
        self.assertEqual(["scored", "missed"], [row["outcome"] for row in attempts])
        self.assertEqual("2026-06-20", attempts[0]["attempted_on"])
        self.assertEqual("World Cup", attempts[0]["competition"])
        self.assertEqual("Keeper One", attempts[0]["goalkeeper_name"])

    def test_repository_saves_penalty_attempts_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            row = {
                "player_name": "Harry Kane",
                "team_name": "England",
                "transfermarkt_player_id": "132098",
                "attempted_on": "2026-06-20",
                "competition": "World Cup",
                "phase": "regular",
                "outcome": "scored",
                "goalkeeper_name": "Keeper One",
                "opponent_team": "USA",
                "minute": "54'",
                "match_label": "England vs USA",
                "source_provider": "transfermarkt",
                "source_url": "https://example.test",
                "source_row_key": "transfermarkt:132098:2026-06-20:1",
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                "raw": {"cells": ["x"]},
            }
            self.assertEqual(1, repo.save_penalty_attempts([row]))
            self.assertEqual(1, repo.save_penalty_attempts([row]))
            stored = repo.list_penalty_attempts("England", "Harry Kane")
        self.assertEqual(1, len(stored))
        self.assertEqual("scored", stored[0]["outcome"])

    def test_targets_use_current_players_for_closed_group_qualifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Mexico")
            b = repo.upsert_team("South Africa")
            c = repo.upsert_team("Spain")
            d = repo.upsert_team("Korea Republic")
            kickoff = datetime(2026, 6, 10, tzinfo=timezone.utc)
            pairs = [
                (a, b, 1, 0), (a, c, 2, 0), (a, d, 2, 1),
                (b, c, 1, 0), (b, d, 2, 0), (c, d, 1, 0),
            ]
            for idx, (home, away, hg, ag) in enumerate(pairs):
                match_id = repo.upsert_match(
                    "FIFA World Cup 2026",
                    "Group stage - Group A",
                    kickoff.replace(day=10 + idx),
                    home,
                    away,
                    "finished",
                )
                with repo.session() as con:
                    con.execute(
                        "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                        "VALUES(?, ?, ?, 'test', ?)",
                        (match_id, hg, ag, kickoff.isoformat()),
                    )
            repo.replace_current_world_cup_players(
                "test",
                [
                    {"player_name": "Player Mexico", "team_name": "Mexico", "position": "FW", "minutes": 180},
                    {"player_name": "Player SA", "team_name": "South Africa", "position": "FW", "minutes": 180},
                    {"player_name": "Player Spain", "team_name": "Spain", "position": "FW", "minutes": 180},
                ],
                datetime.now(timezone.utc),
            )
            teams = eligible_penalty_teams(repo)
            targets = player_targets_for_teams(repo, teams)
        self.assertIn("Mexico", teams)
        self.assertIn("South Africa", teams)
        self.assertNotIn("Spain", {target.team_name for target in targets})
        self.assertEqual({"Player Mexico", "Player SA"}, {target.player_name for target in targets})


if __name__ == "__main__":
    unittest.main()
