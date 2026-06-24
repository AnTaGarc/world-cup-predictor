"""Knockout-stage bracket: seed, resolution and helpers.

Phase model for World Cup 2026 (104 matches total):

    Group stage (72) ─┐
                       ├─► Round of 32 (16) ─► R16 (8) ─► QF (4) ─► SF (2)
                                                                   ├─► Final (1)
                                                                   └─► 3rd place (1)

Each knockout slot has a `home_source` and `away_source` that point to either:
  * A group position: e.g. "1A" (winner of group A), "2B" (runner-up of B),
    "3A".."3H" (third place from the eight best 3rd-placed teams).
  * The winner/loser of a previous knockout: "W:R32-1", "L:SF-1".

Resolution runs whenever a group finishes or a knockout match finishes:
fills the team ids, creates a real `matches` row, and seeds downstream slots
as the brackets bubble up.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from wcpredict.repository import Repository

COMPETITION = "FIFA World Cup 2026"

# Stage labels — kept consistent with what we store in matches.stage.
STAGES = ("Round of 32", "Round of 16", "Quarter-final", "Semi-final",
          "Third-place play-off", "Final")

# Defensive schema: re-applied each time we touch the table so existing
# deployments that were initialised before this module shipped still work
# without forcing a database wipe.
_KNOCKOUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS knockout_bracket (
    id INTEGER PRIMARY KEY,
    competition TEXT NOT NULL,
    stage TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    venue TEXT,
    home_source TEXT NOT NULL,
    away_source TEXT NOT NULL,
    home_team_id INTEGER REFERENCES teams(id),
    away_team_id INTEGER REFERENCES teams(id),
    match_id INTEGER REFERENCES matches(id),
    resolved_at_utc TEXT,
    UNIQUE(competition, slot_id)
);
CREATE INDEX IF NOT EXISTS idx_knockout_bracket_stage
ON knockout_bracket(competition, stage);
"""


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_KNOCKOUT_SCHEMA)


@dataclass(frozen=True)
class BracketSlot:
    id: int
    stage: str
    slot_id: str
    kickoff_utc: str
    venue: str | None
    home_source: str
    away_source: str
    home_team_id: int | None
    away_team_id: int | None
    match_id: int | None


def load_bracket_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def seed_knockout_bracket(repo: Repository, path: Path) -> int:
    """Insert (or update) the knockout slots from the seed CSV.

    Slots not present in the CSV but stored in the DB are cleared as long
    as they have no resolved teams or attached match yet (defensive cleanup
    for legacy seed schemas)."""
    rows = load_bracket_csv(path)
    current_ids = {row["slot_id"] for row in rows}
    with sqlite3.connect(repo.path, timeout=30) as con:
        _ensure_schema(con)
        # Drop legacy slots from earlier seed versions (e.g. R32-1..R32-16),
        # but only when they have not yet been resolved/linked to a match —
        # never throw away history.
        con.execute(
            "DELETE FROM knockout_bracket WHERE competition=? AND slot_id NOT IN "
            f"({','.join('?' * len(current_ids))}) "
            "AND home_team_id IS NULL AND away_team_id IS NULL AND match_id IS NULL",
            (COMPETITION, *current_ids),
        )
        for row in rows:
            con.execute(
                "INSERT INTO knockout_bracket(competition, stage, slot_id, kickoff_utc, "
                "venue, home_source, away_source) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(competition, slot_id) DO UPDATE SET "
                "kickoff_utc=excluded.kickoff_utc, venue=excluded.venue, "
                "stage=excluded.stage, home_source=excluded.home_source, "
                "away_source=excluded.away_source",
                (COMPETITION, row["stage"], row["slot_id"], row["kickoff_utc"],
                 row.get("venue"), row["home_source"], row["away_source"]),
            )
        con.commit()
    return len(rows)


def list_bracket_slots(repo: Repository) -> list[BracketSlot]:
    with sqlite3.connect(repo.path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        _ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM knockout_bracket WHERE competition=? ORDER BY kickoff_utc, slot_id",
            (COMPETITION,),
        ).fetchall()
    return [
        BracketSlot(
            id=int(r["id"]), stage=r["stage"], slot_id=r["slot_id"],
            kickoff_utc=r["kickoff_utc"], venue=r["venue"],
            home_source=r["home_source"], away_source=r["away_source"],
            home_team_id=r["home_team_id"], away_team_id=r["away_team_id"],
            match_id=r["match_id"],
        )
        for r in rows
    ]


# ---- Group standings ---------------------------------------------------------


def _group_standings(con: sqlite3.Connection, group_letter: str) -> list[tuple[int, str]]:
    """Return (team_id, team_name) ordered by group position (1st, 2nd, 3rd, 4th).

    Standings use the FIFA ordering available from local data: points, then
    head-to-head points / goal difference / goals for among tied teams, then
    total goal difference and goals for. Discipline and FIFA ranking are not
    stored locally, so remaining ties are deterministic by team name/id.
    Returns empty if the group still has unfinished matches.
    """
    rows = con.execute(
        "SELECT m.id, m.team_a_id, m.team_b_id, m.status, "
        "  ta.name AS ta, tb.name AS tb, mr.goals_a, mr.goals_b "
        "FROM matches m "
        "JOIN teams ta ON ta.id=m.team_a_id "
        "JOIN teams tb ON tb.id=m.team_b_id "
        "LEFT JOIN match_results mr ON mr.match_id=m.id "
        "WHERE m.competition=? AND m.stage LIKE ?",
        (COMPETITION, f"Group stage - Group {group_letter}"),
    ).fetchall()
    if not rows:
        return []
    if any(r["status"] != "finished" or r["goals_a"] is None for r in rows):
        return []
    stats: dict[int, dict] = {}
    games: list[tuple[int, int, int, int]] = []
    for r in rows:
        ga, gb = int(r["goals_a"]), int(r["goals_b"])
        games.append((int(r["team_a_id"]), int(r["team_b_id"]), ga, gb))
        for tid, name, gf, gag in ((r["team_a_id"], r["ta"], ga, gb),
                                    (r["team_b_id"], r["tb"], gb, ga)):
            s = stats.setdefault(int(tid), {"name": name, "pts": 0, "gd": 0, "gf": 0})
            s["gf"] += gf
            s["gd"] += gf - gag
            if gf > gag:
                s["pts"] += 3
            elif gf == gag:
                s["pts"] += 1

    def total_key(tid: int) -> tuple[int, int, str, int]:
        s = stats[tid]
        return (-int(s["gd"]), -int(s["gf"]), str(s["name"]), tid)

    def h2h_table(tids: list[int]) -> dict[int, dict[str, int]]:
        tied = set(tids)
        table = {tid: {"pts": 0, "gd": 0, "gf": 0} for tid in tids}
        for home_id, away_id, home_goals, away_goals in games:
            if home_id not in tied or away_id not in tied:
                continue
            for tid, gf, ga in (
                (home_id, home_goals, away_goals),
                (away_id, away_goals, home_goals),
            ):
                table[tid]["gf"] += gf
                table[tid]["gd"] += gf - ga
            if home_goals > away_goals:
                table[home_id]["pts"] += 3
            elif away_goals > home_goals:
                table[away_id]["pts"] += 3
            else:
                table[home_id]["pts"] += 1
                table[away_id]["pts"] += 1
        return table

    def rank_tied(tids: list[int]) -> list[int]:
        if len(tids) <= 1:
            return tids
        table = h2h_table(tids)
        buckets: dict[tuple[int, int, int], list[int]] = {}
        for tid in tids:
            h = table[tid]
            buckets.setdefault((int(h["pts"]), int(h["gd"]), int(h["gf"])), []).append(tid)
        ordered: list[int] = []
        for _, bucket in sorted(buckets.items(), key=lambda item: (-item[0][0], -item[0][1], -item[0][2])):
            if len(bucket) == 1:
                ordered.extend(bucket)
            elif len(bucket) < len(tids):
                ordered.extend(rank_tied(bucket))
            else:
                ordered.extend(sorted(bucket, key=total_key))
        return ordered

    by_points: dict[int, list[int]] = {}
    for tid, s in stats.items():
        by_points.setdefault(int(s["pts"]), []).append(tid)
    ranked_ids: list[int] = []
    for points in sorted(by_points, reverse=True):
        tied = by_points[points]
        if len(tied) == 1:
            ranked_ids.extend(tied)
        else:
            ranked_ids.extend(rank_tied(tied))
    return [(tid, stats[tid]["name"]) for tid in ranked_ids]


def _third_place_ranking(con: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """Return the eight best 3rd-placed teams as (team_id, name, group_letter)."""
    thirds: list[tuple[int, str, str, dict]] = []
    for letter in "ABCDEFGHIJKL":
        standings = _group_standings(con, letter)
        if len(standings) >= 3:
            tid, name = standings[2]
            row = con.execute(
                "SELECT SUM(CASE WHEN m.team_a_id=? THEN mr.goals_a ELSE mr.goals_b END) gf, "
                "  SUM(CASE WHEN m.team_a_id=? THEN mr.goals_a - mr.goals_b "
                "       ELSE mr.goals_b - mr.goals_a END) gd, "
                "  SUM(CASE WHEN (m.team_a_id=? AND mr.goals_a>mr.goals_b) "
                "       OR (m.team_b_id=? AND mr.goals_b>mr.goals_a) THEN 3 "
                "       WHEN mr.goals_a=mr.goals_b THEN 1 ELSE 0 END) pts "
                "FROM matches m JOIN match_results mr ON mr.match_id=m.id "
                "WHERE m.competition=? AND m.stage LIKE ? "
                "  AND (m.team_a_id=? OR m.team_b_id=?)",
                (tid, tid, tid, tid, COMPETITION, f"Group stage - Group {letter}", tid, tid),
            ).fetchone()
            thirds.append((tid, name, letter,
                           {"pts": row["pts"] or 0, "gd": row["gd"] or 0, "gf": row["gf"] or 0}))
    thirds.sort(key=lambda x: (-x[3]["pts"], -x[3]["gd"], -x[3]["gf"]))
    return [(tid, name, letter) for tid, name, letter, _ in thirds[:8]]


# ---- Slot resolution ---------------------------------------------------------


def _parse_third_allowed(source: str) -> set[str] | None:
    """Parse the FIFA-style third-place token like ``3{ABCDF}`` and return
    the set of allowed source groups (uppercase). Returns ``None`` for any
    token that is not in that format."""
    s = source.strip()
    if s.startswith("3{") and s.endswith("}"):
        return {ch.upper() for ch in s[2:-1] if ch.isalpha()}
    return None


def _assign_thirds_annex_c(
    con: sqlite3.Connection, slots: list[BracketSlot]
) -> dict[str, int]:
    """Bipartite assignment of the 8 qualified third-placed teams to the 8
    slots that need one. Each slot has an allowed-groups set baked into its
    source token (FIFA Annex C is equivalent to a backtracking search that
    respects the allowed sets and the "no two teams from the same group"
    constraint — already implicit because each allowed set excludes the
    rival's own group).

    Returns ``{slot_id: team_id}`` for slots that could be assigned, or an
    empty dict if any prerequisite is missing (not enough qualified thirds,
    no valid matching, etc).
    """
    third_slots: list[tuple[str, set[str]]] = []
    for slot in slots:
        for source in (slot.home_source, slot.away_source):
            allowed = _parse_third_allowed(source)
            if allowed is not None:
                third_slots.append((slot.slot_id, allowed))
                break
    if not third_slots:
        return {}
    qualified = _third_place_ranking(con)
    if len(qualified) < 8:
        return {}
    # Top-8 by ranking are the qualifiers.
    qualified = qualified[:8]
    by_group: dict[str, tuple[int, str]] = {letter: (tid, name) for tid, name, letter in qualified}
    qualified_groups = list(by_group.keys())

    assignment: dict[str, int] = {}

    def backtrack(idx: int, used: set[str]) -> bool:
        if idx == len(third_slots):
            return True
        slot_id, allowed = third_slots[idx]
        for group in qualified_groups:
            if group in used or group not in allowed:
                continue
            assignment[slot_id] = int(by_group[group][0])
            used.add(group)
            if backtrack(idx + 1, used):
                return True
            used.discard(group)
            del assignment[slot_id]
        return False

    return assignment if backtrack(0, set()) else {}


def _resolve_source(
    con: sqlite3.Connection,
    source: str,
    slots_by_id: dict[str, BracketSlot],
    third_assignment: dict[str, int] | None = None,
    slot_id: str | None = None,
) -> int | None:
    """Translate a `home_source` / `away_source` token into a concrete team_id."""
    source = source.strip()
    # Group position: "1A" .. "4L"
    if len(source) == 2 and source[0].isdigit() and source[1].isalpha():
        position = int(source[0])
        group_letter = source[1].upper()
        standings = _group_standings(con, group_letter)
        if len(standings) >= position:
            return int(standings[position - 1][0])
        return None
    # Third-place from a restricted set: "3{ABCDF}" — resolved via the
    # Annex-C bipartite assignment computed once per resolution pass.
    if _parse_third_allowed(source) is not None:
        if third_assignment and slot_id is not None:
            return third_assignment.get(slot_id)
        return None
    # Knockout winner / loser: "W:R32-1" or "L:SF-1".
    if ":" in source:
        kind, ref_slot = source.split(":", 1)
        slot = slots_by_id.get(ref_slot.strip())
        if slot is None or slot.match_id is None:
            return None
        result = con.execute(
            "SELECT m.team_a_id, m.team_b_id, m.status, mr.goals_a, mr.goals_b, "
            "  mr.extra_time_team_a_goals, mr.extra_time_team_b_goals, "
            "  mr.penalty_team_a, mr.penalty_team_b "
            "FROM matches m LEFT JOIN match_results mr ON mr.match_id=m.id "
            "WHERE m.id=?",
            (int(slot.match_id),),
        ).fetchone()
        if result is None or result["status"] != "finished":
            return None
        winner_id = _decide_winner(result)
        if winner_id is None:
            return None
        if kind.upper() == "W":
            return winner_id
        else:
            return (int(result["team_a_id"]) if winner_id == int(result["team_b_id"])
                    else int(result["team_b_id"]))
    return None


def _decide_winner(row: sqlite3.Row) -> int | None:
    """Decide the advancing team from a finished match result row."""
    ga = row["goals_a"]
    gb = row["goals_b"]
    if ga is None or gb is None:
        return None
    eta = row["extra_time_team_a_goals"] or 0
    etb = row["extra_time_team_b_goals"] or 0
    total_a = int(ga) + int(eta)
    total_b = int(gb) + int(etb)
    if total_a != total_b:
        return int(row["team_a_id"]) if total_a > total_b else int(row["team_b_id"])
    pa = row["penalty_team_a"] or 0
    pb = row["penalty_team_b"] or 0
    if pa != pb:
        return int(row["team_a_id"]) if pa > pb else int(row["team_b_id"])
    return None


# ---- Public entry point ------------------------------------------------------


def resolve_knockout_bracket(repo: Repository, now: datetime | None = None) -> dict:
    """Walk the bracket, fill team_ids and create matches rows where ready.

    Idempotent: re-run anytime. Returns a small summary of changes.
    """
    now = now or datetime.now(timezone.utc)
    summary = {"resolved": 0, "matches_created": 0, "matches_updated": 0}
    slots = list_bracket_slots(repo)
    slots_by_id = {slot.slot_id: slot for slot in slots}

    with sqlite3.connect(repo.path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        _ensure_schema(con)
        # Compute the Annex-C assignment once per pass (requires all 12 groups
        # finished). Empty dict means "not ready" and 3{...} sources stay unresolved.
        third_assignment = _assign_thirds_annex_c(con, slots)
        # Iterate stages in order so winners are known before resolving the next round.
        for stage in STAGES:
            for slot in [s for s in slots if s.stage == stage]:
                home_id = slot.home_team_id or _resolve_source(
                    con, slot.home_source, slots_by_id, third_assignment, slot.slot_id,
                )
                away_id = slot.away_team_id or _resolve_source(
                    con, slot.away_source, slots_by_id, third_assignment, slot.slot_id,
                )
                if home_id is None or away_id is None:
                    continue
                if (home_id, away_id) == (slot.home_team_id, slot.away_team_id) and slot.match_id is not None:
                    continue
                match_id = slot.match_id
                if match_id is None:
                    # Try to find a previously-created match for this slot first
                    # (idempotency across reruns / stale slot rows).
                    existing = con.execute(
                        "SELECT id FROM matches WHERE competition=? AND stage=? "
                        "AND kickoff_utc=? AND team_a_id=? AND team_b_id=?",
                        (COMPETITION, slot.stage, slot.kickoff_utc, home_id, away_id),
                    ).fetchone()
                    if existing is not None:
                        match_id = int(existing["id"])
                        summary["matches_updated"] += 1
                    else:
                        cursor = con.execute(
                            "INSERT INTO matches(competition, stage, kickoff_utc, "
                            "team_a_id, team_b_id, status, venue, neutral_site) "
                            "VALUES(?, ?, ?, ?, ?, 'scheduled', ?, 1)",
                            (COMPETITION, slot.stage, slot.kickoff_utc, home_id, away_id, slot.venue),
                        )
                        match_id = int(cursor.lastrowid)
                        summary["matches_created"] += 1
                else:
                    # Slot already pointed at a match; keep teams aligned in case
                    # of a re-resolution (rare but defensive).
                    con.execute(
                        "UPDATE matches SET team_a_id=?, team_b_id=? WHERE id=?",
                        (home_id, away_id, match_id),
                    )
                    summary["matches_updated"] += 1
                con.execute(
                    "UPDATE knockout_bracket SET home_team_id=?, away_team_id=?, "
                    "match_id=?, resolved_at_utc=? WHERE id=?",
                    (home_id, away_id, match_id, now.isoformat(), slot.id),
                )
                summary["resolved"] += 1
                # Refresh cached slot so downstream sources can see this winner.
                slots_by_id[slot.slot_id] = BracketSlot(
                    id=slot.id, stage=slot.stage, slot_id=slot.slot_id,
                    kickoff_utc=slot.kickoff_utc, venue=slot.venue,
                    home_source=slot.home_source, away_source=slot.away_source,
                    home_team_id=home_id, away_team_id=away_id, match_id=match_id,
                )
        con.commit()
    return summary


def bracket_view(repo: Repository) -> list[dict]:
    """Render-friendly view: each slot with stage, slot id, home/away names or
    pending tokens. Used by the UI to draw the bracket table."""
    slots = list_bracket_slots(repo)
    if not slots:
        return []
    with sqlite3.connect(repo.path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        teams = {int(r["id"]): r["name"]
                 for r in con.execute("SELECT id, name FROM teams")}
    def pretty_source(source: str) -> str:
        allowed = _parse_third_allowed(source)
        if allowed is not None:
            return f"3.º de {'/'.join(sorted(allowed))}"
        if ":" in source:
            kind, ref = source.split(":", 1)
            label = "Gan." if kind.upper() == "W" else "Per."
            return f"{label} {ref}"
        return source

    view = []
    for slot in slots:
        view.append({
            "stage": slot.stage,
            "slot_id": slot.slot_id,
            "kickoff_utc": slot.kickoff_utc,
            "venue": slot.venue or "",
            "home": teams.get(slot.home_team_id) if slot.home_team_id else pretty_source(slot.home_source),
            "away": teams.get(slot.away_team_id) if slot.away_team_id else pretty_source(slot.away_source),
            "home_pending": slot.home_team_id is None,
            "away_pending": slot.away_team_id is None,
            "match_id": slot.match_id,
        })
    return view
