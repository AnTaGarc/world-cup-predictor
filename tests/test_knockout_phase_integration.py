import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wcpredict.deep_match_import import load_deep_match_file
from wcpredict.extra_time_model import adjust_extra_time_xg
from wcpredict.knockout_model import predict_knockout_match
from wcpredict.match_phases import MatchPhaseResultInput, ShootoutKickInput
from wcpredict.repository import Repository
from wcpredict.ui.bracket import bracket_result_display, render_bracket


class KnockoutPhaseIntegrationTests(unittest.TestCase):
    def test_atomic_stats_settlement_learning_and_bracket_flow(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            repo = Repository(root / "app.sqlite")
            repo.initialize()
            spain = repo.upsert_team("Spain")
            germany = repo.upsert_team("Germany")
            portugal = repo.upsert_team("Portugal")
            kickoff = datetime(2026, 7, 1, 18, tzinfo=timezone.utc)
            regulation_id = repo.upsert_match(
                "FIFA World Cup 2026", "Round of 32", kickoff,
                germany, portugal, "scheduled",
            )
            extra_time_id = repo.upsert_match(
                "FIFA World Cup 2026", "Round of 32", kickoff + timedelta(hours=4),
                spain, germany, "scheduled",
            )
            shootout_id = repo.upsert_match(
                "FIFA World Cup 2026", "Round of 16", kickoff + timedelta(days=3),
                spain, portugal, "scheduled",
            )

            repo.settle_knockout_match_versioned(
                regulation_id,
                MatchPhaseResultInput(2, 0, None, None, None, None, "regulation"),
                (), None, kickoff + timedelta(hours=3),
            )
            self._import_period(repo, root, extra_time_id, "regulation_total", 1.50, 1.20)
            self._import_period(repo, root, extra_time_id, "extra_time_total", 0.60, 0.05)
            repo.settle_knockout_match_versioned(
                extra_time_id,
                MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
                (), None, kickoff + timedelta(hours=7),
            )

            players = {
                (team_id, role, index): self._player(repo, f"{team_id}-{role}-{index}", team_id, role)
                for team_id in (spain, portugal)
                for role, count in (("FW", 3), ("GK", 1))
                for index in range(count)
            }
            kicks = []
            for index in range(3):
                kicks.append(ShootoutKickInput(
                    len(kicks) + 1, spain, players[(spain, "FW", index)],
                    players[(portugal, "GK", 0)], "scored",
                ))
                kicks.append(ShootoutKickInput(
                    len(kicks) + 1, portugal, players[(portugal, "FW", index)],
                    players[(spain, "GK", 0)], "off_target_or_woodwork",
                ))
            repo.settle_knockout_match_versioned(
                shootout_id,
                MatchPhaseResultInput(2, 2, 0, 0, 3, 0, "shootout"),
                tuple(kicks), None, kickoff + timedelta(days=3, hours=3),
            )

            with repo.session() as con:
                training = con.execute(
                    "SELECT goals_a, goals_b FROM historical_matches "
                    "WHERE source_id='reviewed_settlement' AND source_row_key=?",
                    (str(extra_time_id),),
                ).fetchone()
            self.assertEqual((1, 1), tuple(training))

            next_kickoff = kickoff + timedelta(days=7)
            rows = repo.list_extra_time_training_rows_before(next_kickoff)
            adjustment = adjust_extra_time_xg(
                "Spain", "Germany", 1.5, 1.2, rows, next_kickoff
            )
            base_ko = predict_knockout_match(1.5, 1.2)
            next_ko = predict_knockout_match(
                1.5, 1.2, extra_time_xg=adjustment.adjusted_xg
            )
            self.assertEqual(base_ko.home_wins_90, next_ko.home_wins_90)
            self.assertNotEqual(
                base_ko.cond_home_wins_et_given_draw_90,
                next_ko.cond_home_wins_et_given_draw_90,
            )
            evidence = repo.list_penalty_evidence(("Spain", "Portugal"), next_kickoff)
            self.assertEqual(6, sum(
                row["source_provider"] == "world_cup_2026_manual" for row in evidence
            ))

            display = bracket_result_display({
                "goals_a": 2,
                "goals_b": 2,
                "regulation_goals_a": 2,
                "regulation_goals_b": 2,
                "phase_extra_time_goals_a": 0,
                "phase_extra_time_goals_b": 0,
                "shootout_goals_a": 3,
                "shootout_goals_b": 0,
                "decided_in": "shootout",
            })
            html = render_bracket([{
                "match_id": "M89",
                "round": "round_of_16",
                "home": {"name": "Spain"},
                "away": {"name": "Portugal"},
                "status": "closed",
                **display,
            }])
            self.assertIn('class="bracket-team-score">2', html)
            self.assertIn('class="bracket-team-penalty-score">(3)</span>', html)

    @staticmethod
    def _player(repo: Repository, name: str, team_id: int, position: str) -> int:
        with repo.session() as con:
            con.execute(
                "INSERT INTO players(name, team_id, position) VALUES(?, ?, ?)",
                (name, team_id, position),
            )
            return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    @staticmethod
    def _import_period(
        repo: Repository,
        root: Path,
        match_id: int,
        period: str,
        xg_a: float,
        xg_b: float,
    ) -> None:
        payload = {
            "numero_de_partidos": 1,
            "partidos": [{
                "id": period,
                "nombre": "Spain vs Germany",
                "equipos": {"izquierda_verde": "Spain", "derecha_azul": "Germany"},
                "estadisticas": {"resumen_del_partido": {
                    "goles_esperados_xg": {"Spain": xg_a, "Germany": xg_b},
                }},
                "fuentes": [f"{period}.png"],
            }],
        }
        path = root / f"{period}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        repo.import_deep_match_period(
            load_deep_match_file(path),
            imported_at_utc=datetime(2026, 7, 2, tzinfo=timezone.utc),
            intended_match_id=match_id,
            period=period,
        )


if __name__ == "__main__":
    unittest.main()
