from datetime import datetime, timezone
import unittest

from wcpredict.weather import build_open_meteo_request, normalize_weather_hour


class WeatherTests(unittest.TestCase):
    def test_request_uses_coordinates_and_needs_no_key(self):
        request = build_open_meteo_request(
            43.6532,
            -79.3832,
            datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
        )
        self.assertEqual("https://api.open-meteo.com/v1/forecast", request.url)
        self.assertNotIn("apikey", request.params)
        self.assertEqual("UTC", request.params["timezone"])

    def test_hour_is_normalized_with_source_context(self):
        row = normalize_weather_hour(
            {
                "time": "2026-06-18T22:00",
                "temperature_2m": 24.1,
                "precipitation": 0.0,
                "wind_speed_10m": 12.0,
            },
            source_id="open-meteo-43.6532--79.3832-2026-06-18",
        )
        self.assertEqual(24.1, row["temperature_c"])
        self.assertEqual(
            "open-meteo-43.6532--79.3832-2026-06-18", row["source_id"]
        )


if __name__ == "__main__":
    unittest.main()
