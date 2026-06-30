from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import closing
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import csv
import hashlib
import re
import sqlite3
import time
from urllib.parse import quote_plus

import requests

from wcpredict.knockout_bracket import (
    COMPETITION,
    _group_position_clinched,
    _group_standings,
)
from wcpredict.names import canonical_team_name
from wcpredict.repository import Repository


TRANSFERMARKT_BASE = "https://www.transfermarkt.com"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class PenaltyPlayerTarget:
    player_name: str
    team_name: str
    position: str | None
    minutes: int
    transfermarkt_player_id: str | None = None


@dataclass(frozen=True)
class IdentityCandidate:
    player_name: str
    team_name: str
    candidate_name: str
    transfermarkt_player_id: str
    url: str
    confidence: float
    reason: str


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []
        self.row_default_outcomes: list[str | None] = []
        self.penalty_section_outcome: str | None = None
        self.link_stack: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self.in_table = True
        if self.in_table and tag == "tr":
            self.current_row = []
        if self.in_table and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []
        if tag == "a":
            href = attrs_dict.get("href")
            if href:
                self.link_stack.append(href)

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag in {"td", "th"} and self.in_cell:
            text = " ".join("".join(self.current_cell).split())
            self.current_row.append(unescape(text))
            self.in_cell = False
        if self.in_table and tag == "tr":
            if any(cell for cell in self.current_row):
                self.rows.append(self.current_row)
                self.row_default_outcomes.append(self.penalty_section_outcome)
            self.current_row = []
        if tag == "table":
            self.in_table = False
        if tag == "a" and self.link_stack:
            self.link_stack.pop()

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split()).casefold()
        if "total penalties saved or missed" in normalized:
            self.penalty_section_outcome = "unknown_miss"
        elif "total non-saved penalties" in normalized:
            self.penalty_section_outcome = "scored"
        elif "total penalties saved" in normalized:
            self.penalty_section_outcome = "saved"
        elif "total penalties scored" in normalized:
            self.penalty_section_outcome = "scored"
        elif "total penalties missed" in normalized:
            self.penalty_section_outcome = "missed"
        if self.in_cell:
            self.current_cell.append(data)
        if self.link_stack:
            text = " ".join(data.split())
            if text:
                self.links.append((text, self.link_stack[-1]))


def slugify_player_name(player_name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", player_name.lower()).strip("-")
    return text or "player"


def penalty_url(player_name: str, transfermarkt_player_id: str) -> str:
    return f"{TRANSFERMARKT_BASE}/{slugify_player_name(player_name)}/elfmetertore/spieler/{transfermarkt_player_id}"


def goalkeeper_penalty_url(player_name: str, transfermarkt_player_id: str) -> str:
    return f"{TRANSFERMARKT_BASE}/{slugify_player_name(player_name)}/elfmeterstatistik/spieler/{transfermarkt_player_id}"


def load_penalty_team_snapshot(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        teams = [canonical_team_name(row["team_name"]) for row in csv.DictReader(handle)]
    return list(dict.fromkeys(team for team in teams if team))


def reconcile_penalty_teams(
    snapshot: list[str], dynamic: list[str]
) -> dict[str, list[str]]:
    wanted = {canonical_team_name(team) for team in snapshot}
    actual = {canonical_team_name(team) for team in dynamic}
    return {
        "missing_from_bracket": sorted(wanted - actual),
        "unexpected_in_bracket": sorted(actual - wanted),
    }


def eligible_penalty_teams(repo: Repository) -> list[str]:
    """Teams worth fetching now: resolved bracket teams, closed top-two, clinched winners."""
    team_ids: set[int] = set()
    with closing(sqlite3.connect(repo.path, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        try:
            bracket_rows = con.execute(
                "SELECT home_team_id, away_team_id FROM knockout_bracket WHERE competition=?",
                (COMPETITION,),
            ).fetchall()
        except sqlite3.OperationalError:
            bracket_rows = []
        for row in bracket_rows:
            for key in ("home_team_id", "away_team_id"):
                if row[key] is not None:
                    team_ids.add(int(row[key]))
        for letter in "ABCDEFGHIJKL":
            standings = _group_standings(con, letter)
            if len(standings) >= 2:
                team_ids.update(int(team_id) for team_id, _ in standings[:2])
                continue
            clinched = _group_position_clinched(con, letter, 1)
            if clinched is not None:
                team_ids.add(int(clinched[0]))
        if not team_ids:
            return []
        placeholders = ",".join("?" for _ in team_ids)
        rows = con.execute(
            f"SELECT name FROM teams WHERE id IN ({placeholders}) ORDER BY name",
            tuple(sorted(team_ids)),
        ).fetchall()
    return [str(row["name"]) for row in rows]


def active_knockout_teams(repo: Repository) -> list[str]:
    """Return teams attached to at least one unfinished knockout slot."""
    team_ids: set[int] = set()
    with closing(sqlite3.connect(repo.path, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        slots = con.execute(
            "SELECT kb.home_team_id, kb.away_team_id, m.status "
            "FROM knockout_bracket kb "
            "LEFT JOIN matches m ON m.id=kb.match_id "
            "WHERE kb.competition=?",
            (COMPETITION,),
        ).fetchall()
        for row in slots:
            if str(row["status"] or "scheduled").casefold() == "finished":
                continue
            for key in ("home_team_id", "away_team_id"):
                if row[key] is not None:
                    team_ids.add(int(row[key]))
        if not team_ids:
            return []
        placeholders = ",".join("?" for _ in team_ids)
        rows = con.execute(
            f"SELECT name FROM teams WHERE id IN ({placeholders}) ORDER BY name",
            tuple(sorted(team_ids)),
        ).fetchall()
    return sorted({canonical_team_name(str(row["name"])) for row in rows})


def player_targets_for_teams(repo: Repository, team_names: list[str]) -> list[PenaltyPlayerTarget]:
    if not team_names:
        return []
    tm_ids = repo.list_transfermarkt_player_ids()
    rows = repo.list_current_world_cup_players()
    selected: list[PenaltyPlayerTarget] = []
    team_set = {canonical_team_name(team) for team in team_names}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        team = canonical_team_name(str(row.get("team_name") or ""))
        name = str(row.get("player_name") or "").strip()
        minutes = int(row.get("minutes") or 0)
        if team not in team_set or not name:
            continue
        key = (name, team)
        if key in seen:
            continue
        seen.add(key)
        selected.append(
            PenaltyPlayerTarget(
                player_name=name,
                team_name=team,
                position=row.get("position"),
                minutes=minutes,
                transfermarkt_player_id=tm_ids.get(key),
            )
        )
    return sorted(selected, key=lambda row: (row.team_name, -row.minutes, row.player_name))


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.html"


def fetch_html(url: str, cache_dir: Path, *, refresh: bool = False, pause_seconds: float = 1.0) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, url)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8", errors="ignore")
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    text = response.text
    path.write_text(text, encoding="utf-8")
    if pause_seconds > 0:
        time.sleep(pause_seconds)
    return text


def search_transfermarkt_player(
    player_name: str,
    team_name: str,
    cache_dir: Path,
    *,
    refresh: bool = False,
) -> IdentityCandidate | None:
    url = f"{TRANSFERMARKT_BASE}/schnellsuche/ergebnis/schnellsuche?query={quote_plus(player_name)}"
    html = fetch_html(url, cache_dir, refresh=refresh, pause_seconds=0.5)
    parser = TableParser()
    parser.feed(html)
    name_norm = _norm(player_name)
    best: IdentityCandidate | None = None
    for text, href in parser.links:
        match = re.search(r"/profil/spieler/(\d+)", href)
        if not match:
            continue
        candidate_norm = _norm(text)
        if not candidate_norm:
            continue
        if candidate_norm == name_norm:
            confidence = 0.98
            reason = "exact_name"
        elif name_norm in candidate_norm or candidate_norm in name_norm:
            confidence = 0.82
            reason = "partial_name"
        else:
            continue
        candidate = IdentityCandidate(
            player_name=player_name,
            team_name=team_name,
            candidate_name=text,
            transfermarkt_player_id=match.group(1),
            url=TRANSFERMARKT_BASE + href if href.startswith("/") else href,
            confidence=confidence,
            reason=reason,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_penalty_attempts(
    html: str,
    *,
    player_name: str,
    team_name: str,
    transfermarkt_player_id: str,
    source_url: str,
    fetched_at_utc: datetime,
    default_outcome: str | None = None,
) -> list[dict]:
    parser = TableParser()
    parser.feed(html)
    attempts: list[dict] = []
    for index, (row, section_outcome) in enumerate(
        zip(parser.rows, parser.row_default_outcomes)
    ):
        cells = [cell for cell in row if cell]
        if len(cells) < 3:
            continue
        lowered = " | ".join(cells).lower()
        if any(header in lowered for header in ("date", "competition", "result", "goalkeeper")):
            continue
        outcome = _row_outcome(
            cells, default_outcome=section_outcome or default_outcome
        )
        if outcome is None:
            continue
        date_value = _first_date(cells)
        competition = cells[1] if len(cells) > 1 else None
        minute = next((cell for cell in cells if re.match(r"^\d{1,3}'", cell)), None)
        goalkeeper = _goalkeeper_from_cells(cells)
        opponent = _opponent_from_cells(cells)
        # Keep the identity key compatible with rows imported before table
        # context was available. The corrected section outcome may change
        # scored -> missed, but must update that row rather than duplicate it.
        legacy_key_outcome = _row_outcome(cells) or outcome
        source_row_key = (
            f"transfermarkt:{transfermarkt_player_id}:"
            f"{date_value or 'unknown'}:{index}:{legacy_key_outcome}:"
            f"{hashlib.sha1('|'.join(cells).encode()).hexdigest()[:10]}"
        )
        attempts.append({
            "player_name": player_name,
            "team_name": team_name,
            "transfermarkt_player_id": transfermarkt_player_id,
            "attempted_on": date_value,
            "competition": competition,
            "phase": "shootout" if "penalty shootout" in lowered or "shoot-out" in lowered else "regular",
            "outcome": outcome,
            "goalkeeper_name": goalkeeper,
            "opponent_team": opponent,
            "minute": minute,
            "match_label": " | ".join(cells[:8]),
            "source_provider": "transfermarkt",
            "source_url": source_url,
            "source_row_key": source_row_key,
            "fetched_at_utc": fetched_at_utc.isoformat(),
            "raw": {"cells": cells},
        })
    return attempts


def parse_goalkeeper_penalty_attempts(
    html: str,
    *,
    goalkeeper_name: str,
    transfermarkt_player_id: str,
    source_url: str,
    fetched_at_utc: datetime,
) -> list[dict]:
    parser = TableParser()
    parser.feed(html)
    attempts: list[dict] = []
    for index, (row, section_outcome) in enumerate(
        zip(parser.rows, parser.row_default_outcomes)
    ):
        cells = [cell for cell in row if cell]
        if len(cells) < 3:
            continue
        lowered = " | ".join(cells).casefold()
        if any(
            header in lowered
            for header in ("date", "competition", "result", "penalty taker")
        ):
            continue
        outcome = _row_outcome(cells, default_outcome=section_outcome)
        if outcome == "missed":
            outcome = "unknown_miss"
        if outcome not in {
            "saved", "scored", "off_target", "woodwork", "unknown_miss"
        }:
            continue
        date_value = _first_date(cells)
        source_row_key = (
            f"transfermarkt-gk:{transfermarkt_player_id}:"
            f"{date_value or 'unknown'}:{index}:{outcome}:"
            f"{hashlib.sha1('|'.join(cells).encode()).hexdigest()[:10]}"
        )
        attempts.append({
            "goalkeeper_name": goalkeeper_name,
            "transfermarkt_player_id": transfermarkt_player_id,
            "attempted_on": date_value,
            "competition": cells[1] if len(cells) > 1 else None,
            "phase": "shootout" if "penalty shootout" in lowered or "shoot-out" in lowered else "regular",
            "outcome": outcome,
            "taker_name": _taker_from_cells(cells),
            "opponent_team": _opponent_from_cells(cells),
            "match_label": " | ".join(cells[:8]),
            "source_provider": "transfermarkt",
            "source_url": source_url,
            "source_row_key": source_row_key,
            "fetched_at_utc": fetched_at_utc.isoformat(),
            "raw": {"cells": cells, "section_outcome": section_outcome},
        })
    return attempts


def _row_outcome(cells: list[str], *, default_outcome: str | None = None) -> str | None:
    text = " ".join(cells).lower()
    if "saved" in text:
        return "saved"
    if "off target" in text:
        return "off_target"
    if "woodwork" in text:
        return "woodwork"
    if "missed" in text:
        return "missed"
    if "scored" in text or "goal" in text:
        return "scored"
    if any(re.search(pattern, text) for pattern in (r"\b\d+:\d+\b", r"\b\d+-\d+\b")):
        # Transfermarkt's successful-penalty table often lacks an explicit
        # "scored" token; if the row has a match result and no miss token,
        # treat it as a made penalty.
        return default_outcome or "scored"
    return None


def _first_date(cells: list[str]) -> str | None:
    for cell in cells:
        match = re.search(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})\b", cell)
        if match:
            try:
                return datetime.strptime(match.group(1), "%b %d, %Y").date().isoformat()
            except ValueError:
                return match.group(1)
        match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", cell)
        if match:
            return match.group(1)
    return None


def _goalkeeper_from_cells(cells: list[str]) -> str | None:
    for idx, cell in enumerate(cells):
        if "goalkeeper" in cell.lower() and idx + 1 < len(cells):
            return cells[idx + 1]
    # Heuristic: Transfermarkt rows usually put the keeper toward the end.
    for cell in reversed(cells):
        if cell and not re.search(r"\d|:|'", cell) and len(cell.split()) <= 4:
            return cell
    return None


def _taker_from_cells(cells: list[str]) -> str | None:
    for idx, cell in enumerate(cells):
        if "penalty taker" in cell.casefold() and idx + 1 < len(cells):
            return cells[idx + 1]
    for cell in reversed(cells):
        if cell and not re.search(r"\d|:|'", cell) and len(cell.split()) <= 5:
            return cell
    return None


def _opponent_from_cells(cells: list[str]) -> str | None:
    for idx, cell in enumerate(cells):
        if cell.lower() in {"against", "opponent"} and idx + 1 < len(cells):
            return cells[idx + 1]
    return None


def write_identity_review(path: Path, candidates: list[IdentityCandidate], missing: list[PenaltyPlayerTarget]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "player_name", "team_name", "candidate_name", "transfermarkt_player_id",
                "confidence", "reason", "url", "status",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({
                "player_name": candidate.player_name,
                "team_name": candidate.team_name,
                "candidate_name": candidate.candidate_name,
                "transfermarkt_player_id": candidate.transfermarkt_player_id,
                "confidence": f"{candidate.confidence:.2f}",
                "reason": candidate.reason,
                "url": candidate.url,
                "status": "auto" if candidate.confidence >= 0.95 else "review",
            })
        for target in missing:
            writer.writerow({
                "player_name": target.player_name,
                "team_name": target.team_name,
                "candidate_name": "",
                "transfermarkt_player_id": "",
                "confidence": "0.00",
                "reason": "not_found",
                "url": "",
                "status": "missing",
            })
