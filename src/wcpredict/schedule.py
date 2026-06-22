from datetime import datetime, timezone
from pathlib import Path
import csv
import sqlite3

from wcpredict.names import canonical_team_name, same_team
from wcpredict.repository import Repository


def load_schedule_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["label"] = f"{row['team_a']} vs {row['team_b']}"
        row["kickoff_utc"] = (
            (row.get("kickoff_utc") or "").strip()
            or datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc).isoformat()
        )
    return rows


def seed_schedule(repo: Repository, path: Path) -> list[int]:
    match_ids: list[int] = []
    for row in load_schedule_csv(path):
        team_a_id = repo.upsert_team(row["team_a"])
        team_b_id = repo.upsert_team(row["team_b"])
        kickoff = datetime.fromisoformat(row["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        # If a match with the same teams already exists on the same date but
        # with a generic 12:00 UTC kickoff (from martj42), update its time.
        _fix_kickoff_for_existing(repo, row["team_a"], row["team_b"], kickoff)
        match_id = repo.upsert_match(
                competition="FIFA World Cup 2026",
                stage=f"{row['stage']} - Group {row['group']}",
                kickoff_utc=kickoff,
                team_a_id=team_a_id,
                team_b_id=team_b_id,
                status=row["status"],
                venue=row["venue"],
            )
        repo.remove_empty_scheduled_duplicates(
            "FIFA World Cup 2026", team_a_id, team_b_id, match_id
        )
        match_ids.append(match_id)
    return match_ids


def _fix_kickoff_for_existing(
    repo: Repository, team_a: str, team_b: str, real_kickoff: datetime
) -> None:
    """If a match exists on a nearby date with a placeholder 12:00/00:00 UTC kickoff
    (typical of date-only imports), update it to the real time so the data attached
    to it is preserved and ON CONFLICT in upsert_match merges into the same row.
    If updating would conflict (real-time row exists), move data over and delete."""
    from datetime import timedelta
    date = real_kickoff.date()
    date_patterns = [
        (date - timedelta(days=1)).isoformat(),
        date.isoformat(),
        (date + timedelta(days=1)).isoformat(),
    ]
    with sqlite3.connect(repo.path, timeout=10) as con:
        con.row_factory = sqlite3.Row
        candidates = con.execute(
            "SELECT m.id, m.kickoff_utc, ta.name AS ta, tb.name AS tb "
            "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
            "WHERE m.competition='FIFA World Cup 2026' "
            "AND (m.kickoff_utc LIKE '%T12:00:00%' OR m.kickoff_utc LIKE '%T00:00:00%')",
        ).fetchall()
        for cand in candidates:
            cand_date = cand["kickoff_utc"][:10]
            if cand_date not in date_patterns:
                continue
            if not ((same_team(cand["ta"], team_a) and same_team(cand["tb"], team_b)) or
                    (same_team(cand["ta"], team_b) and same_team(cand["tb"], team_a))):
                continue
            try:
                con.execute(
                    "UPDATE matches SET kickoff_utc=? WHERE id=?",
                    (real_kickoff.isoformat(), cand["id"]),
                )
                con.commit()
            except sqlite3.IntegrityError:
                # Another row already has the real kickoff; merge data into it.
                target = con.execute(
                    "SELECT id FROM matches WHERE competition='FIFA World Cup 2026' "
                    "AND kickoff_utc=? AND ((team_a_id=(SELECT id FROM teams WHERE name=?) "
                    "AND team_b_id=(SELECT id FROM teams WHERE name=?)) OR "
                    "(team_a_id=(SELECT id FROM teams WHERE name=?) AND team_b_id=(SELECT id FROM teams WHERE name=?)))",
                    (real_kickoff.isoformat(), cand["ta"], cand["tb"], cand["tb"], cand["ta"]),
                ).fetchone()
                if target:
                    _move_match_data(con, cand["id"], target["id"])
                con.execute("DELETE FROM matches WHERE id=?", (cand["id"],))
                con.commit()
            return


def _move_match_data(con: sqlite3.Connection, old_id: int, new_id: int) -> None:
    """Move all data (results, stats, observations, predictions, odds, import_runs)
    from old_id to new_id before deleting old_id."""
    tables = [
        "match_results", "team_match_stats", "observations",
        "predictions", "manual_odds", "import_runs",
    ]
    for table in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
        if "match_id" not in cols:
            continue
        try:
            con.execute(f"UPDATE OR IGNORE {table} SET match_id=? WHERE match_id=?", (new_id, old_id))
            con.execute(f"DELETE FROM {table} WHERE match_id=?", (old_id,))
        except sqlite3.OperationalError:
            pass
