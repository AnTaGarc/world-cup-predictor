"""A/B gate for opponent normalization of volume metrics.

For every closed WC2026 match with actual team stats recorded, rebuild both
teams' profiles strictly pre-kickoff, twice (raw vs normalized), price the
over/under lines with predict_team_volume_markets and compare the Brier
score per metric against reality. Metrics that improve activate in
OPPONENT_NORMALIZED_METRICS; the rest stay raw.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from wcpredict.names import canonical_team_name
from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profiles
from wcpredict.team_volume_markets import MARKET_CATALOG, predict_team_volume_markets

from backfill_live_residuals import (
    _closed_matches,
    _index_by_team,
    _preload_deep_rows,
    _sample_slice,
    _team_slice,
)

ALL_METRIC_KEYS = frozenset(spec["metric"] for spec in MARKET_CATALOG.values())
ACCEPTED = ("verified", "verified_user_json", "verified_user_capture", "verified_external")


def _actuals(con) -> dict[tuple[int, str, str], float]:
    placeholders = ", ".join(f"'{status}'" for status in ACCEPTED)
    rows = con.execute(
        "SELECT o.match_id, o.subject_name, o.metric, o.value_number "
        "FROM observations o JOIN ("
        "  SELECT MAX(id) AS id FROM observations "
        f" WHERE subject_type='team' AND evidence_status IN ({placeholders}) "
        "  AND value_number IS NOT NULL AND period='full_match' "
        "  GROUP BY match_id, subject_name, metric"
        ") latest ON latest.id = o.id"
    ).fetchall()
    return {
        (int(r["match_id"]), canonical_team_name(str(r["subject_name"])), str(r["metric"])): float(r["value_number"])
        for r in rows
        if str(r["metric"]) in ALL_METRIC_KEYS
    }


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()
    con = sqlite3.connect(repo.path)
    con.row_factory = sqlite3.Row
    closed = _closed_matches(con)
    actuals = _actuals(con)
    all_deep = _preload_deep_rows(repo)
    deep_by_team = _index_by_team(all_deep)
    sample = all_deep[::40]

    brier = {"raw": defaultdict(list), "norm": defaultdict(list)}
    for index, m in enumerate(closed, 1):
        kickoff = datetime.fromisoformat(m["kickoff_utc"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        as_of = kickoff - timedelta(minutes=5)
        as_of_iso = as_of.isoformat()
        rel_deep = (
            _team_slice(deep_by_team, m["team_a"], as_of_iso)
            + _team_slice(deep_by_team, m["team_b"], as_of_iso)
            + _sample_slice(sample, as_of_iso)
        )
        for variant, metrics_set in (("raw", frozenset()), ("norm", ALL_METRIC_KEYS)):
            profiles = build_team_profiles(
                (m["team_a"], m["team_b"]), rel_deep, as_of,
                normalized_metrics=metrics_set,
            )
            lines = predict_team_volume_markets(
                profiles[m["team_a"]], profiles[m["team_b"]]
            )
            for line in lines:
                spec = MARKET_CATALOG.get(line.market)
                if spec is None:
                    continue
                actual = actuals.get(
                    (int(m["id"]), canonical_team_name(line.team_name), spec["metric"])
                )
                if actual is None:
                    continue
                outcome = 1.0 if actual > line.line else 0.0
                brier[variant][line.market].append((line.over_probability - outcome) ** 2)
        if index % 20 == 0:
            print(f"  ...{index}/{len(closed)}", flush=True)

    print(f"\n{'métrica':18s} {'n':>5s} {'brier raw':>10s} {'brier norm':>11s} {'mejora':>8s}")
    activate = []
    for market in MARKET_CATALOG:
        raw_scores = brier["raw"].get(market, [])
        norm_scores = brier["norm"].get(market, [])
        if not raw_scores:
            print(f"{market:18s} {'0':>5s}      (sin actuals)")
            continue
        raw_mean = sum(raw_scores) / len(raw_scores)
        norm_mean = sum(norm_scores) / len(norm_scores)
        improvement = 100.0 * (raw_mean - norm_mean) / raw_mean if raw_mean else 0.0
        flag = ""
        if improvement > 0.5:
            activate.append(market)
            flag = "  <- ACTIVAR"
        print(f"{market:18s} {len(raw_scores):>5d} {raw_mean:>10.5f} {norm_mean:>11.5f} {improvement:>+7.2f}%{flag}")
    print("\nactivar:", [MARKET_CATALOG[m]['metric'] for m in activate])
    return 0


if __name__ == "__main__":
    sys.exit(main())
