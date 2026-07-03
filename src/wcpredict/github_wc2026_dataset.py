from __future__ import annotations

import csv
import re
from io import StringIO
from typing import Any


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _rows(csv_text: str) -> list[dict[str, str | None]]:
    reader = csv.DictReader(StringIO(csv_text.lstrip("﻿")))
    return [
        {
            _key(str(name)): (value.strip() if value is not None and value.strip() else None)
            for name, value in row.items()
            if name is not None and isinstance(value, (str, type(None)))
        }
        for row in reader
    ]


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "."))
    except ValueError:
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_events_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        event_id = _int(row.get("event_id"))
        match_id = _int(row.get("match_id"))
        minute = _int(row.get("minute"))
        team_id = _int(row.get("team_id"))
        player_id = _int(row.get("player_id"))
        event_type = _str(row.get("event_type"))
        if (
            event_id is None or match_id is None or minute is None
            or team_id is None or player_id is None or event_type is None
        ):
            continue
        output.append(
            {
                "external_event_id": event_id,
                "external_match_id": match_id,
                "minute": minute,
                "event_type": event_type,
                "external_team_id": team_id,
                "external_player_id": player_id,
            }
        )
    return output


def parse_team_stats_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        match_id = _int(row.get("match_id"))
        team_id = _int(row.get("team_id"))
        if match_id is None or team_id is None:
            continue
        output.append(
            {
                "external_match_id": match_id,
                "external_team_id": team_id,
                "possession_pct": _float(row.get("possession_pct")),
                "total_shots": _int(row.get("total_shots")),
                "shots_on_target": _int(row.get("shots_on_target")),
                "corners": _int(row.get("corners")),
                "fouls": _int(row.get("fouls")),
                "offsides": _int(row.get("offsides")),
                "saves": _int(row.get("saves")),
                "player_of_the_match": _str(row.get("player_of_the_match")),
                "data_source": _str(row.get("data_source")),
                "last_updated": _str(row.get("last_updated")),
            }
        )
    return output


def parse_lineups_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        lineup_id = _int(row.get("lineup_id"))
        match_id = _int(row.get("match_id"))
        player_id = _int(row.get("player_id"))
        team_id = _int(row.get("team_id"))
        if lineup_id is None or match_id is None or player_id is None or team_id is None:
            continue
        output.append(
            {
                "external_lineup_id": lineup_id,
                "external_match_id": match_id,
                "external_player_id": player_id,
                "external_team_id": team_id,
                "is_starting_xi": _int(row.get("is_starting_xi")) or 0,
                "tactical_position": _str(row.get("tactical_position")),
                "minutes_played": _int(row.get("minutes_played")),
            }
        )
    return output


def parse_matches_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        match_id = _int(row.get("match_id"))
        if match_id is None:
            continue
        output.append(
            {
                "external_match_id": match_id,
                "date": _str(row.get("date")),
                "kickoff_time_utc": _str(row.get("kickoff_time_utc")),
                "stage_name": _str(row.get("stage_name")),
                "stadium_name": _str(row.get("stadium_name")),
                "city": _str(row.get("city")),
                "country": _str(row.get("country")),
                "home_team_name": _str(row.get("home_team_name")),
                "home_fifa_code": _str(row.get("home_fifa_code")),
                "away_team_name": _str(row.get("away_team_name")),
                "away_fifa_code": _str(row.get("away_fifa_code")),
                "home_score": _int(row.get("home_score")),
                "away_score": _int(row.get("away_score")),
                "status": _str(row.get("status")),
                "home_xg": _float(row.get("home_xg")),
                "away_xg": _float(row.get("away_xg")),
                "home_goalkeeper": _str(row.get("home_goalkeeper")),
                "away_goalkeeper": _str(row.get("away_goalkeeper")),
                "player_of_the_match_name": _str(row.get("player_of_the_match_name")),
                "referee_name": _str(row.get("referee_name")),
            }
        )
    return output


def parse_referees_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        referee_id = _int(row.get("referee_id"))
        name = _str(row.get("name"))
        if referee_id is None or name is None:
            continue
        output.append(
            {
                "external_referee_id": referee_id,
                "referee_name": name,
                "country": _str(row.get("country")),
                "avg_cards_per_game": _float(row.get("avg_cards_per_game")),
            }
        )
    return output


def parse_player_stats_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        player_id = _int(row.get("player_id"))
        name = _str(row.get("player_name"))
        if player_id is None or name is None:
            continue
        output.append(
            {
                "external_player_id": player_id,
                "player_name": name,
                "external_team_id": _int(row.get("team_id")),
                "position": _str(row.get("position")),
                "matches_played": _int(row.get("matches_played")),
                "matches_started": _int(row.get("matches_started")),
                "minutes_played": _int(row.get("minutes_played")),
                "goals": _int(row.get("goals")),
                "assists": _int(row.get("assists")),
                "shots": _int(row.get("shots")),
                "shots_on_target": _int(row.get("shots_on_target")),
                "yellow_cards": _int(row.get("yellow_cards")),
                "red_cards": _int(row.get("red_cards")),
                "penalty_goals": _int(row.get("penalty_goals")),
                "own_goals": _int(row.get("own_goals")),
                "clean_sheets": _int(row.get("clean_sheets")),
                "saves": _int(row.get("saves")),
                "goals_conceded": _int(row.get("goals_conceded")),
                "average_rating": _float(row.get("average_rating")),
                "data_source": _str(row.get("data_source")),
                "last_verified": _str(row.get("last_verified")),
            }
        )
    return output


GITHUB_DATASETS = {
    "github_wc2026_events": "match_events.csv",
    "github_wc2026_team_stats": "match_team_stats.csv",
    "github_wc2026_lineups": "match_lineups.csv",
    "github_wc2026_matches": "matches_detailed.csv",
    "github_wc2026_referees": "referees.csv",
    "github_wc2026_player_stats": "player_stats.csv",
    "github_wc2026_teams": "teams.csv",
}

PARSER_VERSION = "1"


def import_github_wc2026_download(repository, download, imported_at_utc) -> None:
    text = download.content.decode("utf-8-sig")
    provider_id = download.provider_id
    version = download.version
    if provider_id == "github_wc2026_events":
        repository.replace_gh_events(provider_id, parse_events_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_team_stats":
        repository.replace_gh_team_stats(provider_id, parse_team_stats_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_lineups":
        repository.replace_gh_lineups(provider_id, parse_lineups_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_matches":
        repository.replace_gh_matches(provider_id, parse_matches_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_referees":
        repository.replace_gh_referees(provider_id, parse_referees_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_player_stats":
        repository.replace_gh_player_stats(provider_id, parse_player_stats_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_teams":
        repository.replace_gh_teams(provider_id, parse_teams_rows(text), version, imported_at_utc)
    else:
        raise ValueError(f"unsupported github_wc2026 provider: {provider_id}")
    repository.resolve_gh_foreign_keys(provider_id)
    if provider_id == "github_wc2026_matches":
        repository.record_score_verifications_for_provider(
            provider_id, imported_at_utc.isoformat()
        )


def derive_period(minute: int) -> str:
    if 1 <= minute <= 45:
        return "first_half"
    if 46 <= minute <= 90:
        return "second_half"
    if 91 <= minute <= 105:
        return "et_first"
    if 106 <= minute <= 120:
        return "et_second"
    raise ValueError(f"minute out of expected range: {minute}")


def parse_teams_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        team_id = _int(row.get("team_id"))
        name = _str(row.get("team_name"))
        if team_id is None or name is None:
            continue
        output.append(
            {
                "external_team_id": team_id,
                "team_name": name,
                "fifa_code": _str(row.get("fifa_code")),
                "group_letter": _str(row.get("group_letter")),
                "confederation": _str(row.get("confederation")),
                "fifa_ranking_pre_tournament": _int(row.get("fifa_ranking_pre_tournament")),
                "elo_rating": _int(row.get("elo_rating")),
                "manager_name": _str(row.get("manager_name")),
            }
        )
    return output


RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/mominullptr/FIFA-World-Cup-2026-Dataset/main/{filename}"
COMMITS_API_URL = "https://api.github.com/repos/mominullptr/FIFA-World-Cup-2026-Dataset/commits/main"


def resolve_latest_commit():
    import requests
    from datetime import datetime
    response = requests.get(COMMITS_API_URL, timeout=15)
    response.raise_for_status()
    payload = response.json()
    sha = str(payload["sha"])[:7]
    committed = payload["commit"]["committer"]["date"]
    committed_at = datetime.fromisoformat(str(committed).replace("Z", "+00:00"))
    return sha, committed_at


def fetch_github_wc2026_dataset(provider_id, commit_sha=None, commit_date=None):
    import requests
    from wcpredict.daily_refresh import DatasetDownload
    if provider_id not in GITHUB_DATASETS:
        raise ValueError(f"unsupported github_wc2026 provider: {provider_id}")
    if commit_sha is None or commit_date is None:
        commit_sha, commit_date = resolve_latest_commit()
    url = RAW_URL_TEMPLATE.format(filename=GITHUB_DATASETS[provider_id])
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    content = response.content
    return DatasetDownload(
        provider_id, f"sha:{commit_sha}/parser-{PARSER_VERSION}", content,
        commit_date, max(0, content.count(b"\n") - 1),
    )
