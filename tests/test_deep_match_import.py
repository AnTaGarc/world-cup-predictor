import json
import tempfile
import unittest
from pathlib import Path

from wcpredict.deep_match_import import flatten_team_metrics, load_deep_match_file


class DeepMatchImportTests(unittest.TestCase):
    def test_loads_collection_and_flattens_attributable_numeric_leaves(self):
        payload = {
            "numero_de_partidos": 1,
            "partidos": [{
                "id": "chequia_sudafrica",
                "nombre": "Chequia vs Sudáfrica",
                "equipos": {"izquierda_verde": "Chequia", "derecha_azul": "Sudáfrica"},
                "estadisticas": {
                    "resumen_del_partido": {
                        "goles_esperados_xg": {"Chequia": 1.02, "Sudáfrica": 1.38},
                        "posesion_de_balon_pct": {"Chequia": 47, "Sudáfrica": 53},
                    },
                    "pases": {"pases_en_el_ultimo_tercio": {
                        "Chequia": {"completados": 40, "intentados": 72, "porcentaje": 56},
                        "Sudáfrica": {"completados": 51, "intentados": 80, "porcentaje": 64},
                    }},
                    "mapa_de_pases": {"descripcion": "sin atribución", "tercio_central": 50},
                },
                "fuentes": ["captura-1.png"],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            collection = load_deep_match_file(path)
        self.assertEqual("Czechia", collection.matches[0].team_a)
        self.assertEqual("South Africa", collection.matches[0].team_b)
        rows = flatten_team_metrics(collection.matches[0])
        by_key = {(row.team_name, row.metric): row.value for row in rows}
        self.assertEqual(1.02, by_key[("Czechia", "resumen_del_partido.goles_esperados_xg")])
        self.assertEqual(64, by_key[("South Africa", "pases.pases_en_el_ultimo_tercio.porcentaje")])
        self.assertNotIn(("Czechia", "mapa_de_pases.tercio_central"), by_key)

    def test_declared_match_count_must_match_payload(self):
        payload = {"numero_de_partidos": 2, "partidos": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "número de partidos"):
                load_deep_match_file(path)


if __name__ == "__main__":
    unittest.main()
