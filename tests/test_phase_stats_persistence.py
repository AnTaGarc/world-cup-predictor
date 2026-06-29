import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.deep_match_import import load_deep_match_file
from wcpredict.repository import Repository


class PhaseStatsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repo = Repository(self.root / "app.sqlite")
        self.repo.initialize()
        spain = self.repo.upsert_team("Spain")
        germany = self.repo.upsert_team("Germany")
        self.match_id = self.repo.upsert_match(
            "FIFA World Cup 2026",
            "Round of 32",
            datetime(2026, 6, 30, 18, tzinfo=timezone.utc),
            spain,
            germany,
            "scheduled",
        )
        self.now = datetime(2026, 6, 30, 21, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def _collection(self, name: str, spain: dict, germany: dict):
        def statistics_for(team_values):
            return {
                "goles_esperados_xg": team_values.get("xg"),
                "tiros_totales": team_values.get("shots"),
                "saques_de_esquina": team_values.get("corners"),
                "posesion_de_balon_pct": team_values.get("possession"),
            }

        stats_a = statistics_for(spain)
        stats_b = statistics_for(germany)
        metrics = {
            metric: {"Spain": stats_a[metric], "Germany": stats_b[metric]}
            for metric in stats_a
            if stats_a[metric] is not None and stats_b[metric] is not None
        }
        payload = {
            "numero_de_partidos": 1,
            "partidos": [{
                "id": name,
                "nombre": "Spain vs Germany",
                "equipos": {"izquierda_verde": "Spain", "derecha_azul": "Germany"},
                "estadisticas": {"resumen_del_partido": metrics},
                "fuentes": [f"{name}.png"],
            }],
        }
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_deep_match_file(path)

    def _import(self, period: str, name: str, spain: dict, germany: dict):
        return self.repo.import_deep_match_period(
            self._collection(name, spain, germany),
            imported_at_utc=self.now,
            intended_match_id=self.match_id,
            period=period,
        )

    def test_halves_project_once_to_regulation_stats(self):
        self._import("first_half", "first", {"xg": 0.72, "shots": 8, "corners": 3}, {"xg": 0.40, "shots": 5, "corners": 2})
        self._import("second_half", "second", {"xg": 1.00, "shots": 10, "corners": 4}, {"xg": 0.55, "shots": 6, "corners": 1})

        self.repo.project_regulation_stats(self.match_id, self.now)

        stats = {row["team_name"]: row for row in self.repo.list_team_match_stats(self.match_id)}
        self.assertEqual(18, stats["Spain"]["shots"])
        self.assertAlmostEqual(1.72, stats["Spain"]["xg"])
        self.assertEqual(11, stats["Germany"]["shots"])

    def test_extra_time_stats_never_project_to_team_match_stats(self):
        self._import("extra_time_first", "et1", {"xg": 0.25, "shots": 3}, {"xg": 0.12, "shots": 1})

        self.repo.project_regulation_stats(self.match_id, self.now)

        self.assertEqual([], self.repo.list_team_match_stats(self.match_id))

    def test_regulation_total_mismatch_reports_metric_and_team(self):
        self._import("first_half", "first", {"xg": 0.70, "shots": 8}, {"xg": 0.40, "shots": 5})
        self._import("second_half", "second", {"xg": 1.00, "shots": 10}, {"xg": 0.50, "shots": 6})
        self._import("regulation_total", "total", {"xg": 1.70, "shots": 19}, {"xg": 0.90, "shots": 11})

        issues = self.repo.validate_match_period_stats(self.match_id)

        self.assertIn(("Spain", "shots", 18.0, 19.0), [issue.comparison for issue in issues])

    def test_same_period_file_is_idempotent_and_period_is_queryable(self):
        collection = self._collection("first", {"xg": 0.72, "shots": 8}, {"xg": 0.40, "shots": 5})
        first = self.repo.import_deep_match_period(
            collection, imported_at_utc=self.now, intended_match_id=self.match_id, period="first_half"
        )
        second = self.repo.import_deep_match_period(
            collection, imported_at_utc=self.now, intended_match_id=self.match_id, period="first_half"
        )

        rows = self.repo.list_team_match_period_stats(self.match_id)
        observations = self.repo.list_observations(self.match_id)

        self.assertEqual(1, first.imported_matches)
        self.assertEqual(1, second.unchanged_matches)
        self.assertEqual({"first_half"}, {row["period"] for row in rows})
        self.assertEqual({"first_half"}, {row["period"] for row in observations})


if __name__ == "__main__":
    unittest.main()
