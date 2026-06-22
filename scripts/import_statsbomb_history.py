"""Back-fill per-team-per-match deep stats from StatsBomb Open Data.

Why
---
The JSON captures we maintain only cover the ongoing World Cup, so each team
arrives with 1-2 matches of profile data — far too little to give weight to
the per-team profile. StatsBomb publishes event-level data for the recent
major international tournaments (WC22/18, Euro 24/20, Copa América 24,
AFCON 23) for free. From those events we can derive every per-team-per-match
metric our model already uses, including **real xG** (the metric FBref does
not expose per match).

Coverage
--------
This script handles these StatsBomb-open competitions:

* FIFA World Cup 2022 (64 matches)
* FIFA World Cup 2018 (64 matches)
* UEFA Euro 2024 (51)
* UEFA Euro 2020 (51)
* CONMEBOL Copa América 2024 (32)
* CAF African Cup of Nations 2023 (52)

~314 matches total. Only matches where at least one side is a 2026 World
Cup participant are persisted, so noise from non-qualified teams is dropped.

Not covered by StatsBomb open data: AFC Asian Cup, OFC Nations Cup,
CONCACAF Gold Cup, World Cup qualifiers, Nations Leagues. Those gaps are
addressed in a follow-up pass (see TODO at the bottom of this file).

Storage
-------
Each metric is stored as a row in ``observations`` with
``evidence_status='verified_external'`` and a stable per-competition
``source_id`` so re-runs are idempotent.

Run
---
::

    python scripts/import_statsbomb_history.py
    # or limit to one competition while iterating:
    python scripts/import_statsbomb_history.py --max-competitions 1
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

try:
    from statsbombpy import sb
except ImportError:
    sys.stderr.write(
        "statsbombpy not installed. Run: python -m pip install statsbombpy\n"
    )
    raise

import pandas as pd
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="statsbombpy")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.names import canonical_team_name  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402


# (competition_id, season_id, friendly_name)
COMPETITIONS: list[tuple[int, int, str]] = [
    (43, 106, "FIFA World Cup 2022"),
    (43, 3, "FIFA World Cup 2018"),
    (55, 282, "UEFA Euro 2024"),
    (55, 43, "UEFA Euro 2020"),
    (223, 282, "CONMEBOL Copa América 2024"),
    (1267, 107, "CAF African Cup of Nations 2023"),
]


@dataclass
class Stats:
    matches_seen: int = 0
    matches_kept: int = 0
    matches_skipped_no_wc_team: int = 0
    matches_failed: int = 0
    observations_inserted: int = 0


def _load_wc_team_set(repo: Repository) -> set[str]:
    with sqlite3.connect(repo.path) as con:
        rows = con.execute("SELECT name FROM teams").fetchall()
    return {canonical_team_name(name) for (name,) in rows}


def _team_in_wc(name: str, wc_teams: set[str]) -> bool:
    return canonical_team_name(name) in wc_teams


def _parse_kickoff(date_str, time_str) -> datetime:
    s = f"{date_str}T{time_str or '12:00:00.000'}"[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _stable_source_id(competition_id: int, season_id: int) -> str:
    digest = sha1(f"statsbomb:{competition_id}:{season_id}".encode()).hexdigest()[:16]
    return f"statsbomb-{digest}"


def _ensure_source(repo: Repository, source_id: str, label: str, now: datetime) -> None:
    with sqlite3.connect(repo.path) as con:
        con.execute(
            "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
            "VALUES(?, 'statsbomb_open', ?, ?, ?, 'verified', ?) "
            "ON CONFLICT(id) DO UPDATE SET retrieved_at_utc = excluded.retrieved_at_utc",
            (
                source_id,
                f"StatsBomb {label}",
                "https://github.com/statsbomb/open-data",
                now.isoformat(),
                f"label={label}",
            ),
        )
        con.commit()


def _upsert_historical_match(
    repo: Repository,
    competition: str,
    team_a: str,
    team_b: str,
    kickoff: datetime,
) -> int:
    team_a_id = repo.upsert_team(team_a)
    team_b_id = repo.upsert_team(team_b)
    return repo.upsert_match(
        competition=competition,
        stage="historical",
        kickoff_utc=kickoff,
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        status="finished",
        venue=None,
    )


def _insert_observations(
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


# ---- Event aggregation --------------------------------------------------

def _get(d, *keys, default=None):
    """Safe nested lookup. Returns default if any key is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _shot_in_box(loc) -> bool:
    """StatsBomb pitch is 120×80. Penalty area = x∈[102,120], y∈[18,62].

    ``loc`` arrives as a list/tuple of [x, y] from StatsBomb. pandas may
    surface missing locations as float NaN (no len), so guard the type."""
    if not isinstance(loc, (list, tuple)) or len(loc) < 2:
        return False
    try:
        return loc[0] >= 102 and 18 <= loc[1] <= 62
    except (TypeError, ValueError):
        return False


def _aggregate_team_metrics(events: pd.DataFrame, team_name: str) -> dict[str, float]:
    """Reduce one team's events from a match to our internal metric keys."""
    own = events[events["team"] == team_name]
    others = events[events["team"] != team_name]
    if own.empty:
        return {}

    def cnt(mask) -> int:
        return int(mask.sum())

    # Some columns may not exist for every match (StatsBomb omits empty cols).
    def col(name) -> pd.Series:
        return own[name] if name in own.columns else pd.Series([None] * len(own), index=own.index)

    # ---- Shots ----------------------------------------------------------
    shots = own[own["type"] == "Shot"]
    xg_total = 0.0
    if not shots.empty and "shot_statsbomb_xg" in shots.columns:
        xg_total = float(shots["shot_statsbomb_xg"].fillna(0).sum())
    shot_outcomes = shots.get("shot_outcome", pd.Series([], dtype=object))
    on_target_outcomes = {"Goal", "Saved", "Saved To Post", "Saved Off Target"}
    shots_on_target = cnt(shot_outcomes.isin(on_target_outcomes))
    shots_off = cnt(shot_outcomes == "Off T")
    shots_blocked = cnt(shot_outcomes == "Blocked")
    shots_post = cnt(shot_outcomes.isin({"Post", "Saved To Post"}))
    shots_in_box = 0
    shots_out_box = 0
    big_chances = 0
    if "location" in shots.columns:
        for _, row in shots.iterrows():
            if _shot_in_box(row["location"]):
                shots_in_box += 1
            else:
                shots_out_box += 1
            if row.get("shot_statsbomb_xg", 0) and float(row["shot_statsbomb_xg"]) >= 0.30:
                big_chances += 1

    # ---- Passes ---------------------------------------------------------
    passes = own[own["type"] == "Pass"]
    passes_total = len(passes)
    # Successful pass = no `pass_outcome` (StatsBomb omits the field on success).
    passes_completed = cnt(~passes.get("pass_outcome", pd.Series([None] * len(passes), index=passes.index)).notna())
    long_attempts = 0
    long_completed = 0
    cross_attempts = 0
    cross_completed = 0
    final_third_completed = 0
    corners_taken = 0
    for _, row in passes.iterrows():
        length = row.get("pass_length")
        if length and length >= 30:  # StatsBomb yardage; ≈ "balón largo"
            long_attempts += 1
            if pd.isna(row.get("pass_outcome")):
                long_completed += 1
        if row.get("pass_cross") is True:
            cross_attempts += 1
            if pd.isna(row.get("pass_outcome")):
                cross_completed += 1
        end_loc = row.get("pass_end_location")
        if isinstance(end_loc, (list, tuple)) and len(end_loc) >= 1:
            try:
                if end_loc[0] >= 80 and pd.isna(row.get("pass_outcome")):
                    final_third_completed += 1
            except (TypeError, ValueError):
                pass
        if row.get("pass_type") == "Corner":
            corners_taken += 1

    # ---- Possession % (rough): own pass+carry / total --------------------
    own_touches = cnt(own["type"].isin(["Pass", "Carry"]))
    all_touches = cnt(events["type"].isin(["Pass", "Carry"]))
    possession_pct = round(100.0 * own_touches / all_touches, 1) if all_touches else None

    # ---- Defensive actions ----------------------------------------------
    tackles = cnt(own["type"] == "Duel")
    interceptions = cnt(own["type"] == "Interception")
    blocks = cnt(own["type"] == "Block")
    clearances = cnt(own["type"] == "Clearance")
    recoveries = cnt(own["type"] == "Ball Recovery")

    # ---- Goalkeeper -----------------------------------------------------
    gk = own[own["type"] == "Goal Keeper"]
    saves = 0
    big_saves = 0
    if not gk.empty and "goalkeeper_type" in gk.columns:
        saves = cnt(gk["goalkeeper_type"].isin(["Shot Saved", "Shot Saved Off Target", "Shot Saved To Post"]))
    # Goals prevented ≈ opponent's xG faced − goals conceded
    opp_shots = others[others["type"] == "Shot"]
    xg_against = float(opp_shots.get("shot_statsbomb_xg", pd.Series([0.0])).fillna(0).sum())
    goals_against = cnt(opp_shots.get("shot_outcome", pd.Series([], dtype=object)) == "Goal")
    goals_prevented = round(xg_against - goals_against, 2)

    # ---- Cards / fouls / offsides ---------------------------------------
    fouls = cnt(own["type"] == "Foul Committed")
    yellow = 0
    red = 0
    for _, row in own.iterrows():
        card_bad = _get(row.get("bad_behaviour"), "card", "name") if isinstance(row.get("bad_behaviour"), dict) else None
        card_foul = _get(row.get("foul_committed"), "card", "name") if isinstance(row.get("foul_committed"), dict) else None
        card = card_bad or card_foul
        if card == "Yellow Card":
            yellow += 1
        elif card == "Red Card" or card == "Second Yellow":
            red += 1
    offsides = cnt(own["type"] == "Offside")

    # ---- Touches in opponent box ----------------------------------------
    touches_in_box = 0
    if "location" in own.columns:
        for loc in own["location"]:
            if _shot_in_box(loc):
                touches_in_box += 1

    # ---- Duels (aerial vs ground) ---------------------------------------
    aerial_won = 0
    if "duel_type" in own.columns:
        aerial_won = cnt(own["duel_type"] == "Aerial Lost")  # SB labels "lost" but we count from the loser

    return {
        # Resumen
        "resumen_del_partido.goles_esperados_xg": round(xg_total, 3),
        "resumen_del_partido.tiros_totales": shots_in_box + shots_out_box if (shots_in_box + shots_out_box) else len(shots),
        "resumen_del_partido.saques_de_esquina": corners_taken,
        "resumen_del_partido.posesion_de_balon_pct": possession_pct,
        "resumen_del_partido.pases": passes_total,
        "resumen_del_partido.tarjetas_amarillas": yellow,
        "resumen_del_partido.tarjetas_rojas": red,
        "resumen_del_partido.faltas": fouls,
        "resumen_del_partido.ocasiones_claras": big_chances,
        "resumen_del_partido.paradas": saves,
        # Tiros
        "tiros.tiros_totales": len(shots),
        "tiros.tiros_a_puerta": shots_on_target,
        "tiros.tiros_dentro_del_area_penal": shots_in_box,
        "tiros.tiros_fuera_del_area": shots_out_box,
        "tiros.tiros_bloqueados": shots_blocked,
        "tiros.tiros_al_palo": shots_post,
        "tiros.tiros_fuera": shots_off,
        # Pases
        "pases.pases_precisos": passes_completed,
        "pases.pases_largos.intentados": long_attempts,
        "pases.pases_largos.completados": long_completed,
        "pases.pases_largos.porcentaje": (
            round(100.0 * long_completed / long_attempts, 1) if long_attempts else None
        ),
        "pases.centros.intentados": cross_attempts,
        "pases.centros.completados": cross_completed,
        "pases.centros.porcentaje": (
            round(100.0 * cross_completed / cross_attempts, 1) if cross_attempts else None
        ),
        "pases.pases_en_el_ultimo_tercio.completados": final_third_completed,
        # Defensa
        "defensa.tackles_totales": tackles,
        "defensa.intercepciones": interceptions,
        "defensa.despejes": clearances,
        "defensa.recuperaciones": recoveries,
        # Ataque
        "ataque.toques_dentro_del_area": touches_in_box,
        "ataque.fueras_de_juego": offsides,
        # Portería
        "porteria.paradas": saves,
        "porteria.goles_evitados": goals_prevented,
        # Duelos (best-effort; SB encoding is fragile)
        "duelos.duelos_aereos.ganados": aerial_won,
    }


# ---- Driver -------------------------------------------------------------

def _ingest_competition(
    repo: Repository,
    competition_id: int,
    season_id: int,
    label: str,
    wc_teams: set[str],
    stats: Stats,
) -> None:
    print(f"\n=== {label} ===")
    matches = sb.matches(competition_id=competition_id, season_id=season_id)
    print(f"  partidos en torneo: {len(matches)}")
    now = datetime.now(timezone.utc)
    source_id = _stable_source_id(competition_id, season_id)
    _ensure_source(repo, source_id, label, now)

    for _, m in matches.iterrows():
        stats.matches_seen += 1
        home, away = str(m["home_team"]), str(m["away_team"])
        if not (_team_in_wc(home, wc_teams) or _team_in_wc(away, wc_teams)):
            stats.matches_skipped_no_wc_team += 1
            continue
        kickoff = _parse_kickoff(m.get("match_date"), m.get("kick_off"))
        try:
            events = sb.events(match_id=int(m["match_id"]))
        except Exception as exc:
            stats.matches_failed += 1
            print(f"  events fetch failed {home} vs {away}: {exc}")
            continue
        if events.empty:
            stats.matches_failed += 1
            continue
        try:
            match_id = _upsert_historical_match(repo, label, home, away, kickoff)
        except Exception as exc:
            stats.matches_failed += 1
            print(f"  match upsert failed {home} vs {away}: {exc}")
            continue
        kept = False
        for team in (home, away):
            metrics = _aggregate_team_metrics(events, team)
            if not metrics:
                continue
            stats.observations_inserted += _insert_observations(
                repo, match_id, team, metrics, source_id, now
            )
            kept = True
        if kept:
            stats.matches_kept += 1
        if stats.matches_kept % 10 == 0 and kept:
            print(f"  ... {stats.matches_kept} partidos guardados")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=str(ROOT / "data" / "worldcup.sqlite"),
        help="Path to the SQLite DB",
    )
    parser.add_argument(
        "--max-competitions",
        type=int,
        default=0,
        help="Only ingest the first N competitions (useful for dry runs)",
    )
    args = parser.parse_args()

    repo = Repository(Path(args.db))
    wc_teams = _load_wc_team_set(repo)
    if not wc_teams:
        print("No teams in DB; seed the schedule first.")
        return 1
    print(f"Selecciones Mundial 2026 en BD: {len(wc_teams)}")

    stats = Stats()
    plan = COMPETITIONS if not args.max_competitions else COMPETITIONS[: args.max_competitions]
    for comp_id, season_id, label in plan:
        try:
            _ingest_competition(repo, comp_id, season_id, label, wc_teams, stats)
        except KeyboardInterrupt:
            print("Interrumpido por el usuario.")
            return 130
        except Exception as exc:
            print(f"  {label} fatal: {exc}")

    print("\n========= RESUMEN =========")
    print(f"Partidos vistos:                  {stats.matches_seen}")
    print(f"Partidos guardados (WC team):     {stats.matches_kept}")
    print(f"Partidos saltados (sin WC team):  {stats.matches_skipped_no_wc_team}")
    print(f"Partidos con error:               {stats.matches_failed}")
    print(f"Observaciones insertadas:         {stats.observations_inserted}")
    return 0


# ----------------------------------------------------------------------
# TODO — Future coverage of competitions not in StatsBomb open data:
#   * AFC Asian Cup → consider Sofascore JSON API (used by their site, not
#     officially documented) or a paid API like API-Football (~$10/mo).
#   * OFC Nations Cup → very limited sources; manual CSV from Wikipedia
#     may be the only path.
#   * CONCACAF Gold Cup / Nations League → same problem; check API-Football
#     coverage before paying.
#   * WC Qualifiers per confederation → FBref has them but per-match xG
#     isn't exposed via FBref's team-level tables (only via per-match
#     scraping of individual match reports, ~1500-2000 requests).
# A plausible v2 path: pay API-Football for one month, run a one-shot
# back-fill of everything missing, then cancel. That single fee fills the
# gap completely with ~30 min of work.
# ----------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(main())
