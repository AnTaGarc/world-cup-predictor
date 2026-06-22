from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WeatherRequest:
    url: str
    params: dict[str, str | float]


def build_open_meteo_request(
    latitude: float, longitude: float, kickoff_utc: datetime
) -> WeatherRequest:
    day = kickoff_utc.date().isoformat()
    return WeatherRequest(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m",
            "start_date": day,
            "end_date": day,
            "timezone": "UTC",
        },
    )


def normalize_weather_hour(row: dict, source_id: str) -> dict:
    return {
        "observed_for_utc": row["time"],
        "temperature_c": row.get("temperature_2m"),
        "precipitation_mm": row.get("precipitation"),
        "wind_speed_kmh": row.get("wind_speed_10m"),
        "relative_humidity_pct": row.get("relative_humidity_2m"),
        "source_id": source_id,
    }
