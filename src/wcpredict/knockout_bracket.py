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
from itertools import product
from pathlib import Path
from typing import Iterable

from wcpredict.repository import Repository

COMPETITION = "FIFA World Cup 2026"

# Optional override: if this CSV is filled with FIFA's official Annex C
# matrix, the bipartite solver below is bypassed and the lookup is used
# instead. Format documented in the file itself.
_ANNEX_C_CSV = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "world_cup_2026_annex_c.csv"

# Group winners that face a third-placed team (in slot order: M74, M77,
# M79, M80, M81, M82, M85, M87 → 1E, 1I, 1A, 1L, 1D, 1G, 1B, 1K).
_THIRD_FACING_WINNERS = ("1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L")

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


def _group_position_clinched(
    con: sqlite3.Connection, group_letter: str, position: int
) -> tuple[int, str] | None:
    """Return a mathematically fixed group position before the group is complete.

    Conservative by design. Today we only early-resolve a group winner when
    every remaining win/draw/loss combination still leaves the same team top
    on points or strict head-to-head points. Runner-up and third-place
    positions still wait for the group table, because goal difference and
    third-place cross-group rules can change late.
    """
    if position != 1:
        return None
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
        return None

    stats: dict[int, dict] = {}
    finished_games: list[tuple[int, int, int, int]] = []
    unfinished_games: list[tuple[int, int]] = []
    unfinished = 0
    for r in rows:
        home_id = int(r["team_a_id"])
        away_id = int(r["team_b_id"])
        stats.setdefault(home_id, {"name": r["ta"], "pts": 0})
        stats.setdefault(away_id, {"name": r["tb"], "pts": 0})
        if r["status"] == "finished" and r["goals_a"] is not None and r["goals_b"] is not None:
            ga = int(r["goals_a"])
            gb = int(r["goals_b"])
            finished_games.append((home_id, away_id, ga, gb))
            if ga > gb:
                stats[home_id]["pts"] += 3
            elif gb > ga:
                stats[away_id]["pts"] += 3
            else:
                stats[home_id]["pts"] += 1
                stats[away_id]["pts"] += 1
        else:
            unfinished += 1
            unfinished_games.append((home_id, away_id))
    if unfinished == 0:
        standings = _group_standings(con, group_letter)
        return standings[0] if standings else None

    def strictly_first_in_scenario(
        team_id: int,
        points: dict[int, int],
        games: list[tuple[int, int, int, int]],
    ) -> bool:
        max_points = max(points.values())
        tied_top = [tid for tid, pts in points.items() if pts == max_points]
        if team_id not in tied_top:
            return False
        if len(tied_top) == 1:
            return True

        tied = set(tied_top)
        h2h_points = {tid: 0 for tid in tied_top}
        for home_id, away_id, ga, gb in games:
            if home_id not in tied or away_id not in tied:
                continue
            if ga > gb:
                h2h_points[home_id] += 3
            elif gb > ga:
                h2h_points[away_id] += 3
            else:
                h2h_points[home_id] += 1
                h2h_points[away_id] += 1

        team_h2h = h2h_points[team_id]
        return all(team_h2h > pts for tid, pts in h2h_points.items() if tid != team_id)

    for team_id, values in stats.items():
        always_first = True
        # Score margins are intentionally minimal. This early-resolution path
        # only trusts points and strict head-to-head points; if a scenario
        # would require goal difference or goals scored, it remains pending.
        for outcomes in product(((3, 0, 1, 0), (1, 1, 0, 0), (0, 3, 0, 1)), repeat=len(unfinished_games)):
            scenario_points = {tid: int(s["pts"]) for tid, s in stats.items()}
            scenario_games = list(finished_games)
            for (home_id, away_id), (home_pts, away_pts, ga, gb) in zip(unfinished_games, outcomes):
                scenario_points[home_id] += home_pts
                scenario_points[away_id] += away_pts
                scenario_games.append((home_id, away_id, ga, gb))
            if not strictly_first_in_scenario(team_id, scenario_points, scenario_games):
                always_first = False
                break
        if always_first:
            return team_id, str(values["name"])
    return None


# ---- Slot resolution ---------------------------------------------------------


def _parse_third_allowed(source: str) -> set[str] | None:
    """Parse the FIFA-style third-place token like ``3{ABCDF}`` and return
    the set of allowed source groups (uppercase). Returns ``None`` for any
    token that is not in that format."""
    s = source.strip()
    if s.startswith("3{") and s.endswith("}"):
        return {ch.upper() for ch in s[2:-1] if ch.isalpha()}
    return None


def _load_annex_c_table() -> dict[frozenset[str], dict[str, str]]:
    """Parse the optional Annex C CSV into ``{frozenset(thirds_combo): {1A: groupX, ...}}``.

    Returns an empty dict if the file is missing, empty (only the header)
    or any row is malformed (skipped silently to avoid breaking the app
    on a partial file). Each key is the 8-group combination of third-place
    qualifiers; each value maps every winner slot to the source group of
    its third-place opponent.
    """
    if not _ANNEX_C_CSV.exists():
        return {}
    try:
        rows = list(csv.DictReader(
            (line for line in _ANNEX_C_CSV.read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#")),
        ))
    except Exception:
        return {}
    table: dict[frozenset[str], dict[str, str]] = {}
    for row in rows:
        combo_raw = (row.get("thirds_combo") or "").strip().upper()
        if len(combo_raw) != 8 or not combo_raw.isalpha():
            continue
        combo = frozenset(combo_raw)
        if len(combo) != 8:
            continue
        mapping: dict[str, str] = {}
        ok = True
        for winner_slot in _THIRD_FACING_WINNERS:
            group_letter = (row.get(winner_slot) or "").strip().upper()
            if len(group_letter) != 1 or group_letter not in combo:
                ok = False
                break
            mapping[winner_slot] = group_letter
        if not ok:
            continue
        # Sanity: each third-group must be used exactly once.
        if len(set(mapping.values())) != 8:
            continue
        table[combo] = mapping
    return table


# Slot to winner mapping (M74 → 1E, etc.) — used both ways: from the
# bracket slot ids to the corresponding "1X" code and vice versa.
_THIRD_SLOTS_BY_WINNER = {
    "M74": "1E", "M77": "1I", "M79": "1A", "M80": "1L",
    "M81": "1D", "M82": "1G", "M85": "1B", "M87": "1K",
}


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
    empty dict if any prerequisite is missing (need ALL 12 groups closed,
    no valid matching, etc).

    IMPORTANT: this only fires when the full 12 groups are finished. With
    fewer groups closed the "best 8 thirds" cannot be known — assigning
    early would put a team into the bracket that ends up cut once the
    remaining 4 third-places land (Uruguay-vs-Korea-style mistake the user
    pointed out). Returning {} keeps the slots as ``3.º de A/B/...`` until
    the picture is fully resolved.
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
    # Need every single group of 4 to be settled before we can decide who
    # the 8 best 3rd-placed teams are.
    finished_groups = 0
    for letter in "ABCDEFGHIJKL":
        standings = _group_standings(con, letter)
        if len(standings) >= 4:
            finished_groups += 1
    if finished_groups < 12:
        return {}
    qualified = _third_place_ranking(con)
    if len(qualified) < 8:
        return {}
    # Top-8 by ranking are the qualifiers.
    qualified = qualified[:8]
    by_group: dict[str, tuple[int, str]] = {letter: (tid, name) for tid, name, letter in qualified}
    qualified_groups = list(by_group.keys())

    # Preferred path: official FIFA Annex C lookup. The CSV is keyed by
    # the alphabetically-sorted 8-group combination; for each combo it
    # declares which third faces which group-winner slot. Falls through
    # to the bipartite solver if the file is empty / does not contain
    # this combination.
    table = _load_annex_c_table()
    combo_key = frozenset(qualified_groups)
    official = table.get(combo_key)
    if official is not None:
        # Translate ``{1A: group_letter}`` into the bracket's slot_id keys.
        winner_to_slot = {v: k for k, v in _THIRD_SLOTS_BY_WINNER.items()}
        out: dict[str, int] = {}
        for winner, third_group in official.items():
            slot_id = winner_to_slot.get(winner)
            if slot_id and third_group in by_group:
                out[slot_id] = int(by_group[third_group][0])
        if len(out) == 8:
            return out

    # MRV (Minimum Remaining Values) backtracking:
    # at each step we expand the slot with the fewest currently-feasible
    # thirds. This collapses cases like 3K → 1L (only one valid champion)
    # in the first step and dramatically reduces the chance of picking a
    # valid-but-non-official pairing when multiple solutions exist.
    assignment: dict[str, int] = {}
    qualified_set = set(qualified_groups)

    def feasible_groups(slot_idx: int, used: set[str]) -> list[str]:
        _slot_id, allowed = third_slots[slot_idx]
        return [g for g in qualified_groups
                if g in allowed and g not in used]

    def backtrack(remaining: list[int], used: set[str]) -> bool:
        if not remaining:
            return True
        # MRV: pick the index whose feasible-groups list is shortest.
        best_pos = 0
        best_options: list[str] = feasible_groups(remaining[0], used)
        for pos in range(1, len(remaining)):
            opts = feasible_groups(remaining[pos], used)
            if len(opts) < len(best_options):
                best_pos = pos
                best_options = opts
                if not best_options:
                    return False
        chosen_idx = remaining[best_pos]
        slot_id, _allowed = third_slots[chosen_idx]
        next_remaining = remaining[:best_pos] + remaining[best_pos + 1:]
        for group in best_options:
            assignment[slot_id] = int(by_group[group][0])
            used.add(group)
            if backtrack(next_remaining, used):
                return True
            used.discard(group)
            del assignment[slot_id]
        return False

    if not backtrack(list(range(len(third_slots))), set()):
        return {}
    return assignment


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
        clinched = _group_position_clinched(con, group_letter, position)
        if clinched is not None:
            return int(clinched[0])
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
                def resolved_side(existing_id: int | None, source: str) -> int | None:
                    # Third-place assignment is table-driven and may be
                    # corrected after a provisional/fallback resolution. It
                    # must therefore be recalculated even when an id is already
                    # persisted; other source types retain their stable id.
                    if _parse_third_allowed(source) is not None:
                        return _resolve_source(
                            con, source, slots_by_id, third_assignment, slot.slot_id,
                        )
                    return existing_id or _resolve_source(
                        con, source, slots_by_id, third_assignment, slot.slot_id,
                    )

                home_id = resolved_side(slot.home_team_id, slot.home_source)
                away_id = resolved_side(slot.away_team_id, slot.away_source)
                side_changed = (
                    home_id != slot.home_team_id
                    or away_id != slot.away_team_id
                )
                if side_changed:
                    con.execute(
                        "UPDATE knockout_bracket SET home_team_id=?, away_team_id=?, "
                        "resolved_at_utc=? WHERE id=?",
                        (home_id, away_id, now.isoformat(), slot.id),
                    )
                    summary["resolved"] += 1
                    slot = BracketSlot(
                        id=slot.id, stage=slot.stage, slot_id=slot.slot_id,
                        kickoff_utc=slot.kickoff_utc, venue=slot.venue,
                        home_source=slot.home_source, away_source=slot.away_source,
                        home_team_id=home_id, away_team_id=away_id,
                        match_id=slot.match_id,
                    )
                    slots_by_id[slot.slot_id] = slot
                if home_id is None or away_id is None:
                    continue
                if slot.match_id is not None:
                    # Daily schedule providers sometimes overwrite ``stage``
                    # with the competition label. The bracket is authoritative
                    # for knockout metadata, so repair it on every resolution
                    # pass even when the linked teams have not changed.
                    con.execute(
                        "UPDATE matches SET competition=?, stage=?, kickoff_utc=?, "
                        "venue=COALESCE(?, venue), neutral_site=1 WHERE id=?",
                        (
                            COMPETITION,
                            slot.stage,
                            slot.kickoff_utc,
                            slot.venue,
                            slot.match_id,
                        ),
                    )
                    if side_changed:
                        con.execute(
                            "UPDATE matches SET team_a_id=?, team_b_id=? WHERE id=?",
                            (home_id, away_id, slot.match_id),
                        )
                        summary["matches_updated"] += 1
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
                if not side_changed:
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
    with sqlite3.connect(repo.path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        slots_by_id = {slot.slot_id: slot for slot in slots}
        third_assignment = _assign_thirds_annex_c(con, slots)
        teams = {int(r["id"]): r["name"]
                 for r in con.execute("SELECT id, name FROM teams")}
        for slot in slots:
            home_id = slot.home_team_id or _resolve_source(
                con, slot.home_source, slots_by_id, third_assignment, slot.slot_id
            )
            away_id = slot.away_team_id or _resolve_source(
                con, slot.away_source, slots_by_id, third_assignment, slot.slot_id
            )
            home_name = teams.get(home_id) if home_id else None
            away_name = teams.get(away_id) if away_id else None
            view.append({
                "stage": slot.stage,
                "slot_id": slot.slot_id,
                "kickoff_utc": slot.kickoff_utc,
                "venue": slot.venue or "",
                "home": home_name or pretty_source(slot.home_source),
                "away": away_name or pretty_source(slot.away_source),
                "home_pending": home_name is None,
                "away_pending": away_name is None,
                "match_id": slot.match_id,
            })
    return view
