from pathlib import Path
import tempfile
import unittest

from wcpredict.entity_import import read_transfermarkt_directory
from wcpredict.repository import Repository


class EntityImportTests(unittest.TestCase):
    def test_player_maps_to_national_team_by_provider_id(self):
        fixture = Path(__file__).parent / "fixtures" / "transfermarkt"
        package = read_transfermarkt_directory(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            repo.import_transfermarkt_entities(package)
            mappings = repo.list_provider_entities("transfermarkt")
        self.assertEqual(
            "100",
            next(
                row for row in mappings if row["entity_type"] == "national_team"
            )["provider_id"],
        )
        self.assertEqual(
            "10",
            next(row for row in mappings if row["entity_type"] == "player")[
                "provider_id"
            ],
        )


if __name__ == "__main__":
    unittest.main()
