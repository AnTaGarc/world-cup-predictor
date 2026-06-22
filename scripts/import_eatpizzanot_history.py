"""Back-fill per-team per-match deep stats from the eatpizzanot/soccer-dataset
public Parquet/CSV release.

Why
---
StatsBomb open-data covers only ~310 matches across 6 major tournaments.
The eatpizzanot dataset (https://github.com/eatpizzanot/soccer-dataset) adds:

* UEFA Nations League (660 matches, 99.7% with xG)
* World Cup qualifiers — Europe (628), CONMEBOL (209), CONCACAF (242)
* Asian Cup, AFCON (already in StatsBomb, here as second-source enrichment)
* Euro qualifiers (239)
* International Friendlies (2877, filtered to WC26 teams)

About 5800 international matches, almost all with xG. Together with the
StatsBomb backfill we go from ~140 to ~3000 deep-stat matches for the 48
WC 2026 selections.

The dataset stores per-team stats as pre-aggregated columns (home_xg,
away_xg, home_corners, away_corners, …) so no event aggregation is needed —
just a column-rename + insert.

Run
---
First download three CSVs (small, fast)::

    mkdir -p data/external
    curl -sSL -o data/external/eatpizzanot_leagues.csv \
        https://raw.githubusercontent.com/eatpizzanot/soccer-dataset/main/csv/leagues.csv
    curl -sSL -o data/external/eatpizzanot_fixtures.csv \
        https://raw.githubusercontent.com/eatpizzanot/soccer-dataset/main/csv/fixtures.csv
    curl -sSL -o data/external/eatpizzanot_match_stats.csv \
        https://raw.githubusercontent.com/eatpizzanot/soccer-dataset/main/csv/match_stats.csv

Then::

    python scripts/import_eatpizzanot_history.py

The script is idempotent — re-runs only update changed observations.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.names import canonical_team_name  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402

EXTERNAL_DIR = ROOT / "data" / "external"


# Map eatpizzanot league_id → friendly competition label used in the matches
# table. Only the international comps we care about — the dataset also has
# club leagues that we deliberately skip.
LEAGUE_FILTER: dict[int, str] = {
    78: "FIFA World Cup",
    79: "UEFA Euro",
    80: "UEFA Nations League",
    81: "Copa America",
    82: "Africa Cup of Nations",
    83: "AFC Asian Cup",
    84: "WC Qualification UEFA",
    85: "WC Qualification CONMEBOL",
    86: "WC Qualification CONCACAF",
    87: "Euro Qualification",
    88: "International Friendly",
}


# Pre-aggregated columns → our internal metric keys. The dataset uses the
# pattern `{home,away}_{metric}` so we map the metric stem once and apply
# it to both sides per row.
COLUMN_MAP: dict[str, str] = {
    "xg": "resumen_del_partido.goles_esperados_xg",
    "shots_total": "resumen_del_partido.tiros_totales",
    "shots_on_goal": "tiros.tiros_a_puerta",
    "shots_inside_box": "tiros.tiros_dentro_del_area_penal",
    "shots_outside_box": "tiros.tiros_fuera_del_area",
    "blocked_shots": "tiros.tiros_bloqueados",
    "corners": "resumen_del_partido.saques_de_esquina",
    "yellow_cards": "resumen_del_partido.tarjetas_amarillas",
    "red_cards": "resumen_del_partido.tarjetas_rojas",
    "possession": "resumen_del_partido.posesion_de_balon_pct",
    "fouls": "resumen_del_partido.faltas",
    "offsides": "ataque.fueras_de_juego",
    "pass_accuracy": "pases.pases_largos.porcentaje",
    # Note: the dataset has only one pass-accuracy column, not split by long/
    # short. We reuse the long-passes-% slot since most of our model uses
    # the long-pass family for "build-up" features.
}


@dataclass
class Stats:
    fixtures_seen: int = 0
    fixtures_kept: int = 0
    fixtures_skipped_no_wc_team: int = 0
    fixtures_no_stats: int = 0
    fixtures_failed: int = 0
    observations_inserted: int = 0


def _load_wc_team_set(repo: Repository) -> set[str]:
    with sqlite3.connect(repo.path) as con:
        rows = con.execute("SELECT name FROM teams").fetchall()
    return {canonical_team_name(name) for (name,) in rows}


def _team_in_wc(name: str, wc_teams: set[str]) -> bool:
    return canonical_team_name(name) in wc_teams


def _stable_source_id(league_id: int) -> str:
    digest = sha1(f"eatpizzanot:{league_id}".encode()).hexdigest()[:16]
    return f"eatpizzanot-{digest}"


def _ensure_source(repo: Repository, source_id: str, label: str, now: datetime) -> None:
    with sqlite3.connect(repo.path) as con:
        con.execute(
            "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
            "VALUES(?, 'eatpizzanot_csv', ?, ?, ?, 'verified', ?) "
            "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc = excluded.retrieved_at_utc",
            (
                source_id,
                f"eatpizzanot — {label}",
                "https://github.com/eatpizzanot/soccer-dataset",
                now.isoformat(),
                f"league_id={label}",
            ),
        )
        con.commit()


def _insert_team_observations(
    repo: Repository,
    match_id: int,
    team_name: str,
    metrics: dict[str, float],
    source_id: str,
    observed_at: datetime,
) -> int:
    inserted = 0
    with sqlite3.connect(repo.path) as con:
        for metric, value in metrics.items():
            if value is None:
                continue
            try:
                fval = float(value)
            except (TypeError, ValueError):
                continue
            if pd.isna(fval):
                continue
            try:
                con.execute(
                    "INSERT INTO observations(match_id, subject_type, subject_name, metric, "
                    "value_number, value_text, unit, context_json, source_id, evidence_status, "
                    "sample_size, observed_at_utc) "
                    "VALUES(?, 'team', ?, ?, ?, NULL, NULL, '{}', ?, 'verified_external', 1, ?) "
                    "ON CONFLICT(match_id, subject_type, subject_name, metric, context_json, source_id) "
                    "DO UPDATE SET value_number = excluded.value_number",
                    (match_id, team_name, metric, fval, source_id, observed_at.isoformat()),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        con.commit()
    return inserted


def _parse_kickoff(date_str) -> datetime | None:
    if pd.isna(date_str):
        return None
    s = str(date_str)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:19] if "T" in s else s[:10], fmt[:19] if "T" in fmt else fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _extract_row_metrics(row: pd.Series, side: str) -> dict[str, float]:
    """Pull all per-side stats from one merged row. `side` is 'home' or 'away'."""
    out: dict[str, float] = {}
    for stem, metric_key in COLUMN_MAP.items():
        col = f"{side}_{stem}"
        if col not in row.index:
            continue
        value = row[col]
        if pd.notna(value):
            out[metric_key] = float(value)
    # Some metrics deserve mirroring under tiros.tiros_totales (the JSON deep
    # files store the same number under two keys).
    if "resumen_del_partido.tiros_totales" in out:
        out["tiros.tiros_totales"] = out["resumen_del_partido.tiros_totales"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=str(ROOT / "data" / "worldcup.sqlite"),
        help="Path to the SQLite DB",
    )
    parser.add_argument(
        "--external-dir",
        default=str(EXTERNAL_DIR),
        help="Where the eatpizzanot CSVs were downloaded to",
    )
    args = parser.parse_args()

    ext = Path(args.external_dir)
    required = ["eatpizzanot_leagues.csv", "eatpizzanot_fixtures.csv", "eatpizzanot_match_stats.csv"]
    missing = [f for f in required if not (ext / f).exists()]
    if missing:
        print(f"Missing CSVs in {ext}: {missing}")
        print("Download them per the docstring at the top of this file.")
        return 1

    repo = Repository(Path(args.db))
    wc_teams = _load_wc_team_set(repo)
    if not wc_teams:
        print("No teams in DB; seed the schedule first.")
        return 1
    print(f"Selecciones Mundial 2026 en BD: {len(wc_teams)}")

    print("Cargando CSVs eatpizzanot…")
    leagues = pd.read_csv(ext / "eatpizzanot_leagues.csv")
    fixtures = pd.read_csv(ext / "eatpizzanot_fixtures.csv", low_memory=False)
    match_stats = pd.read_csv(ext / "eatpizzanot_match_stats.csv", low_memory=False)
    teams_csv = ext / "eatpizzanot_teams.csv"
    teams_df = pd.read_csv(teams_csv) if teams_csv.exists() else None
    print(f"  leagues={len(leagues)}  fixtures={len(fixtures)}  match_stats={len(match_stats)}")

    # Merge fixtures with match_stats (inner — we need stats present)
    merged = fixtures.merge(match_stats, left_on="id", right_on="fixture_id", how="inner")
    # Restrict to the international competitions we care about
    merged = merged[merged["league_id"].isin(LEAGUE_FILTER.keys())].copy()
    print(f"  fixtures con stats en comps de interés: {len(merged)}")

    # Resolve team names via teams.csv if present
    if teams_df is not None:
        team_lookup = dict(zip(teams_df["id"], teams_df["name"]))
        merged["home_team_name"] = merged["home_team_id"].map(team_lookup)
        merged["away_team_name"] = merged["away_team_id"].map(team_lookup)
    else:
        # Fall back: try to download teams.csv on the fly
        import urllib.request
        url = "https://raw.githubusercontent.com/eatpizzanot/soccer-dataset/main/csv/teams.csv"
        print(f"  teams.csv missing locally; fetching {url}")
        urllib.request.urlretrieve(url, ext / "eatpizzanot_teams.csv")
        teams_df = pd.read_csv(ext / "eatpizzanot_teams.csv")
        team_lookup = dict(zip(teams_df["id"], teams_df["name"]))
        merged["home_team_name"] = merged["home_team_id"].map(team_lookup)
        merged["away_team_name"] = merged["away_team_id"].map(team_lookup)

    stats = Stats()
    now = datetime.now(timezone.utc)

    for league_id, label in LEAGUE_FILTER.items():
        league_rows = merged[merged["league_id"] == league_id]
        if league_rows.empty:
            continue
        print(f"\n=== {label} ({len(league_rows)} fixtures) ===")
        source_id = _stable_source_id(league_id)
        _ensure_source(repo, source_id, label, now)
        kept_in_league = 0
        for _, row in league_rows.iterrows():
            stats.fixtures_seen += 1
            home = row.get("home_team_name")
            away = row.get("away_team_name")
            if pd.isna(home) or pd.isna(away):
                stats.fixtures_failed += 1
                continue
            home, away = str(home), str(away)
            if not (_team_in_wc(home, wc_teams) or _team_in_wc(away, wc_teams)):
                stats.fixtures_skipped_no_wc_team += 1
                continue
            kickoff = _parse_kickoff(row.get("date"))
            if kickoff is None:
                stats.fixtures_failed += 1
                continue
            try:
                ta = repo.upsert_team(home)
                tb = repo.upsert_team(away)
                match_id = repo.upsert_match(
                    competition=label,
                    stage="historical",
                    kickoff_utc=kickoff,
                    team_a_id=ta,
                    team_b_id=tb,
                    status="finished",
                    venue=None,
                )
            except Exception as exc:
                stats.fixtures_failed += 1
                continue
            home_metrics = _extract_row_metrics(row, "home")
            away_metrics = _extract_row_metrics(row, "away")
            if not home_metrics and not away_metrics:
                stats.fixtures_no_stats += 1
                continue
            n = 0
            n += _insert_team_observations(repo, match_id, home, home_metrics, source_id, now)
            n += _insert_team_observations(repo, match_id, away, away_metrics, source_id, now)
            stats.observations_inserted += n
            stats.fixtures_kept += 1
            kept_in_league += 1
        print(f"  guardados en {label}: {kept_in_league}")

    print("\n========= RESUMEN =========")
    print(f"Fixtures vistos:                  {stats.fixtures_seen}")
    print(f"Fixtures guardados:               {stats.fixtures_kept}")
    print(f"Fixtures saltados (sin WC team):  {stats.fixtures_skipped_no_wc_team}")
    print(f"Fixtures sin métricas válidas:    {stats.fixtures_no_stats}")
    print(f"Fixtures con error:               {stats.fixtures_failed}")
    print(f"Observaciones insertadas:         {stats.observations_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
