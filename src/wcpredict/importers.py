from dataclasses import dataclass
from pathlib import Path
import json
import re


@dataclass(frozen=True)
class ImportedProviderData:
    matches: list[dict]
    team_stats: list[dict]
    player_stats: list[dict]
    sources: list[dict]


def import_provider_export(path: Path) -> ImportedProviderData:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ImportedProviderData(
        matches=list(payload.get("events", [])),
        team_stats=list(payload.get("team_stats", [])),
        player_stats=list(payload.get("player_stats", [])),
        sources=list(payload.get("sources", [])),
    )


def parse_sofascore_html(html: str) -> dict[str, str]:
    match = re.search(
        r'<script[^>]+id="__SOFASCORE_STATE__"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("SofaScore structured state not found")
    payload = json.loads(match.group(1))
    event = payload.get("event") or {}
    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    if not home.get("name") or not away.get("name"):
        raise ValueError("SofaScore team names not found")
    return {
        "team_a": home["name"],
        "team_b": away["name"],
        "status": str(event.get("status", "")),
    }
