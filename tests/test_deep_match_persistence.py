import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.advanced_form import build_xg_form_adjustment, build_volume_rate_observations
from wcpredict.deep_match_import import load_deep_match_file
from wcpredict.repository import Repository


class DeepMatchPersistenceTests(unittest.TestCase):
    def test_import_is_idempotent_and_populates_primary_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Czechia")
            b = repo.upsert_team("South Africa")
            match_id = repo.upsert_match(
                "FIFA World Cup 2026", "Group", datetime(2026, 6, 18, 18, tzinfo=timezone.utc),
                a, b, "finished",
            )
            payload = {"numero_de_partidos": 1, "partidos": [{
                "id": "chequia_sudafrica", "nombre": "Chequia vs Sudáfrica",
                "equipos": {"izquierda_verde": "Chequia", "derecha_azul": "Sudáfrica"},
                "estadisticas": {"resumen_del_partido": {
                    "goles_esperados_xg": {"Chequia": 1.02, "Sudáfrica": 1.38},
                    "tiros_totales": {"Chequia": 14, "Sudáfrica": 17},
                    "tarjetas_rojas": {"Chequia": 0, "Sudáfrica": 1},
                }}, "fuentes": ["captura.png"],
            }]}
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            collection = load_deep_match_file(path)
            first = repo.import_deep_match_collection(collection, datetime.now(timezone.utc))
            second = repo.import_deep_match_collection(collection, datetime.now(timezone.utc))
            before = repo.list_deep_xg_rows_before(datetime(2026, 6, 18, 18, tzinfo=timezone.utc))
            after = repo.list_deep_xg_rows_before(datetime(2026, 6, 19, 18, tzinfo=timezone.utc))
            observations = repo.list_observations(match_id)
            evidence_status = repo.get_match_evidence_status(match_id)
            team_stats = repo.list_team_match_stats(match_id)
            with repo.session() as con:
                stats = con.execute(
                    "SELECT t.name, s.xg, s.shots, s.red_cards FROM team_match_stats s "
                    "JOIN teams t ON t.id=s.team_id WHERE s.match_id=? ORDER BY t.name", (match_id,),
                ).fetchall()
        self.assertEqual(1, first.imported_matches)
        self.assertEqual(1, second.unchanged_matches)
        self.assertEqual(6, len(observations))
        self.assertEqual([], before)
        self.assertEqual(1, len(after))
        self.assertEqual([("Czechia", 1.02, 14, 0), ("South Africa", 1.38, 17, 1)], [tuple(row) for row in stats])
        self.assertFalse(evidence_status["has_result"])
        self.assertTrue(evidence_status["has_team_statistics"])
        self.assertEqual(6, evidence_status["deep_observations"])
        self.assertEqual(2, evidence_status["team_stat_rows"])
        self.assertEqual(["Czechia", "South Africa"], [row["team_name"] for row in team_stats])
        self.assertEqual(1.02, team_stats[0]["xg"])

    def test_imported_deep_stats_flow_into_future_advanced_form_and_volume_rates(self):
        """End-to-end: imported JSON evidence must reach build_xg_form_adjustment
        and build_volume_rate_observations for matches that happen after it."""
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            czechia = repo.upsert_team("Czechia")
            south_africa = repo.upsert_team("South Africa")
            iceland = repo.upsert_team("Iceland")
            # Past match: Czechia dominates South Africa with strong volume.
            past_kickoff = datetime(2026, 6, 18, 18, tzinfo=timezone.utc)
            repo.upsert_match(
                "FIFA World Cup 2026", "Group", past_kickoff,
                czechia, south_africa, "finished",
            )
            # Future match: Czechia plays a different opponent.
            future_kickoff = datetime(2026, 6, 25, 18, tzinfo=timezone.utc)
            repo.upsert_match(
                "FIFA World Cup 2026", "Group", future_kickoff,
                czechia, iceland, "scheduled",
            )
            payload = {"numero_de_partidos": 1, "partidos": [{
                "id": "chequia_sudafrica", "nombre": "Chequia vs Sudáfrica",
                "equipos": {"izquierda_verde": "Chequia", "derecha_azul": "Sudáfrica"},
                "estadisticas": {"resumen_del_partido": {
                    "goles_esperados_xg": {"Chequia": 2.45, "Sudáfrica": 0.32},
                    "tiros_totales": {"Chequia": 22, "Sudáfrica": 5},
                    "posesion_de_balon_pct": {"Chequia": 70, "Sudáfrica": 30},
                    "saques_de_esquina": {"Chequia": 8, "Sudáfrica": 1},
                    "tarjetas_amarillas": {"Chequia": 1, "Sudáfrica": 3},
                }, "tiros": {"tiros_a_puerta": {"Chequia": 9, "Sudáfrica": 1}}},
                "fuentes": ["captura.png"],
            }]}
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            repo.import_deep_match_collection(
                load_deep_match_file(path), datetime.now(timezone.utc)
            )

            xg_rows = repo.list_deep_xg_rows_before(future_kickoff)
            volume_rows = repo.list_deep_volume_rows_before(future_kickoff)

            adjustment = build_xg_form_adjustment(
                "Czechia", "Iceland", xg_rows, future_kickoff,
            )
            volume = build_volume_rate_observations("Czechia", "Iceland", volume_rows)

        # The future-match xG adjustment must reflect Czechia's prior dominance.
        self.assertGreater(adjustment.sample_a, 0)
        self.assertGreater(adjustment.factor_a, 1.0)
        # Volume rates must surface Czechia's corner/shot baseline for downstream
        # estimate_total_market calls.
        keys = {(row["subject_name"], row["metric"]) for row in volume}
        self.assertIn(("Czechia", "corners_for_avg"), keys)
        self.assertIn(("Czechia", "shots_for_avg"), keys)

    def test_goalkeeper_paradas_flow_into_team_match_stats_and_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            spain = repo.upsert_team("Spain")
            czechia = repo.upsert_team("Czechia")
            iceland = repo.upsert_team("Iceland")
            past_kickoff = datetime(2026, 6, 18, 18, tzinfo=timezone.utc)
            repo.upsert_match(
                "FIFA World Cup 2026", "Group", past_kickoff, spain, czechia, "finished",
            )
            future_kickoff = datetime(2026, 6, 25, 18, tzinfo=timezone.utc)
            repo.upsert_match(
                "FIFA World Cup 2026", "Group", future_kickoff, spain, iceland, "scheduled",
            )
            payload = {"numero_de_partidos": 1, "partidos": [{
                "id": "spain_czechia", "nombre": "España vs Chequia",
                "equipos": {"izquierda_verde": "Spain", "derecha_azul": "Czechia"},
                "estadisticas": {
                    "resumen_del_partido": {
                        "goles_esperados_xg": {"Spain": 2.10, "Czechia": 0.65},
                        "tiros_totales": {"Spain": 17, "Czechia": 6},
                    },
                    "tiros": {"tiros_a_puerta": {"Spain": 6, "Czechia": 3}},
                    "porteria": {"paradas": {"Spain": 3, "Czechia": 4}},
                },
                "fuentes": ["captura.png"],
            }]}
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            repo.import_deep_match_collection(
                load_deep_match_file(path), datetime.now(timezone.utc),
            )

            with repo.session() as con:
                row = con.execute(
                    "SELECT t.name, s.saves FROM team_match_stats s "
                    "JOIN teams t ON t.id=s.team_id ORDER BY t.name"
                ).fetchall()

            from wcpredict.advanced_form import build_goalkeeper_baseline
            gk_rows = repo.list_deep_goalkeeper_rows_before(future_kickoff)
            baseline = build_goalkeeper_baseline("Spain", gk_rows, future_kickoff)

        self.assertEqual([("Czechia", 4), ("Spain", 3)], [(r["name"], r["saves"]) for r in row])
        self.assertEqual(1, baseline.sample_matches)
        # Spain made 3 saves vs Czechia's 3 SOT → save_rate = 1.0.
        self.assertAlmostEqual(1.0, baseline.save_rate, places=3)

    def test_deep_xg_rows_attach_extended_observations_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            spain = repo.upsert_team("Spain")
            czechia = repo.upsert_team("Czechia")
            past_kickoff = datetime(2026, 6, 18, 18, tzinfo=timezone.utc)
            repo.upsert_match(
                "FIFA World Cup 2026", "Group", past_kickoff, spain, czechia, "finished",
            )
            payload = {"numero_de_partidos": 1, "partidos": [{
                "id": "spain_czechia", "nombre": "España vs Chequia",
                "equipos": {"izquierda_verde": "Spain", "derecha_azul": "Czechia"},
                "estadisticas": {
                    "resumen_del_partido": {
                        "goles_esperados_xg": {"Spain": 2.10, "Czechia": 0.65},
                        "tiros_totales": {"Spain": 17, "Czechia": 6},
                    },
                    "tiros": {"tiros_a_puerta": {"Spain": 7, "Czechia": 2}},
                    "ataque": {
                        "ocasiones_claras_realizadas": {"Spain": 6, "Czechia": 1},
                        "toques_dentro_del_area": {"Spain": 38, "Czechia": 12},
                    },
                    "porteria": {
                        "goles_evitados": {"Spain": 1.2, "Czechia": -0.4},
                        "paradas": {"Spain": 2, "Czechia": 5},
                    },
                    "defensa": {
                        "errores_que_llevan_a_disparo": {"Spain": 0, "Czechia": 2},
                    },
                },
                "fuentes": ["captura.png"],
            }]}
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            repo.import_deep_match_collection(load_deep_match_file(path), datetime.now(timezone.utc))
            rows = repo.list_deep_xg_rows_before(datetime(2026, 6, 25, tzinfo=timezone.utc))
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(6, row["clear_chances_a"])
        self.assertEqual(1, row["clear_chances_b"])
        self.assertAlmostEqual(1.2, row["goals_prevented_a"])
        self.assertEqual(38, row["box_touches_a"])
        self.assertEqual(2, row["errors_to_shot_b"])

    def test_ambiguous_schedule_pair_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "app.sqlite")
            repo.initialize()
            a = repo.upsert_team("Canada")
            b = repo.upsert_team("Qatar")
            for day in (18, 19):
                repo.upsert_match("FIFA World Cup 2026", "Group", datetime(2026, 6, day, 18, tzinfo=timezone.utc), a, b, "finished")
            payload = {"numero_de_partidos": 1, "partidos": [{
                "id": "canada_qatar", "nombre": "Canadá vs Qatar",
                "equipos": {"izquierda_verde": "Canadá", "derecha_azul": "Qatar"},
                "estadisticas": {"resumen_del_partido": {"tiros_totales": {"Canadá": 10, "Qatar": 2}}},
                "fuentes": [],
            }]}
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = repo.import_deep_match_collection(load_deep_match_file(path), datetime.now(timezone.utc))
        self.assertEqual(1, result.ambiguous_matches)


if __name__ == "__main__":
    unittest.main()
