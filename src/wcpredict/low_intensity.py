"""Detect WC2026 group-stage matchday-3 fixtures where one (or both)
teams had no sporting incentive — typical "rotation" / dead-rubber
matches whose stats over-represent the squad's resting eleven.

These matches keep distorting the team profile if we treat them like
any other tournament fixture: posesión drops, xG flattens, the
defensive shape barely shows. To prevent that we mark each affected
``(match_id, team_name)`` row and reduce its weight by 70% in
``historical_relevance.compute_match_weight``.

Detection (deliberately simple to keep false positives low):

  * The match is the THIRD chronological fixture of its group
    (i.e. the team has already played its first two MD1+MD2 games).
  * The team is *guaranteed top-2* on points alone in every remaining
    scenario (= already qualified) OR *guaranteed bottom-2* with no
    mathematical chance of catching the top-2 (= already eliminated).

We measure the qualifying margin via the same all-scenarios scan used
by ``group_context.draw_incentive_for_match``: enumerate the remaining
matches of the group and check whether the team's group rank stays in
{1,2} or stays in {3,4} across every product. With at most 1 unplayed
match per group on MD3 (the simultaneous fixture), the scan is O(3).
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product
import re


_GROUP_RE = re.compile(r"Group stage\s*-\s*Group\s+([A-L])\b", re.IGNORECASE)


def _group_letter(stage: str | None) -> str | None:
    if not stage:
        return None
    m = _GROUP_RE.search(stage)
    return m.group(1).upper() if m else None


def _add(points, gd, gf, home, away, ga, gb):
    points[home] += 3 if ga > gb else (1 if ga == gb else 0)
    points[away] += 3 if gb > ga else (1 if ga == gb else 0)
    gd[home] += ga - gb
    gd[away] += gb - ga
    gf[home] += ga
    gf[away] += gb


def _rank_after(team, points, gd, gf) -> int:
    # 1-indexed; lower is better. Ties broken on goal diff, then goals for.
    keyed = sorted(
        points.keys(),
        key=lambda t: (-points[t], -gd[t], -gf[t]),
    )
    return keyed.index(team) + 1


def _guaranteed_classification(team, fixtures, completed, this_match_id):
    """Return ``True`` if ``team`` is mathematically already top-2 OR
    bottom-2 regardless of remaining results (excluding ``this_match_id``)."""
    teams = set()
    for f in fixtures:
        teams.add(f["team_a"])
        teams.add(f["team_b"])
    if team not in teams:
        return False

    base_points = defaultdict(int)
    base_gd = defaultdict(int)
    base_gf = defaultdict(int)
    for t in teams:
        base_points[t] = 0
        base_gd[t] = 0
        base_gf[t] = 0
    for fid, (ga, gb, home, away) in completed.items():
        if fid == this_match_id:
            continue
        _add(base_points, base_gd, base_gf, home, away, ga, gb)

    pending = [
        (f["id"], f["team_a"], f["team_b"])
        for f in fixtures
        if f["id"] != this_match_id and f["id"] not in completed
    ]
    if len(pending) > 3:  # safety cap; group has at most 6 fixtures
        return False

    seen_top2 = True
    seen_bot2 = True
    for outcomes in product(("home", "draw", "away"), repeat=len(pending)):
        points = dict(base_points)
        gd = dict(base_gd)
        gf = dict(base_gf)
        for (_fid, home, away), outcome in zip(pending, outcomes):
            if outcome == "home":
                _add(points, gd, gf, home, away, 1, 0)
            elif outcome == "away":
                _add(points, gd, gf, home, away, 0, 1)
            else:
                _add(points, gd, gf, home, away, 0, 0)
        rank = _rank_after(team, points, gd, gf)
        if rank > 2:
            seen_top2 = False
        if rank <= 2:
            seen_bot2 = False
        if not seen_top2 and not seen_bot2:
            return False
    return seen_top2 or seen_bot2


def is_low_intensity_match(
    match, group_fixtures, completed_results, fixture_kickoff_by_id=None,
) -> tuple[bool, bool]:
    """Return ``(low_intensity_a, low_intensity_b)``.

    Parameters
    ----------
    match : Match
        The fixture to evaluate. Must have ``kickoff_utc``.
    group_fixtures : list[dict]
        All 6 fixtures of the group, each ``{id, team_a, team_b}``.
    completed_results : dict[match_id -> (ga, gb, home, away)]
        Results known. To avoid future-leakage we keep only the ones whose
        kickoff_utc is strictly before ``match.kickoff_utc`` (using
        ``fixture_kickoff_by_id`` when provided).
    fixture_kickoff_by_id : dict[int, datetime] | None
        Per-fixture kickoff for time filtering. If omitted, the test will
        use ``completed_results`` as-is (suitable for unit tests where the
        whole group is "known" without temporal ordering).
    """
    group = _group_letter(getattr(match, "stage", None))
    if group is None:
        return (False, False)

    fixtures = group_fixtures
    if len(fixtures) != 6:
        return (False, False)

    if fixture_kickoff_by_id is not None:
        own_kickoff = getattr(match, "kickoff_utc", None)
        if own_kickoff is None:
            return (False, False)
        filtered = {
            fid: payload for fid, payload in completed_results.items()
            if fid in fixture_kickoff_by_id
            and fixture_kickoff_by_id[fid] < own_kickoff
        }
    else:
        filtered = completed_results

    team_a = match.team_a.name
    team_b = match.team_b.name
    a_done = _guaranteed_classification(team_a, fixtures, filtered, match.id)
    b_done = _guaranteed_classification(team_b, fixtures, filtered, match.id)
    return (a_done, b_done)


def mark_low_intensity_rows(deep_rows: list[dict], low_intensity_pairs: set[tuple[str, str]]) -> list[dict]:
    """Tag deep observations whose ``(kickoff_utc, team_name)`` is in
    ``low_intensity_pairs``. Tagged rows carry ``_low_intensity = True``
    and ``historical_relevance.compute_match_weight`` multiplies by 0.30.
    """
    if not low_intensity_pairs:
        return deep_rows
    out = []
    for row in deep_rows:
        key = (str(row.get("kickoff_utc") or "")[:10], str(row.get("team_name") or ""))
        if key in low_intensity_pairs:
            new_row = dict(row)
            new_row["_low_intensity"] = True
            out.append(new_row)
        else:
            out.append(row)
    return out
