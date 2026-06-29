import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.knockout_bracket import (
    COMPETITION,
    _group_standings,
    _load_annex_c_table,
    bracket_view,
    list_bracket_slots,
    resolve_knockout_bracket,
    seed_knockout_bracket,
)
from wcpredict.knockout_model import predict_knockout_match
from wcpredict.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
KNOCKOUT_CSV = ROOT / "data" / "fixtures" / "world_cup_2026_knockouts.csv"


def _seed_group(repo: Repository, group: str, teams: list[str]) -> dict[str, int]:
    """Create 4 teams + 6 matches for a group, marking them all finished so
    the standings can be derived deterministically."""
    ids = {name: repo.upsert_team(name) for name in teams}
    schedule = [
        (teams[0], teams[1], 2, 0),
        (teams[2], teams[3], 1, 1),
        (teams[0], teams[2], 1, 1),
        (teams[3], teams[1], 0, 2),
        (teams[2], teams[1], 0, 1),
        (teams[0], teams[3], 3, 0),
    ]
    with sqlite3.connect(repo.path) as con:
        for idx, (a, b, ga, gb) in enumerate(schedule):
            kickoff = f"2026-06-1{idx + 1}T18:00:00+00:00"
            con.execute(
                "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                "VALUES(?, ?, ?, ?, ?, 'finished', NULL, 1)",
                (COMPETITION, f"Group stage - Group {group}", kickoff, ids[a], ids[b]),
            )
            mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                "VALUES(?, ?, ?, 'manual', ?)",
                (mid, ga, gb, datetime.now(timezone.utc).isoformat()),
            )
        con.commit()
    return ids


def _insert_group_result(
    repo: Repository,
    group: str,
    ids: dict[str, int],
    team_a: str,
    team_b: str,
    goals_a: int,
    goals_b: int,
    idx: int,
) -> None:
    with sqlite3.connect(repo.path) as con:
        con.execute(
            "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
            "VALUES(?, ?, ?, ?, ?, 'finished', NULL, 1)",
            (
                COMPETITION,
                f"Group stage - Group {group}",
                f"2026-06-{idx:02d}T18:00:00+00:00",
                ids[team_a],
                ids[team_b],
            ),
        )
        mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
            "VALUES(?, ?, ?, 'manual', ?)",
            (mid, goals_a, goals_b, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def _seed_partial_group_schedule(
    repo: Repository,
    group: str,
    teams: list[str],
    finished: list[tuple[str, str, int, int]],
) -> dict[str, int]:
    ids = {name: repo.upsert_team(name) for name in teams}
    fixtures = [
        (teams[0], teams[1]),
        (teams[2], teams[3]),
        (teams[0], teams[2]),
        (teams[1], teams[3]),
        (teams[0], teams[3]),
        (teams[1], teams[2]),
    ]
    results = {(a, b): (ga, gb) for a, b, ga, gb in finished}
    with sqlite3.connect(repo.path) as con:
        for idx, (a, b) in enumerate(fixtures, start=1):
            result = results.get((a, b))
            status = "finished" if result is not None else "scheduled"
            con.execute(
                "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                "VALUES(?, ?, ?, ?, ?, ?, NULL, 1)",
                (
                    COMPETITION,
                    f"Group stage - Group {group}",
                    f"2026-06-{idx:02d}T18:00:00+00:00",
                    ids[a],
                    ids[b],
                    status,
                ),
            )
            if result is not None:
                mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                    "VALUES(?, ?, ?, 'manual', ?)",
                    (mid, result[0], result[1], datetime.now(timezone.utc).isoformat()),
                )
        con.commit()
    return ids


class KnockoutBracketTests(unittest.TestCase):
    def test_seed_inserts_all_thirty_two_slots(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            n = seed_knockout_bracket(repo, KNOCKOUT_CSV)
        self.assertEqual(32, n)

    def test_resolution_does_nothing_before_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            summary = resolve_knockout_bracket(repo)
        self.assertEqual(0, summary["resolved"])
        self.assertEqual(0, summary["matches_created"])

    def test_resolution_fills_seconds_only_slot_when_two_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            # M73 wants 2A vs 2B. Seed both groups.
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            _seed_group(repo, "B", ["Canada", "Bosnia and Herzegovina", "Germany", "Japan"])
            summary = resolve_knockout_bracket(repo)
            self.assertGreaterEqual(summary["resolved"], 1)
            self.assertGreaterEqual(summary["matches_created"], 1)
            view = bracket_view(repo)
            m73 = next(slot for slot in view if slot["slot_id"] == "M73")
            self.assertFalse(m73["home_pending"])
            self.assertFalse(m73["away_pending"])

    def test_third_place_slots_stay_pending_until_all_groups_finished(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")
            self.assertFalse(m79["home_pending"])
            self.assertEqual("Mexico", m79["home"])
            # 1A could be resolved but the 3{CEFHI} side requires all 12 groups.
            self.assertTrue(m79["away_pending"])
            # Pretty label should mention the third's source groups.
            self.assertIn("3.º de", m79["away"])

    def test_partial_slot_side_updates_without_waiting_for_third_place_assignment(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            summary = resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")
            self.assertGreaterEqual(summary["resolved"], 1)
            self.assertFalse(m79["home_pending"])
            self.assertTrue(m79["away_pending"])
            self.assertIsNone(m79["match_id"])

    def test_mathematically_clinched_group_winner_fills_before_group_is_complete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            ids = {
                name: repo.upsert_team(name)
                for name in ("Mexico", "South Africa", "Spain", "Korea Republic")
            }
            _insert_group_result(repo, "A", ids, "Mexico", "South Africa", 1, 0, 1)
            _insert_group_result(repo, "A", ids, "Mexico", "Spain", 2, 0, 2)
            _insert_group_result(repo, "A", ids, "Mexico", "Korea Republic", 2, 1, 3)
            _insert_group_result(repo, "A", ids, "South Africa", "Spain", 0, 0, 4)
            _insert_group_result(repo, "A", ids, "South Africa", "Korea Republic", 1, 1, 5)
            resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")
            self.assertEqual("Mexico", m79["home"])
            self.assertFalse(m79["home_pending"])
            self.assertTrue(m79["away_pending"])

    def test_two_wins_and_head_to_head_can_clinch_group_winner_after_matchday_two(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_partial_group_schedule(
                repo,
                "A",
                ["Mexico", "South Africa", "Spain", "Korea Republic"],
                [
                    ("Mexico", "South Africa", 2, 0),
                    ("Spain", "Korea Republic", 1, 1),
                    ("Mexico", "Spain", 1, 0),
                    ("South Africa", "Korea Republic", 2, 0),
                ],
            )

            summary = resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")

            self.assertGreaterEqual(summary["resolved"], 1)
            self.assertEqual("Mexico", m79["home"])
            self.assertFalse(m79["home_pending"])
            self.assertTrue(m79["away_pending"])

    def test_group_winner_clinch_accounts_for_remaining_rivals_playing_each_other(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_partial_group_schedule(
                repo,
                "A",
                ["Mexico", "South Africa", "Spain", "Korea Republic"],
                [
                    ("Mexico", "South Africa", 2, 0),
                    ("Spain", "Korea Republic", 1, 0),
                    ("Mexico", "Spain", 1, 0),
                    ("South Africa", "Korea Republic", 2, 0),
                ],
            )

            summary = resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")

            self.assertGreaterEqual(summary["resolved"], 1)
            self.assertEqual("Mexico", m79["home"])
            self.assertFalse(m79["home_pending"])
            self.assertTrue(m79["away_pending"])

    def test_bracket_view_shows_clinched_group_winner_even_before_persisted_resolution(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_partial_group_schedule(
                repo,
                "A",
                ["Mexico", "South Africa", "Spain", "Korea Republic"],
                [
                    ("Mexico", "South Africa", 2, 0),
                    ("Spain", "Korea Republic", 1, 0),
                    ("Mexico", "Spain", 1, 0),
                    ("South Africa", "Korea Republic", 2, 0),
                ],
            )

            view = bracket_view(repo)
            m79 = next(slot for slot in view if slot["slot_id"] == "M79")

            self.assertEqual("Mexico", m79["home"])
            self.assertFalse(m79["home_pending"])
            self.assertTrue(m79["away_pending"])

    def test_resolution_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            _seed_group(repo, "B", ["Canada", "Bosnia and Herzegovina", "Germany", "Japan"])
            resolve_knockout_bracket(repo)
            again = resolve_knockout_bracket(repo)
        self.assertEqual(0, again["matches_created"])

    def test_resolution_repairs_stage_overwritten_by_schedule_provider(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            _seed_group(repo, "A", ["Mexico", "South Africa", "Spain", "Korea Republic"])
            _seed_group(repo, "B", ["Canada", "Bosnia and Herzegovina", "Germany", "Japan"])
            resolve_knockout_bracket(repo)
            slot = next(row for row in list_bracket_slots(repo) if row.slot_id == "M73")
            with sqlite3.connect(repo.path) as con:
                con.execute(
                    "UPDATE matches SET competition='FIFA World Cup 2026', stage='FIFA World Cup' WHERE id=?",
                    (slot.match_id,),
                )
                con.commit()

            resolve_knockout_bracket(repo)

            with sqlite3.connect(repo.path) as con:
                repaired = con.execute(
                    "SELECT competition, stage FROM matches WHERE id=?", (slot.match_id,)
                ).fetchone()
        self.assertEqual((COMPETITION, "Round of 32"), repaired)

    def test_group_standings_use_head_to_head_before_goal_difference(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            ids = {name: repo.upsert_team(name) for name in ("Alpha", "Beta", "Gamma", "Delta")}
            fixtures = [
                ("Alpha", "Beta", 1, 0),
                ("Alpha", "Gamma", 0, 3),
                ("Alpha", "Delta", 1, 0),
                ("Beta", "Gamma", 5, 0),
                ("Beta", "Delta", 1, 0),
                ("Gamma", "Delta", 0, 0),
            ]
            with sqlite3.connect(repo.path) as con:
                con.row_factory = sqlite3.Row
                for idx, (a, b, ga, gb) in enumerate(fixtures, start=1):
                    con.execute(
                        "INSERT INTO matches(competition, stage, kickoff_utc, team_a_id, team_b_id, status, venue, neutral_site) "
                        "VALUES(?, 'Group stage - Group A', ?, ?, ?, 'finished', NULL, 1)",
                        (COMPETITION, f"2026-06-{idx:02d}T18:00:00+00:00", ids[a], ids[b]),
                    )
                    mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                    con.execute(
                        "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, recorded_at_utc) "
                        "VALUES(?, ?, ?, 'manual', ?)",
                        (mid, ga, gb, datetime.now(timezone.utc).isoformat()),
                    )
                con.commit()

                standings = _group_standings(con, "A")

        self.assertEqual(["Alpha", "Beta"], [row[1] for row in standings[:2]])


class AnnexCAssignmentTests(unittest.TestCase):
    """Verify the bipartite assignment of the 8 best 3rd-placed teams to the
    eight specific slots that take a third (M74/M77/M79/M80/M81/M82/M85/M87)."""

    def test_current_bdefijkl_combo_pairs_belgium_with_i_and_switzerland_with_j(self):
        table = _load_annex_c_table()
        combo = frozenset("BDEFIJKL")
        self.assertIn(combo, table)
        mapping = table[combo]
        self.assertEqual("I", mapping["1G"])
        self.assertEqual("J", mapping["1B"])

    def test_assignment_returns_empty_until_all_twelve_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            for letter in "ABCD":
                _seed_group(repo, letter, [f"{letter}1", f"{letter}2", f"{letter}3", f"{letter}4"])
            summary = resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            # No slot needing a third should have its third side resolved yet.
            for slot_id in ("M74", "M77", "M79", "M80", "M81", "M82", "M85", "M87"):
                slot = next(s for s in view if s["slot_id"] == slot_id)
                self.assertTrue(slot["away_pending"],
                                f"{slot_id} third resolved with only 4 groups finished")

    def test_assignment_completes_when_all_twelve_groups_finish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            for letter in "ABCDEFGHIJKL":
                _seed_group(repo, letter, [f"{letter}1", f"{letter}2", f"{letter}3", f"{letter}4"])
            resolve_knockout_bracket(repo)
            view = bracket_view(repo)
            # All 16 R32 slots should be filled (both sides resolved).
            for slot_id in (f"M{n}" for n in range(73, 89)):
                slot = next(s for s in view if s["slot_id"] == slot_id)
                self.assertFalse(slot["home_pending"],
                                 f"{slot_id} home unresolved after all groups finished")
                self.assertFalse(slot["away_pending"],
                                 f"{slot_id} away unresolved after all groups finished")

    def test_resolution_repairs_stale_persisted_third_place_assignment(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = Repository(Path(tmp) / "app.sqlite")
            repo.initialize()
            seed_knockout_bracket(repo, KNOCKOUT_CSV)
            for letter in "ABCDEFGHIJKL":
                _seed_group(
                    repo,
                    letter,
                    [f"{letter}1", f"{letter}2", f"{letter}3", f"{letter}4"],
                )
            resolve_knockout_bracket(repo)
            slots = {row.slot_id: row for row in list_bracket_slots(repo)}
            original_view = {row["slot_id"]: row for row in bracket_view(repo)}
            expected_m82 = original_view["M82"]["away"]
            expected_m85 = original_view["M85"]["away"]
            m82, m85 = slots["M82"], slots["M85"]
            with sqlite3.connect(repo.path) as con:
                con.execute(
                    "UPDATE knockout_bracket SET away_team_id=? WHERE id=?",
                    (m85.away_team_id, m82.id),
                )
                con.execute(
                    "UPDATE knockout_bracket SET away_team_id=? WHERE id=?",
                    (m82.away_team_id, m85.id),
                )
                con.execute(
                    "UPDATE matches SET team_b_id=? WHERE id=?",
                    (m85.away_team_id, m82.match_id),
                )
                con.execute(
                    "UPDATE matches SET team_b_id=? WHERE id=?",
                    (m82.away_team_id, m85.match_id),
                )
                con.commit()

            resolve_knockout_bracket(repo)

            view = {row["slot_id"]: row for row in bracket_view(repo)}
            self.assertEqual(expected_m82, view["M82"]["away"])
            self.assertEqual(expected_m85, view["M85"]["away"])


class KnockoutModelTests(unittest.TestCase):
    def test_advance_probabilities_sum_to_one(self):
        pred = predict_knockout_match(1.55, 1.30, dispersion=0.08, rho=-0.16)
        self.assertAlmostEqual(pred.home_advances + pred.away_advances, 1.0, places=5)

    def test_better_team_advances_more_often(self):
        pred = predict_knockout_match(2.0, 0.8, dispersion=0.08, rho=-0.16)
        self.assertGreater(pred.home_advances, 0.65)

    def test_even_match_close_to_fifty(self):
        pred = predict_knockout_match(1.4, 1.4, dispersion=0.08, rho=-0.16)
        self.assertAlmostEqual(pred.home_advances, 0.5, delta=0.02)

    def test_methods_sum_to_total_advance(self):
        pred = predict_knockout_match(1.8, 1.2, dispersion=0.08, rho=-0.16)
        home_total = pred.home_wins_90 + pred.home_wins_et + pred.home_wins_penalties
        self.assertAlmostEqual(home_total, pred.home_advances, places=5)


if __name__ == "__main__":
    unittest.main()
