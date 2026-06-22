from pathlib import Path
import unittest

from wcpredict.sofascore_package import (
    load_sofascore_package,
    validate_package_identity,
)


class SofaScorePackageTests(unittest.TestCase):
    def test_valid_package_preserves_method_and_identity(self):
        package = load_sofascore_package(
            Path(__file__).parent / "fixtures" / "sofascore_package.json"
        )
        validate_package_identity(package, "Canada", "Qatar")
        self.assertEqual("dom", package.records[0].method)

    def test_identity_mismatch_is_rejected(self):
        package = load_sofascore_package(
            Path(__file__).parent / "fixtures" / "sofascore_package.json"
        )
        with self.assertRaises(ValueError):
            validate_package_identity(package, "Mexico", "Qatar")


if __name__ == "__main__":
    unittest.main()
