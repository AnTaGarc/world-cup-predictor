from __future__ import annotations

import csv
from hashlib import sha256
from io import StringIO
import re
from typing import Any
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile

from wcpredict.daily_refresh import DatasetDownload


def dataset_sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _rows(csv_text: str) -> list[dict[str, str | None]]:
    reader = csv.DictReader(StringIO(csv_text.lstrip("\ufeff")))
    return [
        {_key(str(name)): (value.strip() if value is not None and value.strip() else None) for name, value in row.items()}
        for row in reader
    ]


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    return None


def _number(row: dict[str, Any], *names: str, integer: bool = False) -> float | int | None:
    value = _first(row, *names)
    if value is None:
        return None
    normalized = str(value).replace("%", "").replace(",", ".")
    try:
        number = float(normalized)
    except ValueError:
        return None
    return int(number) if integer else number


def parse_player_rows(csv_text: str) -> list[dict[str, Any]]:
    output = []
    for row in _rows(csv_text):
        output.append(
            {
                "player_name": _first(row, "player", "player_name", "name"),
                "team_name": _first(row, "team", "country", "squad"),
                "position": _first(row, "position", "pos"),
                "games": _number(row, "games", "matches", integer=True),
                "starts": _number(row, "starts", "games_starts", integer=True),
                "minutes": _number(row, "minutes", "min", integer=True),
                "goals": _number(row, "goals", integer=True),
                "assists": _number(row, "assists", integer=True),
                "shots": _number(row, "shots", integer=True),
                "shots_on_target": _number(row, "shots_on_target", "sot", integer=True),
                "passes": _number(row, "passes", "passes_completed", integer=True),
                "yellow_cards": _number(row, "yellow_cards", "cards_yellow", integer=True),
                "red_cards": _number(row, "red_cards", "cards_red", integer=True),
                "tackles_won": _number(row, "tackles_won", integer=True),
                "interceptions": _number(row, "interceptions", integer=True),
                "save_percentage": _number(row, "save", "save_percentage", "save_pct", "gk_save_pct"),
            }
        )
    return output


def parse_match_rows(csv_text: str) -> list[dict[str, Any]]:
    output = []
    for row in _rows(csv_text):
        played_at = _first(row, "date", "played_at", "match_date")
        kickoff_time = _first(row, "time", "kickoff_time", "kickoff", "utc_time")
        kickoff_utc = _fixture_datetime(played_at, kickoff_time)
        output.append(
            {
                "played_at": played_at,
                "kickoff_utc": kickoff_utc,
                "team_a": _first(row, "home_team", "team_a", "home"),
                "team_b": _first(row, "away_team", "team_b", "away"),
                "stage": _first(row, "stage", "round", "group", "tournament") or "FIFA World Cup 2026",
                "status": _first(row, "status", "match_status"),
                "goals_a": _number(row, "home_score", "score_a", "home_goals", integer=True),
                "goals_b": _number(row, "away_score", "score_b", "away_goals", integer=True),
                "xg_a": _number(row, "home_xg", "xg_a"),
                "xg_b": _number(row, "away_xg", "xg_b"),
                "possession_a": _number(row, "home_possession", "possession_a"),
                "possession_b": _number(row, "away_possession", "possession_b"),
                "corners_a": _number(row, "home_corners", "corners_a", integer=True),
                "corners_b": _number(row, "away_corners", "corners_b", integer=True),
                "shots_on_target_a": _number(row, "home_sot", "shots_on_target_a", integer=True),
                "shots_on_target_b": _number(row, "away_sot", "shots_on_target_b", integer=True),
                "shots_a": _number(row, "home_total_shots", "shots_a", integer=True),
                "shots_b": _number(row, "away_total_shots", "shots_b", integer=True),
                "yellow_cards_a": _number(row, "home_cards_yellow", "yellow_cards_a", integer=True),
                "yellow_cards_b": _number(row, "away_cards_yellow", "yellow_cards_b", integer=True),
                "red_cards_a": _number(row, "home_cards_red", "red_cards_a", integer=True),
                "red_cards_b": _number(row, "away_cards_red", "red_cards_b", integer=True),
                "referee": _first(row, "referee"),
                "venue": _first(row, "venue", "city"),
            }
        )
    return output


def parse_world_cup_schedule_rows(csv_text: str) -> list[dict[str, Any]]:
    source_rows = _rows(csv_text)
    filtered = [
        row for row in source_rows
        if str(_first(row, "tournament") or "").casefold() == "fifa world cup"
        and str(_first(row, "date", "played_at", "match_date") or "").startswith("2026-")
    ]
    if not filtered:
        return []
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(filtered[0].keys()))
    writer.writeheader()
    writer.writerows(filtered)
    return parse_match_rows(buffer.getvalue())


def _fixture_datetime(date_value: str | None, time_value: str | None) -> str | None:
    if not date_value:
        return None
    raw = str(date_value).strip()
    if "T" not in raw and time_value:
        raw = f"{raw}T{str(time_value).strip()}"
    elif "T" not in raw:
        raw = f"{raw}T12:00:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_team_rows(csv_text: str) -> list[dict[str, Any]]:
    output = []
    for row in _rows(csv_text):
        output.append(
            {
                "team_name": _first(row, "team", "squad", "country"),
                "played": _number(row, "played", "matches", "games", integer=True),
                "goals_for": _number(row, "goals_for", "goals", "gf", integer=True),
                "goals_against": _number(row, "goals_against", "ga", integer=True),
                "shots": _number(row, "shots", integer=True),
                "corners": _number(row, "corners", integer=True),
                "yellow_cards": _number(row, "yellow_cards", "cards_yellow", integer=True),
                "red_cards": _number(row, "red_cards", "cards_red", integer=True),
            }
        )
    return output


def import_world_cup_download(repository, download: DatasetDownload, imported_at_utc: datetime) -> None:
    text = download.content.decode("utf-8-sig")
    if download.provider_id == "swaptr_wc2026_players":
        repository.replace_current_world_cup_players(download.provider_id, parse_player_rows(text), imported_at_utc)
    elif download.provider_id == "swaptr_wc2026_teams":
        repository.replace_current_world_cup_teams(download.provider_id, parse_team_rows(text), imported_at_utc)
    elif download.provider_id == "swaptr_wc2026_matches":
        repository.replace_current_world_cup_matches(download.provider_id, parse_match_rows(text), imported_at_utc)
    elif download.provider_id in {"martj42_world_schedule", "martj42_local_schedule"}:
        repository.replace_current_world_cup_matches(
            download.provider_id, parse_world_cup_schedule_rows(text), imported_at_utc
        )
    else:
        raise ValueError(f"unsupported World Cup dataset provider: {download.provider_id}")


KAGGLE_DATASETS = {
    "swaptr_wc2026_players": "swaptr/fifa-wc-2026-players",
    "swaptr_wc2026_teams": "swaptr/fifa-wc-2026-teams",
    "swaptr_wc2026_matches": "swaptr/fifa-wc-2026-matches",
}
MARTJ42_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

PARSER_VERSION = "4"


def fetch_kaggle_world_cup_dataset(provider_id: str) -> DatasetDownload:
    import requests

    if provider_id == "martj42_world_schedule":
        response = requests.get(MARTJ42_RESULTS_URL, timeout=30)
        response.raise_for_status()
        content = response.content
        return DatasetDownload(
            provider_id, f"upstream/parser-{PARSER_VERSION}", content,
            datetime.now(timezone.utc), max(0, content.count(b"\n") - 1),
        )

    slug = KAGGLE_DATASETS[provider_id]
    metadata_response = requests.get(f"https://www.kaggle.com/api/v1/datasets/view/{slug}", timeout=30)
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    archive_response = requests.get(f"https://www.kaggle.com/api/v1/datasets/download/{slug}", timeout=60)
    archive_response.raise_for_status()
    with ZipFile(BytesIO(archive_response.content)) as archive:
        csv_names = [name for name in archive.namelist() if name.casefold().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"{provider_id} archive contains no CSV")
        preferred = next((name for name in csv_names if provider_id.rsplit("_", 1)[-1] in name.casefold()), csv_names[0])
        content = archive.read(preferred)
    updated_raw = metadata.get("lastUpdated") or metadata.get("lastUpdatedDate")
    updated = None
    if updated_raw:
        updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
    dataset_version = str(metadata.get("currentVersionNumber") or metadata.get("versionNumber") or "unknown")
    version = f"{dataset_version}/parser-{PARSER_VERSION}"
    row_count = max(0, content.count(b"\n") - 1)
    return DatasetDownload(provider_id, version, content, updated or datetime.now(timezone.utc), row_count)
