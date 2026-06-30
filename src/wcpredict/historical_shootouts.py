from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import csv
import hashlib

from wcpredict.names import canonical_team_name
from wcpredict.repository import Repository


VALID_OUTCOMES = {"scored", "saved", "off_target", "woodwork"}


@dataclass(frozen=True)
class HistoricalShootoutImportSummary:
    coverage_rows: int
    shootout_rows: int
    kick_rows: int


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "sí", "si"}


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _shootout_key(row: dict) -> str:
    identity = "|".join(
        str(row.get(key) or "")
        for key in (
            "played_on", "competition", "competition_edition", "round_name",
            "team_a", "team_b", "winner_team", "source_url",
        )
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return f"historical-shootout:{digest}"


def import_historical_shootout_csv(
    repo: Repository,
    coverage_path: Path,
    kicks_path: Path,
    *,
    active_teams: set[str],
    dry_run: bool = False,
) -> HistoricalShootoutImportSummary:
    active = {canonical_team_name(team) for team in active_teams}
    coverage_candidates: dict[str, list[dict]] = {}
    for raw in _read_csv(coverage_path):
        team = canonical_team_name(str(raw.get("team_name") or ""))
        if team not in active or not _truthy(raw.get("senior")) or not _truthy(raw.get("official")):
            continue
        date.fromisoformat(str(raw["competition_end_on"]))
        row = {**raw, "team_name": team}
        coverage_candidates.setdefault(team, []).append(row)

    coverage_rows: list[dict] = []
    selected_competitions: dict[str, set[tuple[str, str]]] = {}
    for team, rows in coverage_candidates.items():
        selected = sorted(
            rows, key=lambda row: str(row["competition_end_on"]), reverse=True
        )[:3]
        coverage_rows.extend(selected)
        selected_competitions[team] = {
            (str(row["competition"]), str(row["competition_edition"]))
            for row in selected
        }

    shootouts_by_key: dict[tuple[str, str], dict] = {}
    kick_rows: list[dict] = []
    for raw in _read_csv(kicks_path):
        team_a = canonical_team_name(str(raw.get("team_a") or ""))
        team_b = canonical_team_name(str(raw.get("team_b") or ""))
        competition_key = (
            str(raw.get("competition") or ""),
            str(raw.get("competition_edition") or ""),
        )
        relevant = any(
            team in active and competition_key in selected_competitions.get(team, set())
            for team in (team_a, team_b)
        )
        if not relevant:
            continue
        outcome = str(raw.get("outcome") or "").casefold()
        if outcome not in VALID_OUTCOMES:
            raise ValueError(f"Unsupported shootout outcome: {outcome}")
        date.fromisoformat(str(raw["played_on"]))
        provider = str(raw["source_provider"])
        shootout_key = _shootout_key(raw)
        identity = (provider, shootout_key)
        shootouts_by_key[identity] = {
            "played_on": raw["played_on"],
            "competition": raw["competition"],
            "competition_edition": raw["competition_edition"],
            "round_name": raw.get("round_name"),
            "team_a": team_a,
            "team_b": team_b,
            "winner_team": canonical_team_name(str(raw["winner_team"])),
            "source_provider": provider,
            "source_url": raw["source_url"],
            "source_row_key": shootout_key,
            "retrieved_at_utc": raw["retrieved_at_utc"],
        }
        kick_rows.append({
            "shootout_source_provider": provider,
            "shootout_source_row_key": shootout_key,
            "sequence_number": int(raw["sequence_number"]),
            "team_name": canonical_team_name(str(raw["team_name"])),
            "player_name": raw.get("player_name"),
            "goalkeeper_name": raw.get("goalkeeper_name"),
            "outcome": outcome,
            "source_provider": provider,
            "source_url": raw["source_url"],
            "source_row_key": raw["source_row_key"],
            "retrieved_at_utc": raw["retrieved_at_utc"],
        })

    shootout_rows = list(shootouts_by_key.values())
    if not dry_run:
        repo.save_historical_shootout_coverage(coverage_rows)
        repo.save_historical_shootouts(shootout_rows, kick_rows)
    return HistoricalShootoutImportSummary(
        coverage_rows=len(coverage_rows),
        shootout_rows=len(shootout_rows),
        kick_rows=len(kick_rows),
    )
