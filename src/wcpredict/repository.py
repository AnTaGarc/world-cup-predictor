from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
import json
import sqlite3
from typing import Any

from wcpredict.collector_store import CollectorEventBundle
from wcpredict.database import initialize_database
from wcpredict.models import Match, Team
from wcpredict.backtesting import brier_score
from wcpredict.settlement import prediction_occurred
from wcpredict.outcome_ml import build_training_rows, save_outcome_model, train_outcome_model
from wcpredict.ratings import MatchResult
from wcpredict.review import CandidateDecision, ensure_batch_finalizable, normalized_review_value
from wcpredict.source_catalog import SourceDefinition
from wcpredict.names import canonical_team_name, same_team
from wcpredict.deep_match_import import DeepImportResult, DeepMatchCollection, flatten_team_metrics


def _match_by_teams_near_date(scheduled, team_a: str, team_b: str, played_at: str):
    """Find an existing scheduled match by team pair within ±1 day of played_at.

    Why: upstream providers send a date-only field that may be off-by-one from the
    canonical UTC kickoff (e.g. a US night match sits on the next UTC day). Without
    this fuzziness the lookup misses, a duplicate match is inserted, and stats end
    up split across two rows.

    How to apply: any code that needs to attach upstream rows to the CSV-defined
    schedule should call this rather than an exact-date equality check.
    """
    from datetime import datetime, timedelta
    date_part = played_at[:10] if played_at else ""
    candidates = []
    if date_part:
        try:
            base = datetime.fromisoformat(date_part).date()
            allowed = {
                (base - timedelta(days=1)).isoformat(),
                base.isoformat(),
                (base + timedelta(days=1)).isoformat(),
            }
        except ValueError:
            allowed = {date_part}
    else:
        allowed = None
    for match in scheduled:
        if not (same_team(str(match["team_a"]), team_a) and same_team(str(match["team_b"]), team_b)):
            continue
        if allowed is None or str(match["kickoff_utc"])[:10] in allowed:
            candidates.append(match)
    if not candidates:
        return None
    # Prefer the match on the exact provider date; fall back to the first.
    for match in candidates:
        if str(match["kickoff_utc"])[:10] == date_part:
            return match
    return candidates[0]


def _known_fixture_team(value: object) -> bool:
    name = str(value or "").strip().casefold()
    if not name:
        return False
    placeholders = ("tbd", "to be determined", "winner ", "loser ", "1st group", "2nd group", "third place")
    return not any(token in name for token in placeholders)


def _fixture_time_quality(kickoff_utc: str) -> int:
    parsed = datetime.fromisoformat(kickoff_utc)
    return 0 if parsed.hour == 12 and parsed.minute == 0 else 1


class Repository:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        initialize_database(self.path)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 30000")
        return con

    @contextmanager
    def session(self):
        con = self.connect()
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def upsert_team(self, name: str, fifa_code: str | None = None) -> int:
        canonical_name = canonical_team_name(name)
        with self.session() as con:
            con.execute(
                "INSERT INTO teams(name, fifa_code) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET fifa_code=COALESCE(excluded.fifa_code, teams.fifa_code)",
                (canonical_name, fifa_code),
            )
            row = con.execute("SELECT id FROM teams WHERE name = ?", (canonical_name,)).fetchone()
            return int(row["id"])

    def deduplicate_teams(self) -> dict[str, int]:
        """One-off migration: merge teams that share a canonical name.

        Returns a summary {"groups": N, "merged_teams": N, "rewritten_matches": N,
        "rewritten_team_match_stats": N, "rewritten_players": N}.

        For each group of teams that canonicalise to the same name:
          * pick the team with the most match references as the survivor
          * rewrite matches/team_match_stats/players to point to the survivor
          * delete the duplicates
        Conflicts on UNIQUE constraints (e.g. team_match_stats(match_id, team_id)
        when both sides already wrote a row for the same match) are resolved by
        keeping the survivor's row and discarding the duplicate's.
        """
        from collections import defaultdict
        summary = {
            "groups": 0, "merged_teams": 0, "rewritten_matches": 0,
            "rewritten_team_match_stats": 0, "rewritten_players": 0,
        }
        with self.session() as con:
            rows = con.execute("SELECT id, name FROM teams ORDER BY id").fetchall()
            by_canonical: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for row in rows:
                by_canonical[canonical_team_name(row["name"])].append((row["id"], row["name"]))
            for canonical_name, group in by_canonical.items():
                if len(group) <= 1:
                    continue
                # Survivor: most match references; tie-break by lowest id.
                def usage(team_id: int) -> int:
                    return int(con.execute(
                        "SELECT (SELECT COUNT(*) FROM matches WHERE team_a_id=? OR team_b_id=?)"
                        "+ (SELECT COUNT(*) FROM team_match_stats WHERE team_id=?)",
                        (team_id, team_id, team_id),
                    ).fetchone()[0])
                survivor_id, survivor_name = max(group, key=lambda item: (usage(item[0]), -item[0]))
                summary["groups"] += 1
                # Tables that reference matches.id; we must clean these before
                # deleting a match that couldn't be re-written to the survivor.
                match_child_tables = (
                    "import_runs", "imported_lineups", "manual_odds",
                    "match_results", "observations", "player_match_stats",
                    "predictions", "screenshot_batches", "sentiment_snapshots",
                    "settlement_versions", "team_match_stats",
                )

                def detect_table(name: str) -> bool:
                    return bool(con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,),
                    ).fetchone())

                existing_match_children = [t for t in match_child_tables if detect_table(t)]

                for dup_id, dup_name in group:
                    if dup_id == survivor_id:
                        continue
                    # Step 1: rewrite matches where the survivor doesn't already
                    # have a record for that fixture (UNIQUE collision otherwise).
                    rewritten = con.execute(
                        "UPDATE OR IGNORE matches SET team_a_id=? WHERE team_a_id=?",
                        (survivor_id, dup_id),
                    ).rowcount
                    rewritten += con.execute(
                        "UPDATE OR IGNORE matches SET team_b_id=? WHERE team_b_id=?",
                        (survivor_id, dup_id),
                    ).rowcount
                    summary["rewritten_matches"] += int(rewritten)
                    # Step 2: any matches still referencing the duplicate are
                    # already covered by an equivalent survivor fixture. Drop them
                    # along with all their child rows to avoid FK violations.
                    dangling = [
                        int(row[0]) for row in con.execute(
                            "SELECT id FROM matches WHERE team_a_id=? OR team_b_id=?",
                            (dup_id, dup_id),
                        ).fetchall()
                    ]
                    for match_id in dangling:
                        for child in existing_match_children:
                            con.execute(
                                f"DELETE FROM {child} WHERE match_id=?", (match_id,)
                            )
                        con.execute("DELETE FROM matches WHERE id=?", (match_id,))
                    # Step 3: team_match_stats — rewrite where possible, drop the rest.
                    rewritten = con.execute(
                        "UPDATE OR IGNORE team_match_stats SET team_id=? WHERE team_id=?",
                        (survivor_id, dup_id),
                    ).rowcount
                    summary["rewritten_team_match_stats"] += int(rewritten)
                    con.execute("DELETE FROM team_match_stats WHERE team_id=?", (dup_id,))
                    # Step 4: players — rewrite where possible, drop their stats first.
                    orphan_players = [
                        int(row[0]) for row in con.execute(
                            "SELECT id FROM players WHERE team_id=?", (dup_id,)
                        ).fetchall()
                    ]
                    rewritten = con.execute(
                        "UPDATE OR IGNORE players SET team_id=? WHERE team_id=?",
                        (survivor_id, dup_id),
                    ).rowcount
                    summary["rewritten_players"] += int(rewritten)
                    for player_id in orphan_players:
                        if detect_table("player_match_stats"):
                            con.execute(
                                "DELETE FROM player_match_stats WHERE player_id=?",
                                (player_id,),
                            )
                    con.execute("DELETE FROM players WHERE team_id=?", (dup_id,))
                    # Step 5: finally drop the duplicate team row.
                    con.execute("DELETE FROM teams WHERE id=?", (dup_id,))
                    summary["merged_teams"] += 1
                # Step 6: now that dups are gone, rename survivor to the canonical
                # form so future inserts hit the UNIQUE(name) clause cleanly.
                if survivor_name != canonical_name:
                    con.execute(
                        "UPDATE teams SET name = ? WHERE id = ?",
                        (canonical_name, survivor_id),
                    )
        return summary

    def deduplicate_matches(self, *, hours_window: int = 48) -> dict[str, int]:
        """Merge duplicate fixtures: same competition + same canonical team pair +
        kickoff within `hours_window` hours of each other.

        Survivor selection prioritises matches with more attached data
        (results, team_match_stats, observations, import_runs, predictions);
        ties broken by oldest id. Child rows of the loser are transferred to
        the survivor when possible (UPDATE OR IGNORE) and otherwise deleted.

        Returns a summary dict with keys: groups, merged_matches,
        rewritten_children.
        """
        from collections import defaultdict
        summary = {"groups": 0, "merged_matches": 0, "rewritten_children": 0}
        with self.session() as con:
            rows = con.execute(
                "SELECT m.id, m.competition, m.kickoff_utc, m.team_a_id, m.team_b_id, "
                "ta.name AS a, tb.name AS b "
                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id "
                "JOIN teams tb ON tb.id=m.team_b_id "
                "ORDER BY m.kickoff_utc"
            ).fetchall()
            # Group by (competition, sorted canonical team pair).
            groups: dict[tuple, list] = defaultdict(list)
            for row in rows:
                pair = tuple(sorted((row["a"], row["b"])))
                groups[(row["competition"], pair)].append(row)
            # Tables that reference matches.id; child rows must be moved or removed.
            match_child_tables = (
                "import_runs", "imported_lineups", "manual_odds", "match_results",
                "observations", "player_match_stats", "predictions",
                "screenshot_batches", "sentiment_snapshots", "settlement_versions",
                "team_match_stats",
            )

            def detect(name: str) -> bool:
                return bool(con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone())

            existing = [t for t in match_child_tables if detect(t)]

            def usage(match_id: int) -> int:
                total = 0
                for table in existing:
                    column = "prediction_id" if table == "prediction_evaluations" else "match_id"
                    total += int(con.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                        (match_id,),
                    ).fetchone()[0])
                return total

            for (competition, pair), items in groups.items():
                if len(items) <= 1:
                    continue
                # Cluster matches whose kickoff_utc is within `hours_window` hours.
                items.sort(key=lambda r: r["kickoff_utc"])
                clusters: list[list] = []
                current: list = []
                last_dt = None
                for item in items:
                    dt = datetime.fromisoformat(item["kickoff_utc"].replace("Z", "+00:00"))
                    if current and last_dt and abs((dt - last_dt).total_seconds()) > hours_window * 3600:
                        clusters.append(current)
                        current = []
                    current.append(item)
                    last_dt = dt
                if current:
                    clusters.append(current)
                for cluster in clusters:
                    if len(cluster) <= 1:
                        continue
                    summary["groups"] += 1
                    survivor = max(cluster, key=lambda r: (usage(r["id"]), -int(r["id"])))
                    for losing in cluster:
                        if losing["id"] == survivor["id"]:
                            continue
                        # Move children: prefer UPDATE OR IGNORE to avoid clobbering
                        # survivor rows on UNIQUE constraints, then drop the rest.
                        for table in existing:
                            rewritten = con.execute(
                                f"UPDATE OR IGNORE {table} SET match_id=? WHERE match_id=?",
                                (survivor["id"], losing["id"]),
                            ).rowcount
                            summary["rewritten_children"] += int(rewritten)
                            con.execute(
                                f"DELETE FROM {table} WHERE match_id=?",
                                (losing["id"],),
                            )
                        con.execute("DELETE FROM matches WHERE id=?", (losing["id"],))
                        summary["merged_matches"] += 1
        return summary

    def upsert_match(
        self,
        competition: str,
        stage: str,
        kickoff_utc: datetime,
        team_a_id: int,
        team_b_id: int,
        status: str,
        venue: str | None = None,
        neutral_site: bool = True,
    ) -> int:
        kickoff = kickoff_utc.isoformat()
        with self.session() as con:
            con.execute(
                "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(competition, kickoff_utc, team_a_id, team_b_id) DO UPDATE SET "
                "stage=excluded.stage, "
                "status=CASE "
                "WHEN matches.status='finished' OR EXISTS(SELECT 1 FROM match_results r WHERE r.match_id=matches.id) THEN 'finished' "
                "ELSE excluded.status END, "
                "venue=excluded.venue, neutral_site=excluded.neutral_site",
                (competition, stage, kickoff, team_a_id, team_b_id, status, venue, int(neutral_site)),
            )
            row = con.execute(
                "SELECT id FROM matches WHERE competition=? AND kickoff_utc=? AND team_a_id=? AND team_b_id=?",
                (competition, kickoff, team_a_id, team_b_id),
            ).fetchone()
            return int(row["id"])

    def remove_empty_scheduled_duplicates(
        self,
        competition: str,
        team_a_id: int,
        team_b_id: int,
        keep_match_id: int,
    ) -> None:
        with self.session() as con:
            # Remove exact team_id matches (same order).
            con.execute(
                "DELETE FROM matches WHERE competition=? AND team_a_id=? AND team_b_id=? "
                "AND status='scheduled' AND id<>? "
                "AND NOT EXISTS(SELECT 1 FROM manual_odds o WHERE o.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM predictions p WHERE p.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM team_match_stats t WHERE t.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM player_match_stats ps WHERE ps.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM import_runs i WHERE i.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM observations ob WHERE ob.match_id=matches.id) "
                "AND NOT EXISTS(SELECT 1 FROM imported_lineups il WHERE il.match_id=matches.id)",
                (competition, team_a_id, team_b_id, keep_match_id),
            )
            # Also remove matches with same canonical team names (aliases like
            # "Korea Republic" vs "South Korea") that are empty duplicates.
            keep_row = con.execute(
                "SELECT ta.name AS team_a, tb.name AS team_b "
                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                "WHERE m.id=?", (keep_match_id,)
            ).fetchone()
            if keep_row:
                canon_a = canonical_team_name(keep_row["team_a"])
                canon_b = canonical_team_name(keep_row["team_b"])
                candidates = con.execute(
                    "SELECT m.id, ta.name AS team_a, tb.name AS team_b "
                    "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                    "WHERE m.competition=? AND m.status='scheduled' AND m.id<>? "
                    "AND NOT EXISTS(SELECT 1 FROM match_results r WHERE r.match_id=m.id) "
                    "AND NOT EXISTS(SELECT 1 FROM manual_odds o WHERE o.match_id=m.id) "
                    "AND NOT EXISTS(SELECT 1 FROM predictions p WHERE p.match_id=m.id) "
                    "AND NOT EXISTS(SELECT 1 FROM team_match_stats t WHERE t.match_id=m.id) "
                    "AND NOT EXISTS(SELECT 1 FROM observations ob WHERE ob.match_id=m.id)",
                    (competition, keep_match_id),
                ).fetchall()
                for cand in candidates:
                    ca = canonical_team_name(cand["team_a"])
                    cb = canonical_team_name(cand["team_b"])
                    if (ca == canon_a and cb == canon_b) or (ca == canon_b and cb == canon_a):
                        con.execute("DELETE FROM matches WHERE id=?", (cand["id"],))

    def get_match(self, match_id: int) -> Match:
        with self.session() as con:
            row = con.execute(
                "SELECT m.*, ta.name AS team_a_name, ta.fifa_code AS team_a_code, "
                "tb.name AS team_b_name, tb.fifa_code AS team_b_code "
                "FROM matches m "
                "JOIN teams ta ON ta.id=m.team_a_id "
                "JOIN teams tb ON tb.id=m.team_b_id "
                "WHERE m.id=?",
                (match_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"match {match_id} not found")
        return Match(
            id=int(row["id"]),
            competition=row["competition"],
            stage=row["stage"],
            kickoff_utc=datetime.fromisoformat(row["kickoff_utc"]),
            team_a=Team(int(row["team_a_id"]), row["team_a_name"], row["team_a_code"]),
            team_b=Team(int(row["team_b_id"]), row["team_b_name"], row["team_b_code"]),
            status=row["status"],
            venue=row["venue"],
            neutral_site=bool(row["neutral_site"]),
        )

    def list_matches(self) -> list[Match]:
        with self.session() as con:
            rows = con.execute(
                "SELECT m.*, ta.name AS team_a_name, ta.fifa_code AS team_a_code, "
                "tb.name AS team_b_name, tb.fifa_code AS team_b_code, "
                "EXISTS(SELECT 1 FROM match_results r WHERE r.match_id=m.id) AS has_result, "
                "(SELECT COUNT(*) FROM team_match_stats s WHERE s.match_id=m.id) AS team_stat_rows, "
                "(SELECT COUNT(*) FROM observations o WHERE o.match_id=m.id) AS observation_rows, "
                "(SELECT COUNT(*) FROM predictions p WHERE p.match_id=m.id) AS prediction_rows, "
                "(SELECT COUNT(*) FROM import_runs i WHERE i.match_id=m.id) AS import_rows "
                "FROM matches m "
                "JOIN teams ta ON ta.id=m.team_a_id "
                "JOIN teams tb ON tb.id=m.team_b_id "
                "ORDER BY m.kickoff_utc, m.id"
            ).fetchall()
        chosen: dict[tuple[str, str, str, str], sqlite3.Row] = {}

        def evidence_score(row: sqlite3.Row) -> tuple[int, int, int]:
            return (
                int(row["has_result"]) * 1000
                + int(row["team_stat_rows"] or 0) * 100
                + int(row["observation_rows"] or 0) * 10
                + int(row["prediction_rows"] or 0) * 5
                + int(row["import_rows"] or 0),
                _fixture_time_quality(str(row["kickoff_utc"])),
                -int(row["id"]),
            )

        for row in rows:
            kickoff = datetime.fromisoformat(row["kickoff_utc"])
            key = (
                str(row["competition"]),
                kickoff.date().isoformat(),
                canonical_team_name(str(row["team_a_name"])),
                canonical_team_name(str(row["team_b_name"])),
            )
            previous = chosen.get(key)
            if previous is None or evidence_score(row) > evidence_score(previous):
                chosen[key] = row

        ordered = sorted(chosen.values(), key=lambda row: (row["kickoff_utc"], row["id"]))
        return [
            Match(
                id=int(row["id"]),
                competition=row["competition"],
                stage=row["stage"],
                kickoff_utc=datetime.fromisoformat(row["kickoff_utc"]),
                team_a=Team(int(row["team_a_id"]), row["team_a_name"], row["team_a_code"]),
                team_b=Team(int(row["team_b_id"]), row["team_b_name"], row["team_b_code"]),
                status="finished" if row["has_result"] else row["status"],
                venue=row["venue"],
                neutral_site=bool(row["neutral_site"]),
            )
            for row in ordered
        ]

    def import_deep_match_collection(
        self, collection: DeepMatchCollection, imported_at_utc: datetime
    ) -> DeepImportResult:
        """Persist a reviewed deep-stat JSON without guessing ambiguous fixtures."""
        scheduled = self.list_matches()
        source_id = f"deep-json-{collection.sha256[:16]}"
        imported = unchanged = ambiguous = unmatched = observation_count = 0
        primary = {
            "resumen_del_partido.goles_esperados_xg": "xg",
            "resumen_del_partido.tiros_totales": "shots",
            "tiros.tiros_a_puerta": "shots_on_target",
            "resumen_del_partido.posesion_de_balon_pct": "possession",
            "resumen_del_partido.saques_de_esquina": "corners",
            "resumen_del_partido.tarjetas_amarillas": "yellow_cards",
            "resumen_del_partido.tarjetas_rojas": "red_cards",
            # Goalkeeper-related metrics: the JSON exposes both a top-level
            # 'porteria.paradas' and a 'resumen_del_partido.paradas' duplicate.
            # We pick whichever appears first; both map to the same column.
            "porteria.paradas": "saves",
            "resumen_del_partido.paradas": "saves",
        }
        with self.session() as con:
            con.execute(
                "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                "VALUES(?, 'reviewed_json', 'Estadísticas profundas revisadas', ?, ?, 'verified', ?) "
                "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc=excluded.retrieved_at_utc, notes=excluded.notes",
                (source_id, str(collection.path), imported_at_utc.isoformat(),
                 f"sha256={collection.sha256} partidos={len(collection.matches)}"),
            )
            for record in collection.matches:
                candidates = [
                    match for match in scheduled
                    if (
                        same_team(match.team_a.name, record.team_a)
                        and same_team(match.team_b.name, record.team_b)
                    ) or (
                        same_team(match.team_a.name, record.team_b)
                        and same_team(match.team_b.name, record.team_a)
                    )
                ]
                if not candidates:
                    unmatched += 1
                    continue
                if len(candidates) != 1:
                    ambiguous += 1
                    continue
                match = candidates[0]
                event_id = f"deep:{collection.sha256}:{record.source_match_id}"
                exists = con.execute(
                    "SELECT 1 FROM import_runs WHERE match_id=? AND source_event_id=?",
                    (match.id, event_id),
                ).fetchone()
                if exists:
                    unchanged += 1
                    continue
                team_ids = {match.team_a.name: match.team_a.id, match.team_b.name: match.team_b.id}
                primary_values: dict[int, dict[str, float]] = {match.team_a.id: {}, match.team_b.id: {}}
                metrics = flatten_team_metrics(record)
                for metric in metrics:
                    canonical_subject = next(
                        name for name in team_ids if same_team(name, metric.team_name)
                    )
                    context = json.dumps({
                        **metric.context,
                        "source_match_id": record.source_match_id,
                        "source_files": record.sources,
                    }, ensure_ascii=False, sort_keys=True)
                    con.execute(
                        "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                        "VALUES(?, 'team', ?, ?, ?, NULL, ?, ?, ?, 'verified_user_json', 1, ?) "
                        "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) DO UPDATE SET value_number=excluded.value_number, unit=excluded.unit, observed_at_utc=excluded.observed_at_utc",
                        (match.id, canonical_subject, metric.metric, metric.value, metric.unit, context,
                         source_id, imported_at_utc.isoformat()),
                    )
                    observation_count += 1
                    column = primary.get(metric.metric)
                    if column:
                        primary_values[team_ids[canonical_subject]][column] = metric.value
                for team_id, values in primary_values.items():
                    columns = ("xg", "shots", "shots_on_target", "possession", "corners", "yellow_cards", "red_cards", "saves")
                    payload = [values.get(column) for column in columns]
                    con.execute(
                        "INSERT INTO team_match_stats(match_id, team_id, xg, shots, shots_on_target, possession, corners, yellow_cards, red_cards, saves, source_id, manual_edit) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
                        "ON CONFLICT(match_id, team_id) DO UPDATE SET "
                        "xg=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.xg, team_match_stats.xg) ELSE team_match_stats.xg END, "
                        "shots=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.shots, team_match_stats.shots) ELSE team_match_stats.shots END, "
                        "shots_on_target=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.shots_on_target, team_match_stats.shots_on_target) ELSE team_match_stats.shots_on_target END, "
                        "possession=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.possession, team_match_stats.possession) ELSE team_match_stats.possession END, "
                        "corners=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.corners, team_match_stats.corners) ELSE team_match_stats.corners END, "
                        "yellow_cards=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.yellow_cards, team_match_stats.yellow_cards) ELSE team_match_stats.yellow_cards END, "
                        "red_cards=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.red_cards, team_match_stats.red_cards) ELSE team_match_stats.red_cards END, "
                        "saves=CASE WHEN team_match_stats.manual_edit=0 THEN COALESCE(excluded.saves, team_match_stats.saves) ELSE team_match_stats.saves END, "
                        "source_id=CASE WHEN team_match_stats.manual_edit=0 THEN excluded.source_id ELSE team_match_stats.source_id END",
                        (match.id, team_id, *payload, source_id),
                    )
                con.execute(
                    "INSERT INTO import_runs(match_id, source_event_id, status, imported_at_utc, missing_critical_json, missing_optional_json) "
                    "VALUES(?, ?, 'complete', ?, '[]', '[\"player_stats\"]')",
                    (match.id, event_id, imported_at_utc.isoformat()),
                )
                imported += 1
        return DeepImportResult(imported, unchanged, ambiguous, unmatched, observation_count)

    # Extended observation metrics that complement the structured columns when
    # available. The mapping is intentionally small and curated: extra metrics
    # are useful only if they add real predictive signal beyond xG/SOT/possession.
    _EXTENDED_OBSERVATION_METRICS = {
        "ataque.ocasiones_claras_realizadas": "clear_chances",
        "porteria.goles_evitados": "goals_prevented",
        "ataque.toques_dentro_del_area": "box_touches",
        "defensa.errores_que_llevan_a_disparo": "errors_to_shot",
    }

    def _attach_extended_observations(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return rows
        metric_names = list(self._EXTENDED_OBSERVATION_METRICS)
        placeholders = ",".join("?" * len(metric_names))
        with self.session() as con:
            obs_rows = con.execute(
                "SELECT m.kickoff_utc, o.subject_name, o.metric, o.value_number "
                "FROM observations o JOIN matches m ON m.id = o.match_id "
                f"WHERE o.subject_type='team' AND o.metric IN ({placeholders}) "
                "AND o.value_number IS NOT NULL",
                tuple(metric_names),
            ).fetchall()
        lookup: dict[tuple[str, str], dict[str, float]] = {}
        for obs in obs_rows:
            key = (obs["kickoff_utc"], obs["subject_name"])
            short = self._EXTENDED_OBSERVATION_METRICS[obs["metric"]]
            lookup.setdefault(key, {})[short] = float(obs["value_number"])
        for row in rows:
            kickoff = row["kickoff_utc"]
            for suffix, team_key in (("a", "team_a"), ("b", "team_b")):
                team_name = row.get(team_key)
                if team_name is None:
                    continue
                extras = lookup.get((kickoff, str(team_name)), {})
                for short, value in extras.items():
                    row.setdefault(f"{short}_{suffix}", value)
        return rows

    def list_deep_xg_rows_before(self, as_of_utc: datetime) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
                "sa.xg AS xg_a, sb.xg AS xg_b, "
                "sa.shots AS shots_a, sb.shots AS shots_b, "
                "sa.shots_on_target AS shots_on_target_a, sb.shots_on_target AS shots_on_target_b, "
                "sa.possession AS possession_a, sb.possession AS possession_b "
                "FROM matches m "
                "JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                "JOIN team_match_stats sa ON sa.match_id=m.id AND sa.team_id=m.team_a_id "
                "JOIN team_match_stats sb ON sb.match_id=m.id AND sb.team_id=m.team_b_id "
                "WHERE m.kickoff_utc < ? AND sa.xg IS NOT NULL AND sb.xg IS NOT NULL "
                "ORDER BY m.kickoff_utc, m.id",
                (as_of_utc.isoformat(),),
            ).fetchall()
        return self._attach_extended_observations([dict(row) for row in rows])

    def list_deep_team_metric_observations_before(self, as_of_utc: datetime) -> list[dict]:
        """All team-level deep-stat observations strictly before ``as_of_utc``.

        Returns one row per (match, team, metric) with columns
        ``kickoff_utc, team_name, metric, value_number``. Consumed by
        team_profile.build_team_profile to compute per-team aggregates.

        Deduplicates by (match_id, subject_name, metric): the same metric for
        the same match can be inserted multiple times if several deep-JSON
        files import the same fixture (different source_id each time). We keep
        the most recent (highest observations.id) so sample sizes reflect the
        real number of *matches*, not the number of import passes.
        """
        with self.session() as con:
            rows = con.execute(
                "SELECT m.kickoff_utc, m.competition, o.subject_name AS team_name, o.metric, o.value_number "
                "FROM observations o "
                "JOIN matches m ON m.id = o.match_id "
                "JOIN ( "
                "    SELECT MAX(o2.id) AS id "
                "    FROM observations o2 "
                "    JOIN matches m2 ON m2.id = o2.match_id "
                "    WHERE o2.subject_type = 'team' "
                "      AND o2.evidence_status IN ('verified', 'verified_user_json', "
                "                                 'verified_user_capture', 'verified_external') "
                "      AND o2.value_number IS NOT NULL "
                "      AND m2.kickoff_utc < ? "
                "    GROUP BY o2.match_id, o2.subject_name, o2.metric "
                ") latest ON latest.id = o.id "
                "WHERE m.kickoff_utc < ? "
                "ORDER BY m.kickoff_utc",
                (as_of_utc.isoformat(), as_of_utc.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_deep_volume_rows_before(self, as_of_utc: datetime) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
                "sa.corners AS corners_a, sb.corners AS corners_b, "
                "CASE WHEN sa.yellow_cards IS NULL THEN NULL ELSE sa.yellow_cards + COALESCE(sa.red_cards, 0) END AS cards_a, "
                "CASE WHEN sb.yellow_cards IS NULL THEN NULL ELSE sb.yellow_cards + COALESCE(sb.red_cards, 0) END AS cards_b, "
                "sa.shots AS shots_a, sb.shots AS shots_b, "
                "sa.shots_on_target AS shots_on_target_a, sb.shots_on_target AS shots_on_target_b, "
                "sa.saves AS saves_a, sb.saves AS saves_b "
                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                "JOIN team_match_stats sa ON sa.match_id=m.id AND sa.team_id=m.team_a_id "
                "JOIN team_match_stats sb ON sb.match_id=m.id AND sb.team_id=m.team_b_id "
                "WHERE m.kickoff_utc < ? ORDER BY m.kickoff_utc, m.id",
                (as_of_utc.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_deep_goalkeeper_rows_before(self, as_of_utc: datetime) -> list[dict]:
        """Per-team rows for past matches: saves, shots-on-target faced (opponent SOT),
        and goals conceded (opponent goals).
        Used by build_goalkeeper_baseline to compute a recency-weighted save rate.
        """
        with self.session() as con:
            rows = con.execute(
                "SELECT m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, "
                "sa.saves AS saves_a, sb.saves AS saves_b, "
                "sa.shots_on_target AS sot_a, sb.shots_on_target AS sot_b, "
                "sa.goals AS goals_a, sb.goals AS goals_b "
                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                "JOIN team_match_stats sa ON sa.match_id=m.id AND sa.team_id=m.team_a_id "
                "JOIN team_match_stats sb ON sb.match_id=m.id AND sb.team_id=m.team_b_id "
                "WHERE m.kickoff_utc < ? "
                "AND (sa.saves IS NOT NULL OR sb.saves IS NOT NULL) "
                "ORDER BY m.kickoff_utc, m.id",
                (as_of_utc.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_squad_context_event(self, event: dict, created_at_utc: datetime) -> int:
        with self.session() as con:
            con.execute(
                "INSERT INTO squad_context_events(team_name, player_name, event_type, starts_at_utc, ends_at_utc, affected_match_id, source_id, evidence_status, notes, created_at_utc) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(team_name, player_name, event_type, starts_at_utc, source_id) DO UPDATE SET "
                "ends_at_utc=excluded.ends_at_utc, affected_match_id=excluded.affected_match_id, evidence_status=excluded.evidence_status, notes=excluded.notes, created_at_utc=excluded.created_at_utc",
                (event["team_name"], event.get("player_name"), event["event_type"], event["starts_at_utc"],
                 event.get("ends_at_utc"), event.get("affected_match_id"), event["source_id"],
                 event.get("evidence_status", "reviewed"), event.get("notes", ""), created_at_utc.isoformat()),
            )
            row = con.execute(
                "SELECT id FROM squad_context_events WHERE team_name=? AND player_name IS ? AND event_type=? AND starts_at_utc=? AND source_id=?",
                (event["team_name"], event.get("player_name"), event["event_type"], event["starts_at_utc"], event["source_id"]),
            ).fetchone()
            return int(row["id"])

    def list_active_squad_context_events(
        self, teams: tuple[str, str], kickoff_utc: datetime, match_id: int | None
    ) -> list[dict]:
        with self.session() as con:
            rows = [dict(row) for row in con.execute(
                "SELECT * FROM squad_context_events WHERE evidence_status IN ('reviewed', 'verified') "
                "AND starts_at_utc<=? AND (ends_at_utc IS NULL OR ends_at_utc>=?) "
                "AND (affected_match_id IS NULL OR affected_match_id IS ?)",
                (kickoff_utc.isoformat(), kickoff_utc.isoformat(), match_id),
            ).fetchall()]
        return [row for row in rows if any(same_team(row["team_name"], team) for team in teams)]

    def add_manual_odds(
        self,
        match_id: int,
        market_family: str,
        market_name: str,
        selection_name: str,
        line: float | None,
        decimal_odds: float,
        bookmaker: str,
        captured_at_utc: datetime,
        considered: bool = False,
    ) -> int:
        with self.session() as con:
            cur = con.execute(
                "INSERT INTO manual_odds(match_id, market_family, market_name, selection_name, line, decimal_odds, bookmaker, captured_at_utc, considered) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (match_id, market_family, market_name, selection_name, line, decimal_odds, bookmaker, captured_at_utc.isoformat(), int(considered)),
            )
            return int(cur.lastrowid)

    def list_manual_odds(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM manual_odds WHERE match_id=? ORDER BY id",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_prediction(
        self,
        match_id: int,
        market_family: str,
        market_name: str,
        selection_name: str,
        line: float | None,
        probability: float,
        confidence: str,
        generated_at_utc: datetime,
        explanation: str,
    ) -> int:
        with self.session() as con:
            cur = con.execute(
                "INSERT INTO predictions(match_id, market_family, market_name, selection_name, line, probability, confidence, generated_at_utc, explanation) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (match_id, market_family, market_name, selection_name, line, probability, confidence, generated_at_utc.isoformat(), explanation),
            )
            return int(cur.lastrowid)

    def list_predictions(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM predictions WHERE match_id=? ORDER BY id",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_backtest(
        self,
        prediction_id: int,
        result_value: float,
        brier_score: float | None,
        hit: bool,
        evaluated_at_utc: datetime,
    ) -> int:
        with self.session() as con:
            cur = con.execute(
                "INSERT INTO backtests(prediction_id, result_value, brier_score, hit, evaluated_at_utc) "
                "VALUES(?, ?, ?, ?, ?)",
                (prediction_id, result_value, brier_score, int(hit), evaluated_at_utc.isoformat()),
            )
            return int(cur.lastrowid)

    def list_backtests(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT e.id, e.prediction_id, e.result_value, e.brier_score, e.hit, e.evaluated_at_utc, "
                "p.market_family, p.market_name, p.selection_name, p.probability "
                "FROM prediction_evaluations e JOIN predictions p ON p.id=e.prediction_id "
                "WHERE p.match_id=? AND e.active=1 "
                "UNION ALL "
                "SELECT b.id, b.prediction_id, b.result_value, b.brier_score, b.hit, b.evaluated_at_utc, "
                "p.market_family, p.market_name, p.selection_name, p.probability "
                "FROM backtests b JOIN predictions p ON p.id=b.prediction_id "
                "WHERE p.match_id=? AND NOT EXISTS("
                "SELECT 1 FROM prediction_evaluations e WHERE e.prediction_id=p.id) "
                "ORDER BY evaluated_at_utc",
                (match_id, match_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all_backtests(self) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT e.id, e.prediction_id, e.result_value, e.brier_score, e.hit, e.evaluated_at_utc, "
                "p.market_family, p.market_name, p.selection_name, p.probability "
                "FROM prediction_evaluations e JOIN predictions p ON p.id=e.prediction_id "
                "WHERE e.active=1 "
                "UNION ALL "
                "SELECT b.id, b.prediction_id, b.result_value, b.brier_score, b.hit, b.evaluated_at_utc, "
                "p.market_family, p.market_name, p.selection_name, p.probability "
                "FROM backtests b JOIN predictions p ON p.id=b.prediction_id "
                "WHERE NOT EXISTS(SELECT 1 FROM prediction_evaluations e WHERE e.prediction_id=p.id) "
                "ORDER BY evaluated_at_utc"
            ).fetchall()
        return [dict(row) for row in rows]

    def import_collector_bundle(
        self,
        match_id: int,
        bundle: CollectorEventBundle,
    ) -> None:
        with self.session() as con:
            for source in bundle.sources:
                con.execute(
                    "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                    "VALUES(?, 'collector', 'sports-data', ?, ?, ?, '') "
                    "ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url, "
                    "retrieved_at_utc=excluded.retrieved_at_utc, status=excluded.status",
                    (
                        str(source["id"]),
                        source.get("source_url"),
                        source.get("retrieved_at_utc") or bundle.updated_at_utc.isoformat(),
                        str(source.get("status") or "incomplete"),
                    ),
                )
            for row in bundle.statistics:
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) DO UPDATE SET "
                    "value_number=excluded.value_number, value_text=excluded.value_text, unit=excluded.unit, "
                    "evidence_status=excluded.evidence_status, sample_size=excluded.sample_size, observed_at_utc=excluded.observed_at_utc",
                    (
                        match_id,
                        str(row.get("subject_type") or "event"),
                        row.get("subject_name"),
                        str(row["metric"]),
                        row.get("value_number"),
                        row.get("value_text"),
                        row.get("unit"),
                        str(row.get("context_json") or "{}"),
                        str(row.get("source_id") or "collector-unknown"),
                        str(row.get("evidence_status") or "incomplete"),
                        row.get("sample_size"),
                        str(row.get("observed_at_utc") or bundle.updated_at_utc.isoformat()),
                    ),
                )
            for row in bundle.lineups:
                con.execute(
                    "INSERT INTO imported_lineups(match_id, team_name, player_name, lineup_status, position, shirt_number, source_id, observed_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(match_id, team_name, player_name, source_id) DO UPDATE SET "
                    "lineup_status=excluded.lineup_status, position=excluded.position, shirt_number=excluded.shirt_number, observed_at_utc=excluded.observed_at_utc",
                    (
                        match_id,
                        str(row["team_name"]),
                        str(row["player_name"]),
                        str(row.get("lineup_status") or "unknown"),
                        row.get("position"),
                        row.get("shirt_number"),
                        str(row.get("source_id") or "collector-unknown"),
                        str(row.get("observed_at_utc") or bundle.updated_at_utc.isoformat()),
                    ),
                )
            con.execute(
                "INSERT INTO import_runs(match_id, source_event_id, status, imported_at_utc, missing_critical_json, missing_optional_json) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(match_id, source_event_id) DO UPDATE SET status=excluded.status, "
                "imported_at_utc=excluded.imported_at_utc, missing_critical_json=excluded.missing_critical_json, "
                "missing_optional_json=excluded.missing_optional_json",
                (
                    match_id,
                    str(bundle.event_id),
                    bundle.completeness_status,
                    bundle.updated_at_utc.isoformat(),
                    json.dumps(bundle.missing_critical),
                    json.dumps(bundle.missing_optional),
                ),
            )

    def list_observations(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM observations WHERE match_id=? ORDER BY subject_type, subject_name, metric",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_import_runs(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM import_runs WHERE match_id=? ORDER BY imported_at_utc DESC",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_imported_lineups(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM imported_lineups WHERE match_id=? ORDER BY team_name, player_name",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_manual_observations(
        self,
        match_id: int,
        rows: list[dict],
        observed_at_utc: datetime,
    ) -> None:
        source_id = f"manual-{match_id}"
        observed = observed_at_utc.isoformat()
        with self.session() as con:
            con.execute(
                "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                "VALUES(?, 'manual', 'user correction', NULL, ?, 'verified', 'Edited in the local app') "
                "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc=excluded.retrieved_at_utc, status='verified'",
                (source_id, observed),
            )
            for row in rows:
                metric = str(row.get("metric") or "").strip()
                if not metric:
                    continue
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, '{}', ?, 'manual', ?, ?) "
                    "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) DO UPDATE SET "
                    "value_number=excluded.value_number, value_text=excluded.value_text, unit=excluded.unit, "
                    "sample_size=excluded.sample_size, observed_at_utc=excluded.observed_at_utc, evidence_status='manual'",
                    (
                        match_id,
                        str(row.get("subject_type") or "team"),
                        row.get("subject_name"),
                        metric,
                        row.get("value_number"),
                        row.get("value_text"),
                        row.get("unit"),
                        source_id,
                        row.get("sample_size"),
                        observed,
                    ),
                )

    def import_sofascore_preview(
        self,
        match_id: int,
        imported: Any,
        retrieved_at_utc: datetime,
    ) -> None:
        source_id = f"sofascore-{imported.event_id}"
        retrieved = retrieved_at_utc.isoformat()

        def number(value):
            if value is None:
                return None
            try:
                return float(str(value).replace("%", "").strip())
            except ValueError:
                return None

        with self.session() as con:
            con.execute(
                "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                "VALUES(?, 'url_import', 'SofaScore', ?, ?, ?, 'Experimental public URL import') "
                "ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url, retrieved_at_utc=excluded.retrieved_at_utc, status=excluded.status",
                (source_id, imported.source_url, retrieved, imported.status),
            )
            for stat in imported.statistics:
                context = json.dumps(
                    {"period": stat.get("period"), "group": stat.get("group")},
                    sort_keys=True,
                )
                for team_name, raw_value in (
                    (imported.team_a, stat.get("team_a_value")),
                    (imported.team_b, stat.get("team_b_value")),
                ):
                    numeric = number(raw_value)
                    con.execute(
                        "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                        "VALUES(?, 'team', ?, ?, ?, ?, NULL, ?, ?, 'imported', 1, ?) "
                        "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) DO UPDATE SET "
                        "value_number=excluded.value_number, value_text=excluded.value_text, observed_at_utc=excluded.observed_at_utc, evidence_status='imported'",
                        (match_id, team_name, str(stat.get("metric") or "unknown"), numeric, None if numeric is not None else str(raw_value), context, source_id, retrieved),
                    )
            for player in imported.players:
                team_name = imported.team_a if player.get("side") == "home" else imported.team_b
                con.execute(
                    "INSERT INTO imported_lineups(match_id, team_name, player_name, lineup_status, position, shirt_number, source_id, observed_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, NULL, ?, ?) "
                    "ON CONFLICT(match_id, team_name, player_name, source_id) DO UPDATE SET "
                    "lineup_status=excluded.lineup_status, position=excluded.position, observed_at_utc=excluded.observed_at_utc",
                    (match_id, team_name, str(player.get("player_name") or "Unknown player"), "starter" if player.get("starter") else "substitute", player.get("position"), source_id, retrieved),
                )

    def settle_match(
        self,
        match_id: int,
        goals_a: int,
        goals_b: int,
        statistics: list[dict],
        recorded_at_utc: datetime,
        source_type: str = "manual",
    ) -> None:
        match = self.get_match(match_id)
        with self.session() as con:
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(match_id) DO UPDATE SET "
                "goals_a=excluded.goals_a, goals_b=excluded.goals_b, source_type=excluded.source_type, recorded_at_utc=excluded.recorded_at_utc",
                (match_id, goals_a, goals_b, source_type, recorded_at_utc.isoformat()),
            )
            con.execute("UPDATE matches SET status='finished' WHERE id=?", (match_id,))
            predictions = [dict(row) for row in con.execute("SELECT * FROM predictions WHERE match_id=?", (match_id,)).fetchall()]
            for prediction in predictions:
                occurred = prediction_occurred(
                    prediction, match.team_a.name, match.team_b.name, goals_a, goals_b
                )
                if occurred is None:
                    continue
                existing = con.execute(
                    "SELECT 1 FROM backtests WHERE prediction_id=? LIMIT 1",
                    (prediction["id"],),
                ).fetchone()
                if existing:
                    continue
                probability = float(prediction["probability"])
                con.execute(
                    "INSERT INTO backtests(prediction_id, result_value, brier_score, hit, evaluated_at_utc) VALUES(?, ?, ?, ?, ?)",
                    (
                        prediction["id"], 1.0 if occurred else 0.0,
                        brier_score(probability, occurred), int(occurred), recorded_at_utc.isoformat(),
                    ),
                )
        if statistics:
            self.save_manual_observations(match_id, statistics, recorded_at_utc)

    def get_match_result(self, match_id: int) -> dict | None:
        with self.session() as con:
            row = con.execute("SELECT * FROM match_results WHERE match_id=?", (match_id,)).fetchone()
        return dict(row) if row else None

    def list_team_match_stats(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT s.*, t.name AS team_name FROM team_match_stats s "
                "JOIN teams t ON t.id=s.team_id WHERE s.match_id=? ORDER BY t.name",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_match_evidence_status(self, match_id: int) -> dict:
        with self.session() as con:
            row = con.execute(
                "SELECT "
                "EXISTS(SELECT 1 FROM match_results r WHERE r.match_id=?) AS has_result, "
                "COUNT(DISTINCT CASE WHEN s.xg IS NOT NULL OR s.shots IS NOT NULL OR "
                "s.shots_on_target IS NOT NULL OR s.possession IS NOT NULL OR s.corners IS NOT NULL OR "
                "s.yellow_cards IS NOT NULL OR s.red_cards IS NOT NULL THEN s.team_id END) AS team_stat_rows, "
                "(SELECT COUNT(*) FROM observations o WHERE o.match_id=? AND o.evidence_status='verified_user_json') AS deep_observations, "
                "(SELECT COUNT(*) FROM player_match_stats p WHERE p.match_id=?) AS player_stat_rows "
                "FROM team_match_stats s WHERE s.match_id=?",
                (match_id, match_id, match_id, match_id),
            ).fetchone()
        team_stat_rows = int(row["team_stat_rows"] or 0)
        return {
            "has_result": bool(row["has_result"]),
            "has_team_statistics": team_stat_rows > 0,
            "team_stat_rows": team_stat_rows,
            "deep_observations": int(row["deep_observations"] or 0),
            "player_stat_rows": int(row["player_stat_rows"] or 0),
        }

    def get_all_match_evidence_statuses(self) -> dict[int, dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT m.id AS match_id, "
                "EXISTS(SELECT 1 FROM match_results r WHERE r.match_id=m.id) AS has_result, "
                "COUNT(DISTINCT CASE WHEN s.xg IS NOT NULL OR s.shots IS NOT NULL OR "
                "s.shots_on_target IS NOT NULL OR s.possession IS NOT NULL OR s.corners IS NOT NULL OR "
                "s.yellow_cards IS NOT NULL OR s.red_cards IS NOT NULL THEN s.team_id END) AS team_stat_rows, "
                "(SELECT COUNT(*) FROM observations o WHERE o.match_id=m.id AND o.evidence_status='verified_user_json') AS deep_observations, "
                "(SELECT COUNT(*) FROM player_match_stats p WHERE p.match_id=m.id) AS player_stat_rows "
                "FROM matches m LEFT JOIN team_match_stats s ON s.match_id=m.id "
                "GROUP BY m.id"
            ).fetchall()
        result = {}
        for row in rows:
            team_stat_rows = int(row["team_stat_rows"] or 0)
            result[int(row["match_id"])] = {
                "has_result": bool(row["has_result"]),
                "has_team_statistics": team_stat_rows > 0,
                "team_stat_rows": team_stat_rows,
                "deep_observations": int(row["deep_observations"] or 0),
                "player_stat_rows": int(row["player_stat_rows"] or 0),
            }
        return result

    def count_deep_observations_by_match(self) -> dict[int, int]:
        with self.session() as con:
            rows = con.execute(
                "SELECT match_id, COUNT(*) AS cnt FROM observations "
                "WHERE evidence_status='verified_user_json' GROUP BY match_id"
            ).fetchall()
        return {int(row["match_id"]): int(row["cnt"]) for row in rows}

    def has_import_runs_by_match(self) -> dict[int, bool]:
        with self.session() as con:
            rows = con.execute(
                "SELECT DISTINCT match_id FROM import_runs"
            ).fetchall()
        return {int(row["match_id"]): True for row in rows}

    def list_match_results_before(self, as_of_utc: datetime) -> list[MatchResult]:
        with self.session() as con:
            rows = con.execute(
                "SELECT m.kickoff_utc, ta.name AS team_a, tb.name AS team_b, r.goals_a, r.goals_b "
                "FROM match_results r JOIN matches m ON m.id=r.match_id "
                "JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id "
                "WHERE m.kickoff_utc < ? ORDER BY m.kickoff_utc",
                (as_of_utc.isoformat(),),
            ).fetchall()
        return [
            MatchResult(
                datetime.fromisoformat(row["kickoff_utc"]).date(), row["team_a"], row["team_b"],
                int(row["goals_a"]), int(row["goals_b"]), "world_cup",
            )
            for row in rows
        ]

    def import_historical_matches(self, rows) -> int:
        inserted = 0
        with self.session() as con:
            for row in rows:
                for source_id in row.source_ids:
                    key = "|".join(map(str, row.identity))
                    cursor = con.execute(
                        "INSERT OR IGNORE INTO historical_matches(played_at_utc, team_a_name, team_b_name, goals_a, goals_b, tournament, city, country, neutral_site, source_id, source_row_key) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            row.played_on.isoformat(),
                            row.team_a,
                            row.team_b,
                            row.goals_a,
                            row.goals_b,
                            row.tournament,
                            row.city,
                            row.country,
                            int(row.neutral_site),
                            source_id,
                            key,
                        ),
                    )
                    inserted += cursor.rowcount
        return inserted

    def list_historical_rows_before(self, as_of_utc: datetime) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT played_at_utc, team_a_name AS team_a, team_b_name AS team_b, goals_a, goals_b, tournament, neutral_site "
                "FROM historical_matches WHERE played_at_utc < ? ORDER BY played_at_utc, id",
                (as_of_utc.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_historical_results_before(self, as_of_utc: datetime) -> list[MatchResult]:
        rows = self.list_historical_rows_before(as_of_utc)
        results = []
        for row in rows:
            tournament = str(row.get("tournament") or "").lower()
            match_type = "world_cup" if "world cup" in tournament else "friendly" if "friendly" in tournament else "competitive"
            results.append(
                MatchResult(
                    datetime.fromisoformat(str(row["played_at_utc"])).date(),
                    str(row["team_a"]), str(row["team_b"]), int(row["goals_a"]), int(row["goals_b"]), match_type,
                )
            )
        return results

    def import_transfermarkt_entities(self, package) -> None:
        team_ids: dict[str, int] = {}
        with self.session() as con:
            for row in package.national_teams:
                con.execute(
                    "INSERT INTO teams(name, fifa_code) VALUES(?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET fifa_code=excluded.fifa_code",
                    (row["name"], row.get("country_id")),
                )
                team_id = int(
                    con.execute(
                        "SELECT id FROM teams WHERE name=?", (row["name"],)
                    ).fetchone()[0]
                )
                team_ids[row["national_team_id"]] = team_id
                con.execute(
                    "INSERT INTO provider_entities(provider, entity_type, provider_id, canonical_type, canonical_id, original_name, metadata_json) "
                    "VALUES('transfermarkt', 'national_team', ?, 'team', ?, ?, ?) "
                    "ON CONFLICT(provider, entity_type, provider_id) DO UPDATE SET "
                    "canonical_id=excluded.canonical_id, original_name=excluded.original_name, metadata_json=excluded.metadata_json",
                    (
                        row["national_team_id"],
                        team_id,
                        row["name"],
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
            for row in package.players:
                national_team_id = row.get("current_national_team_id")
                if national_team_id not in team_ids:
                    continue
                team_id = team_ids[national_team_id]
                con.execute(
                    "INSERT INTO players(name, team_id, position) VALUES(?, ?, ?) "
                    "ON CONFLICT(name, team_id) DO UPDATE SET position=excluded.position",
                    (row["name"], team_id, row.get("position")),
                )
                player_id = int(
                    con.execute(
                        "SELECT id FROM players WHERE name=? AND team_id=?",
                        (row["name"], team_id),
                    ).fetchone()[0]
                )
                con.execute(
                    "INSERT INTO provider_entities(provider, entity_type, provider_id, canonical_type, canonical_id, original_name, metadata_json) "
                    "VALUES('transfermarkt', 'player', ?, 'player', ?, ?, ?) "
                    "ON CONFLICT(provider, entity_type, provider_id) DO UPDATE SET "
                    "canonical_id=excluded.canonical_id, original_name=excluded.original_name, metadata_json=excluded.metadata_json",
                    (
                        row["player_id"],
                        player_id,
                        row["name"],
                        json.dumps(row, ensure_ascii=False),
                    ),
                )

    def list_provider_entities(self, provider: str) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM provider_entities WHERE provider=? "
                "ORDER BY entity_type, provider_id",
                (provider,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_screenshot_batch(
        self,
        match_id: int,
        source_url: str | None,
        created_at_utc: datetime,
    ) -> int:
        with self.session() as con:
            cursor = con.execute(
                "INSERT INTO screenshot_batches(match_id, status, source_url, created_at_utc) "
                "VALUES(?, 'draft', ?, ?)",
                (match_id, source_url, created_at_utc.isoformat()),
            )
            return int(cursor.lastrowid)

    def add_screenshot_asset(
        self,
        batch_id: int,
        original_name: str,
        mime_type: str,
        byte_size: int,
        sha256: str,
        stored_path: str,
        uploaded_at_utc: datetime,
    ) -> int:
        with self.session() as con:
            con.execute(
                "INSERT OR IGNORE INTO screenshot_assets(batch_id, original_name, mime_type, byte_size, sha256, stored_path, uploaded_at_utc) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    original_name,
                    mime_type,
                    byte_size,
                    sha256,
                    stored_path,
                    uploaded_at_utc.isoformat(),
                ),
            )
            row = con.execute(
                "SELECT id FROM screenshot_assets WHERE batch_id=? AND sha256=?",
                (batch_id, sha256),
            ).fetchone()
            return int(row["id"])

    def add_extraction_candidates(
        self, batch_id: int, candidates: list[dict]
    ) -> list[int]:
        inserted: list[int] = []
        with self.session() as con:
            for row in candidates:
                if row.get("review_status") != "pending_review":
                    raise ValueError("New extraction candidates must be pending_review")
                cursor = con.execute(
                    "INSERT INTO extraction_candidates(batch_id, asset_id, subject_type, subject_name, metric, value_number, value_text, unit, period, raw_label, raw_value, confidence, warnings_json, review_status) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review')",
                    (
                        batch_id,
                        row["asset_id"],
                        row["subject_type"],
                        row.get("subject_name"),
                        row["metric"],
                        row.get("value_number"),
                        row.get("value_text"),
                        row.get("unit"),
                        row.get("period", "ALL"),
                        row["raw_label"],
                        row["raw_value"],
                        row["confidence"],
                        json.dumps(row.get("warnings", []), ensure_ascii=False),
                    ),
                )
                inserted.append(int(cursor.lastrowid))
        return inserted

    def list_extraction_candidates(self, batch_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT c.*, a.stored_path, a.original_name "
                "FROM extraction_candidates c JOIN screenshot_assets a ON a.id=c.asset_id "
                "WHERE c.batch_id=? ORDER BY c.id",
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def review_candidate(
        self,
        candidate_id: int,
        decision: CandidateDecision,
        reviewed_at_utc: datetime,
    ) -> None:
        statuses = {
            "confirm": "confirmed",
            "correct": "corrected",
            "discard": "discarded",
        }
        if decision.decision not in statuses:
            raise ValueError("Unsupported review decision")
        with self.session() as con:
            exists = con.execute(
                "SELECT 1 FROM extraction_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if not exists:
                raise KeyError(f"candidate {candidate_id} not found")
            con.execute(
                "INSERT INTO review_decisions(candidate_id, decision, corrected_subject_name, corrected_metric, corrected_value_number, corrected_value_text, corrected_unit, corrected_period, rejection_reason, reviewed_at_utc) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(candidate_id) DO UPDATE SET "
                "decision=excluded.decision, corrected_subject_name=excluded.corrected_subject_name, "
                "corrected_metric=excluded.corrected_metric, corrected_value_number=excluded.corrected_value_number, "
                "corrected_value_text=excluded.corrected_value_text, corrected_unit=excluded.corrected_unit, "
                "corrected_period=excluded.corrected_period, rejection_reason=excluded.rejection_reason, "
                "reviewed_at_utc=excluded.reviewed_at_utc",
                (
                    candidate_id,
                    decision.decision,
                    decision.corrected_subject_name,
                    decision.corrected_metric,
                    decision.corrected_value_number,
                    decision.corrected_value_text,
                    decision.corrected_unit,
                    decision.corrected_period,
                    decision.rejection_reason,
                    reviewed_at_utc.isoformat(),
                ),
            )
            con.execute(
                "UPDATE extraction_candidates SET review_status=? WHERE id=?",
                (statuses[decision.decision], candidate_id),
            )

    def finalize_screenshot_batch(
        self, batch_id: int, finalized_at_utc: datetime
    ) -> None:
        finalized = finalized_at_utc.isoformat()
        with self.session() as con:
            batch = con.execute(
                "SELECT * FROM screenshot_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise KeyError(f"screenshot batch {batch_id} not found")
            if batch["status"] == "finalized":
                return
            candidates = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM extraction_candidates WHERE batch_id=? ORDER BY id",
                    (batch_id,),
                ).fetchall()
            ]
            ensure_batch_finalizable(candidates)
            source_id = f"user-capture-{batch_id}"
            con.execute(
                "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                "VALUES(?, 'user_capture', 'SofaScore screenshot', ?, ?, 'verified', 'Every used value was reviewed') "
                "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc=excluded.retrieved_at_utc, status=excluded.status",
                (source_id, batch["source_url"], finalized),
            )
            for candidate in candidates:
                decision_row = con.execute(
                    "SELECT * FROM review_decisions WHERE candidate_id=?",
                    (candidate["id"],),
                ).fetchone()
                if decision_row is None:
                    raise ValueError("Reviewed candidate is missing its audit decision")
                decision = CandidateDecision(
                    decision=decision_row["decision"],
                    corrected_subject_name=decision_row["corrected_subject_name"],
                    corrected_metric=decision_row["corrected_metric"],
                    corrected_value_number=decision_row["corrected_value_number"],
                    corrected_value_text=decision_row["corrected_value_text"],
                    corrected_unit=decision_row["corrected_unit"],
                    corrected_period=decision_row["corrected_period"],
                    rejection_reason=decision_row["rejection_reason"],
                )
                normalized = normalized_review_value(candidate, decision)
                if normalized["evidence_status"] == "discarded":
                    continue
                context = json.dumps(
                    {
                        "period": normalized["period"],
                        "candidate_id": candidate["id"],
                        "raw_label": candidate["raw_label"],
                        "raw_value": candidate["raw_value"],
                        "warnings": json.loads(candidate.get("warnings_json") or "[]"),
                        "team_name": next(
                            (
                                warning.split(":", 1)[1]
                                for warning in json.loads(candidate.get("warnings_json") or "[]")
                                if str(warning).startswith("team:")
                            ),
                            None,
                        ),
                    },
                    sort_keys=True,
                )
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified_user_capture', 1, ?)",
                    (
                        batch["match_id"],
                        normalized["subject_type"],
                        normalized.get("subject_name"),
                        normalized["metric"],
                        normalized.get("value_number"),
                        normalized.get("value_text"),
                        normalized.get("unit"),
                        context,
                        source_id,
                        finalized,
                    ),
                )
            con.execute(
                "UPDATE screenshot_batches SET status='finalized', finalized_at_utc=? WHERE id=?",
                (finalized, batch_id),
            )

    def settle_match_versioned(
        self,
        match_id: int,
        goals_a: int,
        goals_b: int,
        batch_id: int | None,
        evaluated_at_utc: datetime,
    ) -> int:
        match = self.get_match(match_id)
        evaluated = evaluated_at_utc.isoformat()
        with self.session() as con:
            active = con.execute(
                "SELECT * FROM settlement_versions WHERE match_id=? AND active=1",
                (match_id,),
            ).fetchone()
            if (
                active is not None
                and int(active["goals_a"]) == goals_a
                and int(active["goals_b"]) == goals_b
                and active["batch_id"] == batch_id
            ):
                return int(active["id"])
            version = int(
                con.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM settlement_versions WHERE match_id=?",
                    (match_id,),
                ).fetchone()[0]
            ) + 1
            con.execute(
                "UPDATE settlement_versions SET active=0 WHERE match_id=?",
                (match_id,),
            )
            cursor = con.execute(
                "INSERT INTO settlement_versions(match_id, version, batch_id, goals_a, goals_b, active, created_at_utc) "
                "VALUES(?, ?, ?, ?, ?, 1, ?)",
                (match_id, version, batch_id, goals_a, goals_b, evaluated),
            )
            settlement_id = int(cursor.lastrowid)
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                "VALUES(?, ?, ?, 'verified_user_capture', ?) "
                "ON CONFLICT(match_id) DO UPDATE SET goals_a=excluded.goals_a, goals_b=excluded.goals_b, "
                "source_type=excluded.source_type, recorded_at_utc=excluded.recorded_at_utc",
                (match_id, goals_a, goals_b, evaluated),
            )
            con.execute(
                "INSERT INTO historical_matches(played_at_utc, team_a_name, team_b_name, goals_a, goals_b, tournament, city, country, neutral_site, source_id, source_row_key) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, 'reviewed_settlement', ?) "
                "ON CONFLICT(source_id, source_row_key) DO UPDATE SET goals_a=excluded.goals_a, goals_b=excluded.goals_b, tournament=excluded.tournament, city=excluded.city, neutral_site=excluded.neutral_site",
                (
                    match.kickoff_utc.isoformat(), match.team_a.name, match.team_b.name,
                    goals_a, goals_b, match.competition, match.venue, int(match.neutral_site), str(match_id),
                ),
            )
            con.execute("UPDATE matches SET status='finished' WHERE id=?", (match_id,))
            con.execute(
                "UPDATE prediction_evaluations SET active=0 "
                "WHERE prediction_id IN (SELECT id FROM predictions WHERE match_id=?)",
                (match_id,),
            )
            predictions = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM predictions WHERE match_id=?", (match_id,)
                ).fetchall()
            ]
            for prediction in predictions:
                occurred = prediction_occurred(
                    prediction,
                    match.team_a.name,
                    match.team_b.name,
                    goals_a,
                    goals_b,
                )
                if occurred is None:
                    continue
                score = brier_score(float(prediction["probability"]), occurred)
                con.execute(
                    "INSERT INTO prediction_evaluations(prediction_id, settlement_version_id, result_value, brier_score, hit, active, evaluated_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, 1, ?)",
                    (
                        prediction["id"],
                        settlement_id,
                        1.0 if occurred else 0.0,
                        score,
                        int(occurred),
                        evaluated,
                    ),
                )
            training_source = [
                dict(row)
                for row in con.execute(
                    "SELECT played_at_utc, team_a_name AS team_a, team_b_name AS team_b, goals_a, goals_b, neutral_site "
                    "FROM historical_matches ORDER BY played_at_utc, id"
                ).fetchall()
            ]
            fitted = train_outcome_model(build_training_rows(training_source), minimum_matches=60)
            persisted_status = fitted.status
            persisted_reason = fitted.reason
            if fitted.status == "ready":
                try:
                    save_outcome_model(fitted, self.path.parent / "models" / "outcome_ml.joblib")
                except Exception as exc:
                    # A pickle failure must NOT block the user from closing a
                    # match. Most commonly this happens when Streamlit's hot
                    # reload caused the FittedOutcomeModel class to be loaded
                    # twice; the in-memory instance no longer matches the
                    # current class object, so joblib refuses to pickle. We
                    # record the failure as the run reason and keep going.
                    persisted_status = "error"
                    persisted_reason = f"save_failed: {type(exc).__name__}: {exc}"
            con.execute(
                "INSERT INTO outcome_model_runs(status, sample_size, training_cutoff_utc, validation_cutoff_utc, temperature, reason, created_at_utc) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    persisted_status, fitted.sample_size, fitted.training_cutoff_utc,
                    fitted.validation_cutoff_utc, fitted.temperature if fitted.status == "ready" else None,
                    persisted_reason, evaluated,
                ),
            )
            return settlement_id

    def list_prediction_evaluations(
        self, match_id: int, active_only: bool = True
    ) -> list[dict]:
        query = (
            "SELECT e.*, p.market_family, p.market_name, p.selection_name, p.probability "
            "FROM prediction_evaluations e "
            "JOIN predictions p ON p.id=e.prediction_id WHERE p.match_id=?"
        )
        if active_only:
            query += " AND e.active=1"
        query += " ORDER BY e.id"
        with self.session() as con:
            rows = con.execute(query, (match_id,)).fetchall()
        return [dict(row) for row in rows]

    def save_weather_observation(
        self, match_id: int, weather: dict, retrieved_at_utc: datetime
    ) -> None:
        source_id = str(weather["source_id"])
        retrieved = retrieved_at_utc.isoformat()
        context = json.dumps(
            {"forecast_target_utc": weather["observed_for_utc"]}, sort_keys=True
        )
        metrics = {
            "temperature_c": "celsius",
            "precipitation_mm": "millimetres",
            "wind_speed_kmh": "km/h",
            "relative_humidity_pct": "%",
        }
        with self.session() as con:
            con.execute(
                "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
                "VALUES(?, 'weather_api', 'Open-Meteo', 'https://api.open-meteo.com/v1/forecast', ?, 'verified', 'Forecast for match context') "
                "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc=excluded.retrieved_at_utc, status=excluded.status",
                (source_id, retrieved),
            )
            for metric, unit in metrics.items():
                value = weather.get(metric)
                if value is None:
                    continue
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, value_number, value_text, unit, context_json, source_id, evidence_status, sample_size, observed_at_utc) "
                    "VALUES(?, 'event', 'match', ?, ?, NULL, ?, ?, ?, 'verified', 1, ?) "
                    "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) DO UPDATE SET "
                    "value_number=excluded.value_number, observed_at_utc=excluded.observed_at_utc",
                    (match_id, metric, float(value), unit, context, source_id, retrieved),
                )

    def sync_source_catalog(
        self, definitions: list[SourceDefinition], synced_at_utc: datetime
    ) -> None:
        synced = synced_at_utc.isoformat()
        with self.session() as con:
            for row in definitions:
                con.execute(
                    "INSERT INTO source_catalog(provider_id, label, bank, reliability, cost_tier, resource_tier, domains_json, freshness_hours, requires_credentials, notes, synced_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(provider_id) DO UPDATE SET label=excluded.label, bank=excluded.bank, reliability=excluded.reliability, "
                    "cost_tier=excluded.cost_tier, resource_tier=excluded.resource_tier, domains_json=excluded.domains_json, "
                    "freshness_hours=excluded.freshness_hours, requires_credentials=excluded.requires_credentials, notes=excluded.notes, synced_at_utc=excluded.synced_at_utc",
                    (
                        row.provider_id, row.label, row.bank, row.reliability,
                        row.cost_tier, row.resource_tier, json.dumps(row.domains),
                        row.freshness_hours, int(row.requires_credentials), row.notes, synced,
                    ),
                )

    def list_source_catalog(self) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM source_catalog ORDER BY bank, reliability DESC, provider_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_dataset_snapshot(
        self,
        provider_id: str,
        provider_version: str | None,
        content_sha256: str,
        checked_at_utc: datetime,
        data_updated_at_utc: datetime | None,
        row_count: int,
        status: str,
        error_message: str | None,
    ) -> int:
        with self.session() as con:
            con.execute(
                "INSERT INTO dataset_snapshots(provider_id, provider_version, content_sha256, checked_at_utc, data_updated_at_utc, row_count, status, error_message) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id, content_sha256) DO UPDATE SET checked_at_utc=excluded.checked_at_utc, "
                "provider_version=excluded.provider_version, data_updated_at_utc=excluded.data_updated_at_utc, "
                "row_count=excluded.row_count, status=excluded.status, error_message=excluded.error_message",
                (
                    provider_id, provider_version, content_sha256, checked_at_utc.isoformat(),
                    data_updated_at_utc.isoformat() if data_updated_at_utc else None,
                    int(row_count), status, error_message,
                ),
            )
            row = con.execute(
                "SELECT id FROM dataset_snapshots WHERE provider_id=? AND content_sha256=?",
                (provider_id, content_sha256),
            ).fetchone()
            return int(row["id"])

    def list_dataset_snapshots(self, provider_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM dataset_snapshots"
        params: tuple = ()
        if provider_id is not None:
            query += " WHERE provider_id=?"
            params = (provider_id,)
        query += " ORDER BY checked_at_utc DESC, id DESC"
        with self.session() as con:
            rows = con.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def record_dataset_refresh_check(
        self,
        provider_id: str,
        checked_at_utc: datetime,
        status: str,
        error_message: str | None,
    ) -> int:
        with self.session() as con:
            cursor = con.execute(
                "INSERT INTO dataset_refresh_checks(provider_id, checked_at_utc, status, error_message) VALUES(?, ?, ?, ?)",
                (provider_id, checked_at_utc.isoformat(), status, error_message),
            )
            return int(cursor.lastrowid)

    def list_dataset_refresh_checks(self, provider_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM dataset_refresh_checks"
        params: tuple = ()
        if provider_id is not None:
            query += " WHERE provider_id=?"
            params = (provider_id,)
        query += " ORDER BY checked_at_utc DESC, id DESC"
        with self.session() as con:
            rows = con.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def replace_current_world_cup_players(
        self, provider_id: str, rows: list[dict], imported_at_utc: datetime
    ) -> None:
        columns = (
            "position", "games", "starts", "minutes", "goals", "assists", "shots",
            "shots_on_target", "passes", "yellow_cards", "red_cards", "tackles_won",
            "interceptions", "save_percentage",
        )
        with self.session() as con:
            con.execute("DELETE FROM current_wc_player_stats WHERE provider_id=?", (provider_id,))
            for row in rows:
                if not row.get("player_name") or not row.get("team_name"):
                    continue
                con.execute(
                    "INSERT INTO current_wc_player_stats(provider_id, player_name, team_name, "
                    + ", ".join(columns)
                    + ", imported_at_utc) VALUES(?, ?, ?, "
                    + ", ".join("?" for _ in columns)
                    + ", ?)",
                    (
                        provider_id, row["player_name"], row["team_name"],
                        *(row.get(column) for column in columns), imported_at_utc.isoformat(),
                    ),
                )

    def list_current_world_cup_players(self, team_name: str | None = None) -> list[dict]:
        query = "SELECT * FROM current_wc_player_stats"
        params: tuple = ()
        if team_name is not None:
            query += " WHERE team_name=?"
            params = (team_name,)
        query += " ORDER BY team_name, minutes DESC, player_name"
        with self.session() as con:
            rows = con.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def replace_current_world_cup_teams(
        self, provider_id: str, rows: list[dict], imported_at_utc: datetime
    ) -> None:
        with self.session() as con:
            con.execute("DELETE FROM current_wc_team_stats WHERE provider_id=?", (provider_id,))
            for row in rows:
                if not row.get("team_name"):
                    continue
                con.execute(
                    "INSERT INTO current_wc_team_stats(provider_id, team_name, data_json, imported_at_utc) VALUES(?, ?, ?, ?)",
                    (provider_id, row["team_name"], json.dumps(row, ensure_ascii=False), imported_at_utc.isoformat()),
                )

    def replace_current_world_cup_matches(
        self, provider_id: str, rows: list[dict], imported_at_utc: datetime
    ) -> None:
        with self.session() as con:
            con.execute("DELETE FROM current_wc_match_stats WHERE provider_id=?", (provider_id,))
            scheduled = con.execute(
                "SELECT m.id, m.kickoff_utc, m.venue, ta.name AS team_a, tb.name AS team_b "
                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id"
            ).fetchall()
            # When the schedule is already seeded (CSV → 72 group fixtures),
            # never insert new fixtures from upstream feeds; only update the
            # ones we already have. Prevents swaptr knockout/duplicate
            # publications from inflating the match table in production.
            seed_locked = bool(
                con.execute(
                    "SELECT 1 FROM matches WHERE competition='FIFA World Cup 2026' LIMIT 1"
                ).fetchone()
            )
            teams = con.execute("SELECT id, name FROM teams").fetchall()
            for index, row in enumerate(rows):
                if not _known_fixture_team(row.get("team_a")) or not _known_fixture_team(row.get("team_b")):
                    continue
                match_key = f"{row.get('played_at') or index}|{row['team_a']}|{row['team_b']}"
                con.execute(
                    "INSERT INTO current_wc_match_stats(provider_id, match_key, data_json, imported_at_utc) VALUES(?, ?, ?, ?)",
                    (provider_id, match_key, json.dumps(row, ensure_ascii=False), imported_at_utc.isoformat()),
                )
                scheduled_match = _match_by_teams_near_date(
                    scheduled, str(row["team_a"]), str(row["team_b"]), str(row.get("played_at") or "")
                )
                # Try the reversed pair before inserting a duplicate. Some
                # upstream feeds swap home/away vs our seeded schedule
                # (this used to leak ~14 extra fixtures per refresh in cloud).
                reversed_seed = False
                if scheduled_match is None:
                    scheduled_match = _match_by_teams_near_date(
                        scheduled, str(row["team_b"]), str(row["team_a"]), str(row.get("played_at") or "")
                    )
                    reversed_seed = scheduled_match is not None
                kickoff = (
                    str(scheduled_match["kickoff_utc"])
                    if scheduled_match is not None
                    else row.get("kickoff_utc")
                )
                if kickoff:
                    team_ids = []
                    for team_name in (str(row["team_a"]), str(row["team_b"])):
                        existing = next((team for team in teams if same_team(str(team["name"]), team_name)), None)
                        if existing is None:
                            cursor = con.execute("INSERT INTO teams(name) VALUES(?)", (team_name,))
                            existing = {"id": int(cursor.lastrowid), "name": team_name}
                            teams = [*teams, existing]
                        team_ids.append(int(existing["id"]))
                    match_status = "finished" if row.get("goals_a") is not None and row.get("goals_b") is not None else (row.get("status") or "scheduled")
                    if reversed_seed:
                        # The seed already holds this pair with reversed home/away.
                        # Keep the seed's id and just update status/stage/venue.
                        con.execute(
                            "UPDATE matches SET stage=COALESCE(?, stage), status=?, venue=COALESCE(?, venue) WHERE id=?",
                            (row.get("stage"), match_status, row.get("venue"), int(scheduled_match["id"])),
                        )
                    elif scheduled_match is None and seed_locked:
                        # Seed schedule is the source of truth; ignore extra
                        # fixtures published by the daily feed (knockout
                        # brackets, alt placeholders, etc.).
                        pass
                    else:
                        con.execute(
                            "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                            "VALUES('FIFA World Cup 2026', ?, ?, ?, ?, ?, ?, 1) "
                            "ON CONFLICT(competition, kickoff_utc, team_a_id, team_b_id) DO UPDATE SET "
                            "stage=excluded.stage, status=excluded.status, venue=COALESCE(excluded.venue, matches.venue)",
                            (row.get("stage") or "FIFA World Cup 2026", kickoff, team_ids[0], team_ids[1], match_status, row.get("venue")),
                        )
                        if scheduled_match is None:
                            scheduled = con.execute(
                                "SELECT m.id, m.kickoff_utc, m.venue, ta.name AS team_a, tb.name AS team_b "
                                "FROM matches m JOIN teams ta ON ta.id=m.team_a_id JOIN teams tb ON tb.id=m.team_b_id"
                            ).fetchall()
                if row.get("goals_a") is None or row.get("goals_b") is None:
                    continue
                scheduled_match = _match_by_teams_near_date(
                    scheduled, str(row["team_a"]), str(row["team_b"]), str(row.get("played_at") or "")
                ) or scheduled_match
                played_at = (
                    str(scheduled_match["kickoff_utc"])
                    if scheduled_match is not None
                    else f"{str(row.get('played_at'))[:10]}T23:59:59+00:00"
                )
                con.execute(
                    "INSERT INTO historical_matches(played_at_utc, team_a_name, team_b_name, goals_a, goals_b, tournament, city, country, neutral_site, source_id, source_row_key) "
                    "VALUES(?, ?, ?, ?, ?, 'FIFA World Cup 2026', ?, NULL, 1, ?, ?) "
                    "ON CONFLICT(source_id, source_row_key) DO UPDATE SET goals_a=excluded.goals_a, goals_b=excluded.goals_b, played_at_utc=excluded.played_at_utc, city=excluded.city",
                    (
                        played_at, row["team_a"], row["team_b"], int(row["goals_a"]), int(row["goals_b"]),
                        (scheduled_match["venue"] if scheduled_match is not None else row.get("venue")),
                        provider_id, match_key,
                    ),
                )
                if scheduled_match is not None:
                    con.execute("UPDATE matches SET status='finished' WHERE id=?", (scheduled_match["id"],))

    def has_current_world_cup_matches(self, provider_id: str) -> bool:
        with self.session() as con:
            row = con.execute(
                "SELECT 1 FROM current_wc_match_stats WHERE provider_id=? LIMIT 1",
                (provider_id,),
            ).fetchone()
        return row is not None

    def save_sentiment_snapshot(self, snapshot: dict, created_at_utc: datetime) -> int:
        with self.session() as con:
            con.execute(
                "INSERT INTO sentiment_snapshots(match_id, provider_id, window_start_utc, window_end_utc, query, language, positive, neutral, negative, sample_size, sentiment_score, estimated_cost_usd, eligible_for_model, status, created_at_utc) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(match_id, provider_id, window_start_utc, window_end_utc, query, language) DO UPDATE SET "
                "positive=excluded.positive, neutral=excluded.neutral, negative=excluded.negative, sample_size=excluded.sample_size, "
                "sentiment_score=excluded.sentiment_score, estimated_cost_usd=excluded.estimated_cost_usd, status=excluded.status, created_at_utc=excluded.created_at_utc",
                (
                    snapshot["match_id"], snapshot["provider_id"], snapshot["window_start_utc"], snapshot["window_end_utc"],
                    snapshot["query"], snapshot["language"], snapshot["positive"], snapshot["neutral"], snapshot["negative"],
                    snapshot["sample_size"], snapshot["sentiment_score"], snapshot["estimated_cost_usd"],
                    int(snapshot.get("eligible_for_model", False)), snapshot["status"], created_at_utc.isoformat(),
                ),
            )
            row = con.execute(
                "SELECT id FROM sentiment_snapshots WHERE match_id=? AND provider_id=? AND window_start_utc=? AND window_end_utc=? AND query=? AND language=?",
                (snapshot["match_id"], snapshot["provider_id"], snapshot["window_start_utc"], snapshot["window_end_utc"], snapshot["query"], snapshot["language"]),
            ).fetchone()
            return int(row["id"])

    def list_sentiment_snapshots(self, match_id: int) -> list[dict]:
        with self.session() as con:
            rows = con.execute(
                "SELECT * FROM sentiment_snapshots WHERE match_id=? ORDER BY window_end_utc DESC",
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_player_performance_rows(self) -> list[dict]:
        with self.session() as con:
            daily = [
                dict(row)
                for row in con.execute(
                    "SELECT player_name, team_name, position, minutes, goals, assists, shots, "
                    "shots_on_target, passes, yellow_cards, tackles_won, interceptions, save_percentage "
                    "FROM current_wc_player_stats"
                ).fetchall()
            ]
            structured = [
                dict(row)
                for row in con.execute(
                    "SELECT p.name AS player_name, t.name AS team_name, p.position, "
                    "ps.minutes, ps.goals, ps.assists, ps.shots, ps.shots_on_target, ps.passes, ps.yellow_cards "
                    "FROM player_match_stats ps JOIN players p ON p.id=ps.player_id JOIN teams t ON t.id=p.team_id"
                ).fetchall()
            ]
            observation_rows = [
                dict(row)
                for row in con.execute(
                    "SELECT match_id, subject_name AS player_name, metric, value_number, context_json FROM observations "
                    "WHERE subject_type='player' AND evidence_status IN ('verified', 'verified_user_capture') AND value_number IS NOT NULL"
                ).fetchall()
            ]
        pivoted: dict[tuple[int, str], dict] = {}
        supported = {"minutes", "goals", "assists", "shots", "shots_on_target", "passes", "yellow_cards"}
        for row in observation_rows:
            if row["metric"] not in supported or not row["player_name"]:
                continue
            key = (int(row["match_id"]), str(row["player_name"]))
            try:
                context = json.loads(row.get("context_json") or "{}")
            except (TypeError, ValueError):
                context = {}
            target = pivoted.setdefault(
                key,
                {"player_name": row["player_name"], "team_name": context.get("team_name") or "Captura revisada"},
            )
            target[row["metric"]] = row["value_number"]
        return daily + structured + list(pivoted.values())
