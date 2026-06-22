import unittest

from wcpredict.provider_health import (
    FailureKind,
    classify_provider_failure,
    credential_matrix,
)


class ProviderHealthTests(unittest.TestCase):
    def test_classifies_known_failures(self):
        self.assertEqual(
            FailureKind.BLOCKED, classify_provider_failure("HTTP 403 Forbidden")
        )
        self.assertEqual(
            FailureKind.UNSUPPORTED, classify_provider_failure("HTTP 422")
        )
        self.assertEqual(
            FailureKind.QUOTA, classify_provider_failure("rate limit exceeded")
        )
        self.assertEqual(
            FailureKind.SCHEMA,
            classify_provider_failure("statistics result must be a list"),
        )

    def test_matrix_reports_presence_not_secret_value(self):
        matrix = credential_matrix(
            {"API_SPORTS_KEY": "secret", "THE_ODDS_API_KEY": ""}
        )
        self.assertTrue(matrix["api_sports_football"]["configured"])
        self.assertFalse(matrix["the_odds_api"]["configured"])
        self.assertNotIn("secret", repr(matrix))


if __name__ == "__main__":
    unittest.main()
