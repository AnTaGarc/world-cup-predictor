import unittest
from unittest.mock import patch

from wcpredict.sofascore import (
    SofaScoreBlockedError,
    extract_event_id,
    fetch_sofascore_json,
    import_sofascore_event,
)


class SofaScoreTests(unittest.TestCase):
    @patch("wcpredict.sofascore.requests.get")
    def test_direct_http_403_is_classified_as_blocked(self, get):
        get.return_value.status_code = 403
        with self.assertRaises(SofaScoreBlockedError):
            fetch_sofascore_json("/api/v1/event/15186731")

    def test_extracts_event_id_from_supported_url(self):
        self.assertEqual(123456, extract_event_id("https://www.sofascore.com/football/match/a-b/xyz#id:123456"))
        self.assertEqual(987, extract_event_id("https://www.sofascore.com/event/987"))

    def test_imports_event_statistics_and_lineups_with_injected_fetcher(self):
        payloads = {
            "/api/v1/event/123": {"event": {"id": 123, "homeTeam": {"name": "Canada"}, "awayTeam": {"name": "Qatar"}, "status": {"type": "notstarted"}}},
            "/api/v1/event/123/statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"name": "Corner kicks", "home": "6", "away": "3"}]}]}]},
            "/api/v1/event/123/lineups": {"home": {"players": [{"player": {"name": "A Player"}, "position": "F"}]}, "away": {"players": []}},
        }
        result = import_sofascore_event("https://www.sofascore.com/x#id:123", payloads.__getitem__)
        self.assertEqual("complete", result.status)
        self.assertEqual("Canada", result.team_a)
        self.assertEqual(1, len(result.statistics))
        self.assertEqual("A Player", result.players[0]["player_name"])

    def test_missing_optional_endpoint_returns_incomplete_not_exception(self):
        def fetch(path):
            if path.endswith("/123"):
                return {"event": {"id": 123, "homeTeam": {"name": "Canada"}, "awayTeam": {"name": "Qatar"}, "status": {"type": "notstarted"}}}
            raise RuntimeError("blocked")

        result = import_sofascore_event("https://www.sofascore.com/x#id:123", fetch)
        self.assertEqual("incomplete", result.status)
        self.assertIn("statistics", result.missing)
        self.assertIn("lineups", result.missing)


if __name__ == "__main__":
    unittest.main()
