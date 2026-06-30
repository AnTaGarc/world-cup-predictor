from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
import unicodedata

from wcpredict.advanced_form import build_goalkeeper_baseline
from wcpredict.names import canonical_team_name, same_team
from wcpredict.penalty_history_model import (
    DEFAULT_SIMULATIONS,
    PENALTY_MODEL_VERSION,
    PenaltyCoverage,
    PenaltyGoalkeeperContribution,
    PenaltyMatchContext,
    PenaltyPlayerContribution,
    PenaltyTeamShootoutCoverage,
    PenaltyTeamProfile,
    build_penalty_match_context,
)
from wcpredict.player_markets import is_goalkeeper
from wcpredict.repository import Repository
from wcpredict.squad_context import apply_squad_context


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", canonical_team_name(value))
    ascii_value = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")


def precomputed_artifact_path(directory: Path, team_a: str, team_b: str) -> Path:
    return directory / f"{_slug(team_a)}--{_slug(team_b)}.json"


def _context_from_dict(raw: dict) -> PenaltyMatchContext:
    return PenaltyMatchContext(
        team_a=PenaltyTeamProfile(**raw["team_a"]),
        team_b=PenaltyTeamProfile(**raw["team_b"]),
        team_a_shootout_win_probability=float(raw["team_a_shootout_win_probability"]),
        team_b_shootout_win_probability=float(raw["team_b_shootout_win_probability"]),
        player_rows=tuple(PenaltyPlayerContribution(**row) for row in raw.get("player_rows", [])),
        coverage=PenaltyCoverage(**raw.get("coverage", {
            "squad_players": 0, "players_with_history": 0, "attempts": 0,
            "team_a_squad_players": 0, "team_b_squad_players": 0,
            "team_a_players_with_history": 0, "team_b_players_with_history": 0,
        })),
        simulations=int(raw.get("simulations", 0)),
        standard_error=float(raw.get("standard_error", 0.0)),
        goalkeeper_rows=tuple(
            PenaltyGoalkeeperContribution(**row)
            for row in raw.get("goalkeeper_rows", [])
        ),
        shootout_coverage_rows=tuple(
            PenaltyTeamShootoutCoverage(
                team_name=row["team_name"],
                competitions=tuple(row.get("competitions", [])),
                shootout_attempts=int(row.get("shootout_attempts", 0)),
            )
            for row in raw.get("shootout_coverage_rows", [])
        ),
        data_cutoff=str(raw.get("data_cutoff") or ""),
        model_version=str(raw.get("model_version") or PENALTY_MODEL_VERSION),
        explanation=str(raw.get("explanation") or ""),
    )


def save_precomputed_context(
    directory: Path,
    match_id: int,
    team_a: str,
    team_b: str,
    context: PenaltyMatchContext,
    *,
    input_fingerprint: str,
    model_version: str = PENALTY_MODEL_VERSION,
    generated_at_utc: datetime | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = precomputed_artifact_path(directory, team_a, team_b)
    payload = {
        "schema_version": 1,
        "model_version": model_version,
        "match_id": int(match_id),
        "team_a": canonical_team_name(team_a),
        "team_b": canonical_team_name(team_b),
        "generated_at_utc": (generated_at_utc or datetime.now(timezone.utc)).isoformat(),
        "input_fingerprint": input_fingerprint,
        "context": asdict(context),
    }
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_precomputed_context(
    directory: Path,
    team_a: str,
    team_b: str,
    *,
    model_version: str = PENALTY_MODEL_VERSION,
    expected_input_fingerprint: str | None = None,
) -> PenaltyMatchContext | None:
    target = precomputed_artifact_path(directory, team_a, team_b)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or payload.get("model_version") != model_version:
            return None
        if (
            expected_input_fingerprint is not None
            and payload.get("input_fingerprint") != expected_input_fingerprint
        ):
            return None
        if canonical_team_name(str(payload.get("team_a") or "")) != canonical_team_name(team_a):
            return None
        if canonical_team_name(str(payload.get("team_b") or "")) != canonical_team_name(team_b):
            return None
        return _context_from_dict(payload["context"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None


def group_stage_complete(repo: Repository, team_name: str) -> bool:
    completed = 0
    for match in repo.list_matches():
        if match.competition != "FIFA World Cup 2026":
            continue
        if not str(match.stage or "").startswith("Group stage"):
            continue
        if not (same_team(match.team_a.name, team_name) or same_team(match.team_b.name, team_name)):
            continue
        if repo.get_match_result(match.id) is not None:
            completed += 1
    return completed >= 3


def _repository_inputs(repo: Repository, match) -> tuple[
    dict, dict, dict, list[dict], list[dict], list[dict]
]:
    team_names = (match.team_a.name, match.team_b.name)
    selected = [
        dict(row) for row in repo.list_current_world_cup_players()
        if any(same_team(str(row.get("team_name") or ""), team) for team in team_names)
    ]
    known = {
        (str(row.get("player_name") or ""), canonical_team_name(str(row.get("team_name") or "")))
        for row in selected
    }
    for row in repo.list_deep_goalkeeper_player_profiles(team_names):
        key = (str(row.get("player_name") or ""), canonical_team_name(str(row.get("team_name") or "")))
        if key not in known:
            selected.append(dict(row))
            known.add(key)
    for row in selected:
        games = max(1, int(row.get("games") or 0))
        row.setdefault("expected_minutes", min(90, round(int(row.get("minutes") or 0) / games)))
        row.setdefault("starter_probability", min(1.0, int(row.get("starts") or 0) / games))
        row.setdefault("availability", "available")
    events = repo.list_active_squad_context_events(team_names, match.kickoff_utc, match.id)
    selected, _ = apply_squad_context(selected, events, match.kickoff_utc, match.id)
    squads = {
        team: [row for row in selected if same_team(str(row.get("team_name") or ""), team)]
        for team in team_names
    }

    lineups: dict[str, list[str]] = {}
    imported = repo.list_imported_lineups(match.id)
    for team in team_names:
        starters = [
            str(row.get("player_name") or "") for row in imported
            if same_team(str(row.get("team_name") or ""), team)
            and str(row.get("lineup_status") or "").casefold()
            in {"starter", "starting", "starting xi", "confirmed"}
        ]
        if starters:
            lineups[team] = starters

    deep_rows = repo.list_deep_goalkeeper_rows_before(match.kickoff_utc)
    deep_rates: dict[str, float] = {}
    for team in team_names:
        baseline = build_goalkeeper_baseline(team, deep_rows, match.kickoff_utc)
        if baseline.save_rate is None:
            continue
        for player in squads[team]:
            if is_goalkeeper(player):
                deep_rates[str(player.get("player_name") or "")] = baseline.save_rate
    attempts = repo.list_penalty_evidence(team_names, match.kickoff_utc)
    historical_kicks = repo.list_historical_shootout_kicks(
        team_names, match.kickoff_utc
    )
    historical_attempts = [
        {
            "player_name": row.get("player_name"),
            "team_name": row.get("team_name"),
            "attempted_on": row.get("played_on"),
            "competition": row.get("competition"),
            "phase": "shootout",
            "outcome": row.get("outcome"),
            "goalkeeper_name": row.get("goalkeeper_name"),
            "opponent_team": (
                row.get("team_b")
                if same_team(str(row.get("team_a") or ""), str(row.get("team_name") or ""))
                else row.get("team_a")
            ),
            "source_provider": row.get("source_provider"),
            "source_url": row.get("source_url"),
            "source_row_key": row.get("source_row_key"),
        }
        for row in historical_kicks
    ]
    attempts.extend(historical_attempts)

    goalkeeper_attempts: list[dict] = []
    goalkeeper_names = {
        str(player.get("player_name") or "")
        for team in team_names
        for player in squads[team]
        if is_goalkeeper(player)
    }
    for goalkeeper_name in sorted(goalkeeper_names):
        goalkeeper_attempts.extend(
            repo.list_goalkeeper_penalty_attempts(
                goalkeeper_name, match.kickoff_utc
            )
        )
    goalkeeper_attempts.extend([
        row for row in attempts
        if str(row.get("source_provider") or "") != "transfermarkt"
    ])
    coverage = [
        row
        for team in team_names
        for row in repo.list_historical_shootout_coverage(team)
    ]
    return (
        squads, lineups, deep_rates, attempts, goalkeeper_attempts, coverage
    )


def _group_results_for_fingerprint(repo: Repository, match) -> list[dict]:
    rows = []
    for candidate in repo.list_matches():
        if candidate.competition != "FIFA World Cup 2026" or not str(candidate.stage or "").startswith("Group stage"):
            continue
        if not any(
            same_team(team, candidate.team_a.name) or same_team(team, candidate.team_b.name)
            for team in (match.team_a.name, match.team_b.name)
        ):
            continue
        result = repo.get_match_result(candidate.id)
        if result is not None:
            rows.append({"match_id": candidate.id, **dict(result)})
    return rows


def _input_fingerprint(
    match,
    squads: dict,
    lineups: dict,
    deep_rates: dict,
    attempts: list[dict],
    goalkeeper_attempts: list[dict],
    shootout_coverage: list[dict],
    group_results: list[dict],
    model_version: str,
) -> str:
    source = json.dumps(
        {
            "teams": [canonical_team_name(match.team_a.name), canonical_team_name(match.team_b.name)],
            "squads": squads,
            "lineups": lineups,
            "deep_rates": deep_rates,
            "attempts": attempts,
            "goalkeeper_attempts": goalkeeper_attempts,
            "shootout_coverage": shootout_coverage,
            "group_results": group_results,
            "model_version": model_version,
        },
        default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return sha256(source.encode("utf-8")).hexdigest()


def repository_penalty_input_fingerprint(
    repo: Repository,
    match,
    *,
    model_version: str = PENALTY_MODEL_VERSION,
) -> str:
    (
        squads, lineups, deep_rates, attempts, goalkeeper_attempts,
        shootout_coverage,
    ) = _repository_inputs(repo, match)
    return _input_fingerprint(
        match, squads, lineups, deep_rates, attempts, goalkeeper_attempts,
        shootout_coverage,
        _group_results_for_fingerprint(repo, match), model_version,
    )


def build_repository_penalty_context(
    repo: Repository,
    match,
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    model_version: str = PENALTY_MODEL_VERSION,
) -> tuple[PenaltyMatchContext, str]:
    (
        squads, lineups, deep_rates, attempts, goalkeeper_attempts,
        shootout_coverage,
    ) = _repository_inputs(repo, match)
    fingerprint = _input_fingerprint(
        match,
        squads,
        lineups,
        deep_rates,
        attempts,
        goalkeeper_attempts,
        shootout_coverage,
        _group_results_for_fingerprint(repo, match),
        model_version,
    )
    seed = int(sha256(f"{match.id}:{model_version}".encode("utf-8")).hexdigest()[:16], 16)
    context = build_penalty_match_context(
        match.team_a.name,
        match.team_b.name,
        attempts,
        squads=squads,
        lineups=lineups,
        goalkeeper_attempts=goalkeeper_attempts,
        shootout_coverage=shootout_coverage,
        deep_goalkeeper_rates=deep_rates,
        as_of=match.kickoff_utc.date(),
        seed=seed,
        simulations=simulations,
    )
    return context, fingerprint
