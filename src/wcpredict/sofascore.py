from dataclasses import dataclass
from typing import Callable
import re

import requests


class SofaScoreBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SofaScoreImport:
    event_id: int
    team_a: str
    team_b: str
    event_status: str
    statistics: list[dict]
    players: list[dict]
    status: str
    missing: list[str]
    source_url: str


def extract_event_id(url: str) -> int:
    if "sofascore.com" not in url.casefold():
        raise ValueError("La URL debe pertenecer a sofascore.com")
    match = re.search(r"(?:#id:|/event/)(\d+)(?:\D|$)", url)
    if not match:
        raise ValueError("No se encontro el identificador del evento en la URL")
    return int(match.group(1))


def fetch_sofascore_json(path: str) -> dict:
    response = requests.get(
        f"https://www.sofascore.com{path}",
        headers={"Accept": "application/json", "User-Agent": "WorldCupPredictor/1.0"},
        timeout=15,
    )
    if response.status_code == 403:
        raise SofaScoreBlockedError("blocked_by_provider")
    response.raise_for_status()
    return response.json()


def _statistics(payload: dict) -> list[dict]:
    rows = []
    for period in payload.get("statistics", []):
        for group in period.get("groups", []):
            for item in group.get("statisticsItems", []):
                rows.append(
                    {
                        "period": period.get("period"),
                        "group": group.get("groupName"),
                        "metric": item.get("name"),
                        "team_a_value": item.get("home"),
                        "team_b_value": item.get("away"),
                    }
                )
    return rows


def _players(payload: dict) -> list[dict]:
    rows = []
    for side in ("home", "away"):
        for item in (payload.get(side) or {}).get("players", []):
            player = item.get("player") or {}
            rows.append(
                {
                    "side": side,
                    "player_name": player.get("name"),
                    "player_id": player.get("id"),
                    "position": item.get("position"),
                    "starter": not bool(item.get("substitute")),
                    "statistics": item.get("statistics") or {},
                }
            )
    return rows


def import_sofascore_event(
    url: str,
    fetcher: Callable[[str], dict] = fetch_sofascore_json,
) -> SofaScoreImport:
    event_id = extract_event_id(url)
    event_payload = fetcher(f"/api/v1/event/{event_id}")
    event = event_payload.get("event") or event_payload
    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    if not home.get("name") or not away.get("name"):
        raise ValueError("SofaScore no devolvio los equipos del evento")

    missing = []
    try:
        statistics = _statistics(fetcher(f"/api/v1/event/{event_id}/statistics"))
    except (requests.RequestException, RuntimeError, ValueError, KeyError):
        statistics = []
        missing.append("statistics")
    try:
        players = _players(fetcher(f"/api/v1/event/{event_id}/lineups"))
    except (requests.RequestException, RuntimeError, ValueError, KeyError):
        players = []
        missing.append("lineups")
    status_value = event.get("status") or {}
    event_status = str(status_value.get("type") if isinstance(status_value, dict) else status_value)
    return SofaScoreImport(
        event_id=event_id,
        team_a=str(home["name"]),
        team_b=str(away["name"]),
        event_status=event_status,
        statistics=statistics,
        players=players,
        status="incomplete" if missing else "complete",
        missing=missing,
        source_url=url,
    )
