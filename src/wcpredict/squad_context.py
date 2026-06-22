from __future__ import annotations

from datetime import datetime, timezone
import unicodedata

from wcpredict.names import same_team


EVENT_LABELS = {
    "suspension_red": "sanción por roja",
    "suspension_yellows": "sanción por acumulación de amarillas",
    "injury": "lesión",
    "illness": "enfermedad",
    "coach_change": "cambio de entrenador",
}


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _person_key(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def apply_squad_context(
    players: list[dict], events: list[dict], kickoff_utc: datetime, match_id: int
) -> tuple[list[dict], list[str]]:
    adjusted = [dict(row) for row in players]
    notes: list[str] = []
    for event in events:
        start, end = _dt(event.get("starts_at_utc")), _dt(event.get("ends_at_utc"))
        affected = event.get("affected_match_id")
        if start and kickoff_utc < start or end and kickoff_utc > end:
            continue
        if affected is not None and int(affected) != int(match_id):
            continue
        label = EVENT_LABELS.get(str(event.get("event_type")), str(event.get("event_type")))
        player_name = event.get("player_name")
        if not player_name:
            notes.append(f"{event.get('team_name')}: {label}.")
            continue
        target = next((row for row in adjusted if same_team(str(row.get("team_name") or ""), str(event.get("team_name") or "")) and _person_key(row.get("player_name")) == _person_key(player_name)), None)
        if target is None:
            target = {"player_name": player_name, "team_name": event.get("team_name"), "minutes": 0}
            adjusted.append(target)
        target.update(availability="out", starter_probability=0.0, expected_minutes=0)
        notes.append(f"{player_name} ({event.get('team_name')}): {label}.")
    return adjusted, notes
