from __future__ import annotations


def parse_shootout_shots(payload: dict) -> list[dict]:
    """Flatten ESPN's per-team shootout arrays into chronological kick rows."""
    shots: list[dict] = []
    for team in payload.get("shootout") or []:
        team_name = str(team.get("team") or "")
        for shot in team.get("shots") or []:
            shots.append({
                "event_id": str(shot.get("id") or ""),
                "team_name": team_name,
                "player_name": str(shot.get("player") or ""),
                "goalkeeper_name": "",
                "outcome": "scored" if shot.get("didScore") is True else "missed",
            })
    shots.sort(key=lambda row: int(row["event_id"]))
    for sequence_number, row in enumerate(shots, start=1):
        row["sequence_number"] = sequence_number
    return shots
