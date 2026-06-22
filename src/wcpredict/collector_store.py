from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from contextlib import closing
import json
import sqlite3

from wcpredict.names import canonical_team_name, same_team
from wcpredict.ratings import MatchResult


@dataclass(frozen=True)
class CollectorEventBundle:
    event_id: int
    canonical_key: str
    team_a: str
    team_b: str
    start_time_utc: datetime
    status: str
    venue: str | None
    result: dict
    updated_at_utc: datetime
    statistics: list[dict]
    lineups: list[dict]
    availability: list[dict]
    sources: list[dict]
    missing_critical: list[str]
    missing_optional: list[str]

    @property
    def completeness_status(self) -> str:
        return "incomplete" if self.missing_critical else "complete"


class CollectorStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def find_event(
        self,
        team_a: str,
        team_b: str,
        event_date: date,
    ) -> CollectorEventBundle | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT e.*, p1.canonical_name AS team_a_name, "
                "p2.canonical_name AS team_b_name "
                "FROM events e "
                "JOIN participants p1 ON p1.id=e.participant1_id "
                "JOIN participants p2 ON p2.id=e.participant2_id "
                "WHERE substr(e.start_time_utc, 1, 10) BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
                "ORDER BY e.updated_at_utc DESC",
                (event_date.isoformat(), event_date.isoformat()),
            ).fetchall()
            event = next(
                (
                    row
                    for row in rows
                    if same_team(row["team_a_name"], team_a)
                    and same_team(row["team_b_name"], team_b)
                ),
                None,
            )
            if event is None:
                return None
            event_id = int(event["id"])
            statistics = self._rows(
                connection,
                "SELECT s.*, p.canonical_name AS subject_name, "
                "src.retrieved_at_utc AS source_retrieved_at_utc, src.status AS source_status, "
                "src.confidence AS source_confidence, src.source_url "
                "FROM statistics s "
                "LEFT JOIN participants p ON p.id=s.subject_id "
                "LEFT JOIN sources src ON src.id=s.source_id "
                "WHERE s.event_id=? ORDER BY s.subject_type, s.subject_id, s.metric",
                event_id,
            )
            lineups = self._rows(
                connection,
                "SELECT l.*, team.canonical_name AS team_name, player.canonical_name AS player_name "
                "FROM lineups l "
                "JOIN participants team ON team.id=l.participant_id "
                "JOIN participants player ON player.id=l.player_id "
                "WHERE l.event_id=? ORDER BY team_name, player_name",
                event_id,
            )
            availability = self._rows(
                connection,
                "SELECT a.*, p.canonical_name AS subject_name "
                "FROM availability a JOIN participants p ON p.id=a.participant_id "
                "WHERE a.event_id=? ORDER BY subject_name",
                event_id,
            )
            source_ids = sorted(
                {
                    str(row["source_id"])
                    for row in statistics + lineups + availability
                    if row.get("source_id")
                }
            )
            sources = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                sources = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM sources WHERE id IN ({placeholders}) ORDER BY retrieved_at_utc DESC",
                        source_ids,
                    ).fetchall()
                ]

        missing_critical = [] if statistics else ["team_statistics"]
        missing_optional = []
        if not lineups:
            missing_optional.append("players")
        if not availability:
            missing_optional.append("availability")
        return CollectorEventBundle(
            event_id=event_id,
            canonical_key=str(event["canonical_key"]),
            team_a=canonical_team_name(str(event["team_a_name"])),
            team_b=canonical_team_name(str(event["team_b_name"])),
            start_time_utc=datetime.fromisoformat(str(event["start_time_utc"])),
            status=str(event["status"]),
            venue=event["venue"],
            result=json.loads(event["result_json"] or "{}"),
            updated_at_utc=datetime.fromisoformat(str(event["updated_at_utc"])),
            statistics=statistics,
            lineups=lineups,
            availability=availability,
            sources=sources,
            missing_critical=missing_critical,
            missing_optional=missing_optional,
        )

    def list_finished_results(self, as_of: date | datetime) -> list[MatchResult]:
        if not self.path.exists():
            return []
        with closing(self._connect()) as connection:
            boundary = (
                datetime.combine(as_of, time.min, tzinfo=timezone.utc)
                if isinstance(as_of, date) and not isinstance(as_of, datetime)
                else as_of
            )
            rows = connection.execute(
                "SELECT e.canonical_key, e.start_time_utc, e.result_json, "
                "p1.canonical_name AS team_a_name, p2.canonical_name AS team_b_name "
                "FROM events e "
                "JOIN participants p1 ON p1.id=e.participant1_id "
                "JOIN participants p2 ON p2.id=e.participant2_id "
                "WHERE e.status='finished' AND e.start_time_utc < ? "
                "ORDER BY e.start_time_utc",
                (boundary.isoformat(),),
            ).fetchall()
        results = []
        for row in rows:
            payload = json.loads(row["result_json"] or "{}")
            goals_a = payload.get("home", payload.get("participant1"))
            goals_b = payload.get("away", payload.get("participant2"))
            if goals_a is None or goals_b is None:
                continue
            results.append(
                MatchResult(
                    played_on=datetime.fromisoformat(row["start_time_utc"]).date(),
                    team_a=canonical_team_name(row["team_a_name"]),
                    team_b=canonical_team_name(row["team_b_name"]),
                    goals_a=int(goals_a),
                    goals_b=int(goals_b),
                    match_type="world_cup" if "world cup" in row["canonical_key"] else "competitive",
                )
            )
        return results

    @staticmethod
    def _rows(
        connection: sqlite3.Connection,
        query: str,
        event_id: int,
    ) -> list[dict]:
        return [dict(row) for row in connection.execute(query, (event_id,)).fetchall()]
