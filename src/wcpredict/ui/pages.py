from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hashlib import sha256
from html import escape
from zoneinfo import ZoneInfo
import json
import os
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

from wcpredict.backtesting import brier_score, calibration_bands, summarize_by_market_family, calibration_drift
from wcpredict.collector_store import CollectorEventBundle, CollectorStore
from wcpredict.group_context import draw_incentive_for_match
from wcpredict.market_catalog import default_market_rows, normalize_market_rows
from wcpredict.models import MarketFamily
from wcpredict.odds import compare_odds_to_probability
from wcpredict.odds import parse_odds_csv
from wcpredict.outcome_ml import current_match_features, load_outcome_model, match_results_to_feature_rows
from wcpredict.player_markets import (
    GOALKEEPER_MARKETS,
    PLAYER_MARKET_METRICS,
    derive_player_assumption,
    estimate_player_market_probability,
    is_goalkeeper,
)
from wcpredict.player_analytics import build_player_profiles, cluster_player_styles
from wcpredict.ratings import MatchResult, build_team_ratings
from wcpredict.refresh import refresh_match
from wcpredict.repository import Repository
from wcpredict.schedule import seed_schedule
from wcpredict.knockout_bracket import (
    bracket_view,
    resolve_knockout_bracket,
    seed_knockout_bracket,
)
from wcpredict.knockout_model import predict_knockout_match
from wcpredict.penalty_history_model import build_penalty_match_context
from wcpredict.services import MarketPrediction, predict_match_markets
from wcpredict.source_catalog import default_source_catalog
from wcpredict.daily_refresh import DEFAULT_PROVIDERS, DatasetDownload, ensure_current_world_cup_data
from wcpredict.world_cup_data import fetch_kaggle_world_cup_dataset, import_world_cup_download
from wcpredict.advanced_form import (
    build_goalkeeper_baseline,
    build_volume_rate_observations,
    build_xg_form_adjustment,
)
from wcpredict.calibration import build_calibration_samples, summarise_bias
from wcpredict.model_corrections import (
    ModelCorrections,
    describe_corrections,
    derive_corrections,
    is_active as corrections_active,
)
from wcpredict.audit import (
    SEVERITY_COLORS,
    audit_rows_to_records,
    build_match_audit,
    build_per_team_audit,
)
from wcpredict.squad_context import apply_squad_context
from wcpredict.names import canonical_team_name, same_team
from wcpredict.deep_match_import import load_deep_match_file
from wcpredict.ui.postmatch_capture import render_capture_review
from wcpredict.ui.crests import crest_html, team_with_crest_html
from wcpredict.ui.theme import callout, empty_state, hero, probability_bar, section_note, status_pill
from wcpredict.ui.translations import (
    canonical_market,
    canonical_market_family,
    canonical_selection,
    localize_cost_tier,
    localize_market,
    localize_market_family,
    localize_metric,
    localize_model,
    localize_resource_tier,
    localize_selection,
    localize_status,
    localize_table_columns,
)
from wcpredict.ui.view_models import (
    coverage_summary,
    dataset_freshness_rows,
    ev_rows,
    model_comparison_rows,
    model_policy_rows,
    postmatch_queue_message,
    prediction_rows,
    probability_chart_rows,
)
from wcpredict.volume_markets import estimate_total_market


ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
DATABASE_PATH = DATA_DIR / "worldcup.sqlite"
SCHEDULE_PATH = DATA_DIR / "fixtures" / "world_cup_2026_schedule.csv"
KNOCKOUT_PATH = DATA_DIR / "fixtures" / "world_cup_2026_knockouts.csv"
WORKSPACE_ROOT = ROOT.parent
SPORTS_DATA_DIR = WORKSPACE_ROOT / "sports-data"
SPORTS_DB_PATH = SPORTS_DATA_DIR / "sports.db"
OUTCOME_MODEL_PATH = DATA_DIR / "models" / "outcome_ml.joblib"
DEEP_OUTCOME_MODEL_PATH = DATA_DIR / "models" / "outcome_ml_deep.joblib"
OPEN_SCHEDULE_PATH = DATA_DIR / "open" / "martj42-results.csv"
DAILY_PROVIDERS = (*DEFAULT_PROVIDERS, "martj42_world_schedule")
HOST_TEAMS = {"USA", "Canada", "Mexico"}
PREDICTION_ENGINE_VERSION = "2026-06-25-draw-incentive-v1"
DISPLAY_TZ = ZoneInfo("Europe/Madrid")


def _display_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(DISPLAY_TZ)


def _display_time(value: datetime | str, fmt: str) -> str:
    return _display_dt(value).strftime(fmt)


def _host_factor(team_name: str) -> float:
    return 1.10 if canonical_team_name(team_name) in HOST_TEAMS else 1.0


def _team_strengths(results, as_of_date) -> dict[str, dict[str, float]]:
    return {
        team_name: {"attack": rating.attack, "defense": rating.defense}
        for team_name, rating in build_team_ratings(results, as_of_date).items()
    }


def _historical_rows_to_results(rows: list[dict]) -> list[MatchResult]:
    results = []
    for row in rows:
        tournament = str(row.get("tournament") or "").lower()
        match_type = (
            "world_cup" if "world cup" in tournament
            else "friendly" if "friendly" in tournament
            else "competitive"
        )
        results.append(
            MatchResult(
                datetime.fromisoformat(str(row["played_at_utc"])).date(),
                str(row["team_a"]),
                str(row["team_b"]),
                int(row["goals_a"]),
                int(row["goals_b"]),
                match_type,
            )
        )
    return results


@st.cache_resource(show_spinner=False)
def _repo() -> Repository:
    repo = Repository(DATABASE_PATH)
    repo.initialize()
    if SCHEDULE_PATH.exists():
        seed_schedule(repo, SCHEDULE_PATH)
    if KNOCKOUT_PATH.exists():
        seed_knockout_bracket(repo, KNOCKOUT_PATH)
        # Best-effort resolution on cold start; safe to call when no group is
        # finished yet (returns 0 resolved). Re-runs each time the data tab
        # finalises a match so brackets bubble up automatically.
        try:
            resolve_knockout_bracket(repo)
        except Exception:
            pass
    if OPEN_SCHEDULE_PATH.exists() and not repo.has_current_world_cup_matches("martj42_local_schedule"):
        content = OPEN_SCHEDULE_PATH.read_bytes()
        import_world_cup_download(
            repo,
            DatasetDownload(
                "martj42_local_schedule", "local/parser-4", content,
                datetime.fromtimestamp(OPEN_SCHEDULE_PATH.stat().st_mtime, timezone.utc),
                max(0, content.count(b"\n") - 1),
            ),
            datetime.now(timezone.utc),
        )
    return repo


def _file_signature(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _db_signature() -> tuple[int, int]:
    sig = _file_signature(DATABASE_PATH)
    return sig if sig is not None else (0, 0)


def _sports_db_signature() -> tuple[int, int]:
    sig = _file_signature(SPORTS_DB_PATH)
    return sig if sig is not None else (0, 0)


@st.cache_resource(show_spinner=False)
def _load_outcome_model_cached(path: str, signature: tuple[int, int] | None):
    if signature is None:
        return None
    model_path = Path(path)
    return load_outcome_model(model_path) if model_path.exists() else None


@st.cache_resource(show_spinner=False)
def _load_deep_outcome_model_cached(path: str, signature: tuple[int, int] | None):
    """Lazy-load the deep-stats 1X2 classifier (HistGBM). Returns None when
    the artifact hasn't been trained yet so callers can fall back to the
    Elo-only model alone."""
    if signature is None:
        return None
    from wcpredict.outcome_ml_deep import load_deep_model
    model_path = Path(path)
    if not model_path.exists():
        return None
    try:
        return load_deep_model(model_path)
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _store_cached() -> CollectorStore:
    return CollectorStore(SPORTS_DB_PATH)


def _store() -> CollectorStore:
    return _store_cached()


@st.cache_resource(show_spinner=False)
def _matches_cached(db_sig: tuple[int, int]):
    # Uses cache_resource (not cache_data) because Match holds nested Team
    # dataclasses with datetime fields that recent Streamlit versions refuse
    # to serialize for cache_data. The list is immutable per db signature so
    # caching as a resource is safe.
    return _repo().list_matches()


def _list_matches():
    """Return matches the UI cares about — i.e. the World Cup 2026 fixtures.

    The DB now also holds ~4k historical matches from the StatsBomb +
    eatpizzanot back-fills used to train the deep-stats classifier. Those
    should NOT appear in the schedule selectboxes, dashboard counters or
    backtesting panel — they're training data, not part of the tournament.
    """
    return [m for m in _matches_cached(_db_signature()) if m.competition == "FIFA World Cup 2026"]


@st.cache_resource(show_spinner=False)
def _collector_bundle_cached(
    team_a: str, team_b: str, date_iso: str, sports_db_sig: tuple[int, int]
) -> CollectorEventBundle | None:
    from datetime import date as _date
    return _store_cached().find_event(team_a, team_b, _date.fromisoformat(date_iso))


@st.cache_resource(show_spinner=False)
def _calibration_summary_cached(db_sig: tuple[int, int]):
    return summarize_by_market_family(_repo().list_all_backtests())


@st.cache_resource(show_spinner=False)
def _freshness_rows_cached(db_sig: tuple[int, int], minute_bucket: str):
    repo = _repo()
    return dataset_freshness_rows(
        repo.list_dataset_snapshots(),
        repo.list_dataset_refresh_checks(),
        datetime.now(timezone.utc),
    )


def _freshness_rows_now():
    bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    return _freshness_rows_cached(_db_signature(), bucket)


@st.cache_resource(show_spinner=False)
def _all_evidence_statuses_cached(db_sig: tuple[int, int]) -> dict[int, dict]:
    return _repo().get_all_match_evidence_statuses()


def _all_evidence_statuses() -> dict[int, dict]:
    return _all_evidence_statuses_cached(_db_signature())


@st.cache_resource(show_spinner=False)
def _deep_obs_counts_cached(db_sig: tuple[int, int]) -> dict[int, int]:
    return _repo().count_deep_observations_by_match()


@st.cache_resource(show_spinner=False)
def _import_runs_cached(db_sig: tuple[int, int]) -> dict[int, bool]:
    return _repo().has_import_runs_by_match()


@st.cache_resource(show_spinner=False, ttl=3600)
def _low_intensity_pairs_cached(db_sig: tuple[int, int]) -> set[tuple[str, str]]:
    """Return ``{(YYYY-MM-DD, team_name)}`` for every WC2026 group fixture
    where that team was already mathematically classified/eliminated before
    the match. Used to down-weight rotated-XI matchday-3 dead-rubbers.

    Computed once per cache miss; the set is tiny (≤96 entries even if
    every team had at least one dead-rubber side per group).
    """
    from wcpredict.low_intensity import is_low_intensity_match, _group_letter
    repo = _repo()
    matches = _matches_cached(db_sig)
    # Filter to WC2026 group-stage matches with a date.
    group_matches = [
        m for m in matches
        if m.competition == "FIFA World Cup 2026" and _group_letter(m.stage)
    ]
    # Index fixtures per group.
    by_group: dict[str, list] = {}
    for m in group_matches:
        g = _group_letter(m.stage)
        by_group.setdefault(g, []).append(m)
    # Load WC2026 results once.
    with sqlite3.connect(repo.path) as con:
        con.row_factory = sqlite3.Row
        results_rows = list(con.execute(
            "SELECT mr.match_id, mr.goals_a, mr.goals_b, ta.name a, tb.name b "
            "FROM match_results mr "
            "JOIN matches m ON m.id=mr.match_id "
            "JOIN teams ta ON ta.id=m.team_a_id "
            "JOIN teams tb ON tb.id=m.team_b_id "
            "WHERE m.competition='FIFA World Cup 2026'"
        ))
    completed_by_id = {
        int(r["match_id"]): (int(r["goals_a"]), int(r["goals_b"]), r["a"], r["b"])
        for r in results_rows
    }
    pairs: set[tuple[str, str]] = set()
    for group, fixtures in by_group.items():
        if len(fixtures) != 6:
            continue
        group_fixture_dicts = [
            {"id": f.id, "team_a": f.team_a.name, "team_b": f.team_b.name}
            for f in fixtures
        ]
        kickoff_by_id = {f.id: f.kickoff_utc for f in fixtures}
        # Only matches with a final result feed the team profile, so we
        # only need to flag those.
        finished = [f for f in fixtures if f.id in completed_by_id]
        for fx in finished:
            a_low, b_low = is_low_intensity_match(
                fx, group_fixture_dicts, completed_by_id,
                fixture_kickoff_by_id=kickoff_by_id,
            )
            date_key = fx.kickoff_utc.date().isoformat()
            if a_low:
                pairs.add((date_key, fx.team_a.name))
            if b_low:
                pairs.add((date_key, fx.team_b.name))
    return pairs


@st.cache_resource(show_spinner=False, ttl=3600)
def _load_team_shifts_cached(db_sig: tuple[int, int]) -> dict[str, dict[str, float]]:
    """Load per-team 1X2 shifts from two stacked sources.

    1. ``historical-pool-v1``: ~440 partidos 2022-2026 entre selecciones
       del WC2026. Built offline; covers nearly every team out of the box.
    2. ``live-wc2026-v1``: residuos guardados automáticamente cada vez que
       se cierra un partido del Mundial (settle_match_versioned). Cada
       residuo live cuenta TRIPLE (replicado 3 veces) frente al histórico
       para reflejar que es muestra de la competición activa.

    Returns ``{team_name: {'1X2': logit_shift}}``. Negative shift means
    the model historically over-estimated that team (apply downward).
    """
    from wcpredict.team_corrections import compute_team_market_shifts
    repo = _repo()
    rows = []
    try:
        with sqlite3.connect(repo.path) as con:
            con.row_factory = sqlite3.Row
            # Historical pool (one row per query, sourced from historical_matches).
            for r in con.execute(
                "SELECT br.market, br.selection, br.prob_predicted, br.outcome_observed, "
                "hm.team_a_name AS team_a, hm.team_b_name AS team_b "
                "FROM backtest_runs br "
                "JOIN historical_matches hm ON hm.id = -br.match_id "
                "WHERE br.run_label='historical-pool-v1'"
            ):
                rows.append(dict(r))
            # Live WC2026 residuals: weighted 3× by inserting each row thrice.
            # This is the simplest stable EMA-equivalent: the per-team mean of
            # residuals stays mathematically the same as a 3:1 weighted blend
            # between live and historical, without needing a custom weight in
            # compute_team_market_shifts.
            for r in con.execute(
                "SELECT br.market, br.selection, br.prob_predicted, br.outcome_observed, "
                "ta.name AS team_a, tb.name AS team_b "
                "FROM backtest_runs br "
                "JOIN matches m ON m.id=br.match_id "
                "JOIN teams ta ON ta.id=m.team_a_id "
                "JOIN teams tb ON tb.id=m.team_b_id "
                "WHERE br.run_label='live-wc2026-v1'"
            ):
                row = dict(r)
                rows.extend([row, row, row])
    except Exception:
        return {}
    if not rows:
        return {}
    raw = compute_team_market_shifts(
        rows, prior_strength=4.0, min_n=2, market_filter=("1X2",),
    )
    by_team: dict[str, dict[str, float]] = {}
    for (team, _market), shift in raw.items():
        # Negative residual (model under-predicts) → positive logit shift.
        by_team.setdefault(team, {})["1X2"] = -shift
    return by_team


@st.cache_resource(show_spinner=False, ttl=3600)
def _calibration_bias_report_cached(db_sig: tuple[int, int]):
    """Recompute the global-bias report for all finished matches with deep stats."""
    repo = _repo()
    # Gather inputs from a single connection.
    with sqlite3.connect(repo.path) as con:
        con.row_factory = sqlite3.Row
        finished = [
            dict(row) for row in con.execute(
                "SELECT m.id, m.kickoff_utc, ta.name AS team_a, tb.name AS team_b "
                "FROM matches m "
                "JOIN teams ta ON ta.id=m.team_a_id "
                "JOIN teams tb ON tb.id=m.team_b_id "
                "JOIN match_results mr ON mr.match_id=m.id "
                "JOIN team_match_stats s ON s.match_id=m.id "
                "GROUP BY m.id ORDER BY m.kickoff_utc"
            ).fetchall()
        ]
        match_results = {
            int(row["match_id"]): {"goals_a": row["goals_a"], "goals_b": row["goals_b"]}
            for row in con.execute("SELECT match_id, goals_a, goals_b FROM match_results").fetchall()
        }
        stats_rows = con.execute(
            "SELECT s.match_id, t.name AS team_name, s.xg, s.shots, s.shots_on_target "
            "FROM team_match_stats s JOIN teams t ON t.id=s.team_id"
        ).fetchall()
    stats_by_match: dict[int, dict[str, dict]] = {}
    for row in stats_rows:
        stats_by_match.setdefault(int(row["match_id"]), {})[row["team_name"]] = dict(row)
    historical = repo.list_historical_results_before(
        datetime.now(timezone.utc) + timedelta(days=365),
    )
    local = repo.list_match_results_before(
        datetime.now(timezone.utc) + timedelta(days=365),
    )
    deep_rows = repo.list_deep_xg_rows_before(
        datetime.now(timezone.utc) + timedelta(days=365),
    )
    samples = build_calibration_samples(
        finished_matches=finished,
        historical_results=historical + local,
        deep_rows=deep_rows,
        team_match_stats_by_match=stats_by_match,
        match_results_by_match=match_results,
    )
    return samples, summarise_bias(samples)


def _calibration_bias_report():
    return _calibration_bias_report_cached(_db_signature())


def _corrections_enabled() -> bool:
    return bool(st.session_state.get("apply_corrections", False))


def _active_corrections() -> ModelCorrections | None:
    """Return the corrections derived from the current bias report, but only if
    the toggle in the Calibración page is ON."""
    if not _corrections_enabled():
        return None
    try:
        _, report = _calibration_bias_report()
    except Exception:
        return None
    corrections = derive_corrections(report)
    return corrections if corrections_active(corrections) else None


@st.cache_resource(show_spinner=False)
def _player_intelligence_rows_cached(db_sig: tuple[int, int], minimum_minutes: int):
    repo = _repo()
    # Always load with min_minutes=0 so absolute-count rankings (goals,
    # assists, shots) can include short-time impact players (e.g. Undav with
    # 3 goals in 58'). The slider only filters the Impacto ranking, where
    # per-90 stability matters.
    profiles = build_player_profiles(
        repo.list_player_performance_rows(), min_minutes=0
    )
    clustered = cluster_player_styles(profiles[:120], requested_clusters=4)
    styles = {
        (row["player_name"], row["team_name"]): {
            "style_cluster": row["style_cluster"],
            "style_label": row["style_label"],
        }
        for row in clustered
    }
    return [
        {**row, **styles.get((row["player_name"], row["team_name"]), {})}
        for row in profiles
    ]


@st.fragment
def _render_player_panel(
    frame: pd.DataFrame,
    metric: str,
    title: str,
    total_col: str,
    total_label: str,
    rate_col: str | None,
    rate_label: str | None,
    minimum_minutes: int = 0,
) -> None:
    """Render one ranking panel inside the Jugadores tab.

    Wrapped in ``st.fragment`` so that the search input and sort radio
    inside this panel only re-run THIS panel's body on interaction, not the
    whole player-intelligence view (which had to rebuild every other tab's
    HTML on every keystroke — the source of the lag the user reported).

    The ``minimum_minutes`` slider only applies to the Impacto ranking,
    where per-90 percentile needs a minutes floor to stay stable. Absolute
    counters (goals/assists/shots) always show anyone with the relevant
    counter > 0 so short-impact players (e.g. Undav 3 goles / 58 min)
    don't disappear from the goal-scorers list.
    """
    if metric not in frame:
        st.info(f"La fuente actual no publica datos suficientes para {title.lower()}.")
        return
    if metric == "impact":
        st.caption(
            "Escala 0-100 (percentil del jugador dentro de su posición). "
            "Cada rol pondera lo suyo: delanteros premia goles/tiros, medios "
            "asistencias/pases, defensas tackles+despejes, porteros % paradas. "
            "El slider de minutos solo filtra la vista; la puntuación es estable."
        )
    subset = frame[frame[metric].notna()]
    if metric == "impact" and minimum_minutes > 0 and "minutes" in subset:
        subset = subset[subset["minutes"] >= minimum_minutes]
    if rate_col and total_col in subset:
        subset = subset[subset[total_col] > 0]
    if rate_col:
        sort_col1, sort_col2 = st.columns([2, 1])
        with sort_col1:
            search = st.text_input(
                "Buscar jugador", key=f"search_{metric}",
                placeholder="Nombre del jugador…", label_visibility="collapsed",
            ).strip()
        with sort_col2:
            sort_choice = st.radio(
                "Ordenar por", [total_label, rate_label], horizontal=True,
                key=f"sort_{metric}", label_visibility="collapsed",
            )
        if search:
            mask = (
                subset["player_name"].astype(str).str.contains(search, case=False, na=False)
                | subset["team_name"].astype(str).str.contains(search, case=False, na=False)
            )
            subset = subset[mask]
        if sort_choice == total_label:
            ranked = subset.sort_values([total_col, rate_col], ascending=[False, False]).head(50)
        else:
            ranked = subset.sort_values([rate_col, total_col], ascending=[False, False]).head(50)
    else:
        ranked = subset.sort_values(metric, ascending=False).head(30)
    if ranked.empty:
        empty_state("Sin resultados", "Ningún jugador coincide con el filtro.", icon="🔍")
        return
    _render_player_ranking_table(ranked, total_col, total_label, rate_col, rate_label)
    # Chart in an expander — Altair rendering is the heaviest step and most
    # users don't need to expand the bar chart every interaction.
    with st.expander(f"📊 Gráfico — top 15 por {title.lower()}"):
        chart = alt.Chart(ranked.head(15)).mark_bar(cornerRadiusEnd=4, color="#1769E0").encode(
            y=alt.Y("player_name:N", sort="-x", title=None),
            x=alt.X(f"{metric}:Q", title=title),
            tooltip=["player_name", "team_name", "minutes", alt.Tooltip(f"{metric}:Q", format=".2f")],
        ).properties(height=360)
        st.altair_chart(chart, width="stretch")


_POSITION_LABEL = {"ATT": "Delantero", "MID": "Medio", "DEF": "Defensa", "GK": "Portero"}


def _render_player_ranking_table(
    ranked: pd.DataFrame,
    total_col: str,
    total_label: str,
    rate_col: str | None,
    rate_label: str | None,
) -> None:
    """Render a player ranking as an HTML table with crests inline next to the
    team name. The Impacto tab also shows the player's position label so the
    user can see why a goalkeeper or defender ranks where they do (they're
    scored against players of the same role, not against strikers)."""
    from wcpredict.ui.crests import team_with_crest_html
    total_is_integer = total_col in {"goals", "assists", "shots"}
    show_position = total_col == "impact"
    rows_html = []
    for _, row in ranked.iterrows():
        team_cell = team_with_crest_html(str(row.get("team_name") or ""), size=20)
        total_value = row.get(total_col)
        if total_value is None or pd.isna(total_value):
            total_display = "—"
        elif total_is_integer:
            total_display = str(int(total_value))
        else:
            total_display = f"{float(total_value):.1f}" if total_col == "impact" else f"{float(total_value):.2f}"
        cells = [f'<td class="pt-name">{row.get("player_name") or ""}</td>']
        if show_position:
            pos_label = _POSITION_LABEL.get(str(row.get("position_group") or ""), "")
            cells.append(f'<td class="pt-pos">{pos_label}</td>')
        cells.extend([
            f'<td class="pt-team">{team_cell}</td>',
            f'<td class="pt-num">{int(row.get("minutes") or 0)}</td>',
            f'<td class="pt-num">{int(row.get("matches") or 0)}</td>',
            f'<td class="pt-num pt-strong">{total_display}</td>',
        ])
        if rate_col:
            rate_value = row.get(rate_col)
            cells.append(
                f'<td class="pt-num">{rate_value:.2f}</td>'
                if rate_value is not None and not pd.isna(rate_value) else '<td class="pt-num">—</td>'
            )
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    header_cells = ['<th>Jugador</th>']
    if show_position:
        header_cells.append('<th>Posición</th>')
    header_cells.extend([
        '<th>Selección</th>', '<th>Minutos</th>', '<th>Partidos</th>',
        f'<th>{total_label}</th>',
    ])
    if rate_label:
        header_cells.append(f'<th>{rate_label}</th>')
    table_html = (
        '<div class="player-table-wrap"><table class="player-table">'
        '<thead><tr>' + "".join(header_cells) + '</tr></thead>'
        '<tbody>' + "".join(rows_html) + '</tbody></table></div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)


def _visible_frame(data) -> pd.DataFrame:
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    if frame.empty:
        return frame
    return pd.DataFrame(localize_table_columns(frame.to_dict(orient="records")))


@st.cache_resource(ttl=900, show_spinner=False)
def _refresh_current_world_cup_banks_cached(
    refresh_bucket: str,
    providers: tuple[str, ...],
):
    repo = Repository(DATABASE_PATH)
    repo.initialize()
    now = datetime.now(timezone.utc)
    return ensure_current_world_cup_data(
        repo,
        fetch_kaggle_world_cup_dataset,
        importer=lambda download: import_world_cup_download(repo, download, now),
        now=now,
        providers=providers,
    )


def _refresh_current_world_cup_banks(repo: Repository):
    return _refresh_current_world_cup_banks_cached(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
        tuple(DAILY_PROVIDERS),
    )


def _resolve_bracket_after_daily_refresh(repo: Repository, daily_result) -> None:
    if not getattr(daily_result, "updated", ()):
        return
    try:
        resolve_knockout_bracket(repo, getattr(daily_result, "checked_at", None))
    except Exception:
        pass


def _force_refresh_players(repo: Repository):
    """Bypass the 24-hour freshness check and re-fetch the player bank now.

    Used by the explicit 'Actualizar datos de jugadores' button. Returns the
    DailyRefreshResult so the UI can show what happened.
    """
    # Mark the player-provider refresh-check as expired so ensure_current_world_cup_data
    # doesn't short-circuit on the 24h max_age window.
    now = datetime.now(timezone.utc)
    with sqlite3.connect(repo.path) as con:
        con.execute(
            "DELETE FROM dataset_refresh_checks WHERE provider_id = ?",
            ("swaptr_wc2026_players",),
        )
        con.commit()
    return ensure_current_world_cup_data(
        repo,
        fetch_kaggle_world_cup_dataset,
        importer=lambda download: import_world_cup_download(repo, download, now),
        now=now,
        providers=("swaptr_wc2026_players",),
        max_age=timedelta(seconds=0),
    )


def _player_context(repo: Repository, match) -> tuple[list[dict], list[str]]:
    team_a, team_b = match.team_a.name, match.team_b.name
    selected = [
        row for row in repo.list_current_world_cup_players()
        if any(same_team(str(row.get("team_name") or ""), team) for team in (team_a, team_b))
    ]
    by_player_team = {
        (
            str(row.get("player_name") or ""),
            canonical_team_name(str(row.get("team_name") or "")),
        ): row
        for row in selected
    }
    for row in repo.list_deep_goalkeeper_player_profiles((team_a, team_b)):
        key = (
            str(row.get("player_name") or ""),
            canonical_team_name(str(row.get("team_name") or "")),
        )
        existing = by_player_team.get(key)
        if existing is None:
            enriched = {**row, "provider_id": "reviewed_deep_goalkeeper_stats"}
            selected.append(enriched)
            by_player_team[key] = enriched
            continue
        for metric in ("save_percentage", "saves", "goals_conceded"):
            if row.get(metric) is not None:
                if metric == "save_percentage" and existing.get("bank_save_percentage") is None:
                    existing["bank_save_percentage"] = existing.get("save_percentage")
                existing[metric] = row.get(metric)
                existing["goalkeeper_stats_source"] = "deep"
    context = []
    for row in selected:
        games = max(1, int(row.get("games") or 0))
        starts = max(0, int(row.get("starts") or 0))
        minutes = int(row.get("minutes") or 0)
        context.append(
            {
                **row,
                "expected_minutes": min(90, round(minutes / games)) if minutes else None,
                "starter_probability": min(1.0, starts / games),
                "availability": "available",
            }
        )
    events = repo.list_active_squad_context_events((team_a, team_b), match.kickoff_utc, match.id)
    return apply_squad_context(context, events, match.kickoff_utc, match.id)


def _prediction_index(predictions: list[MarketPrediction]) -> dict[tuple[str, str], MarketPrediction]:
    return {(prediction.market_name, prediction.selection_name): prediction for prediction in predictions}


KNOCKOUT_STAGES = (
    "Round of 32", "Round of 16", "Quarter-final", "Semi-final",
    "Third-place play-off", "Final",
)


def _is_knockout_stage(stage: str | None) -> bool:
    if not stage:
        return False
    return any(stage.startswith(s) for s in KNOCKOUT_STAGES)


def _penalty_attempts_for_match(repo: Repository, team_a: str, team_b: str) -> list[dict]:
    return repo.list_penalty_attempts(team_a) + repo.list_penalty_attempts(team_b)


def _knockout_prediction_for_match(match, bundle, repo: Repository | None = None):
    if not _is_knockout_stage(getattr(match, "stage", None)):
        return None
    expected_xg = bundle.expected_xg
    if not expected_xg or len(expected_xg) != 2:
        return None
    xa, xb = float(expected_xg[0]), float(expected_xg[1])
    if xa <= 0 or xb <= 0:
        return None
    penalty_context = None
    if repo is not None:
        penalty_context = build_penalty_match_context(
            match.team_a.name,
            match.team_b.name,
            _penalty_attempts_for_match(repo, match.team_a.name, match.team_b.name),
        )
    return predict_knockout_match(
        xa, xb,
        dispersion=0.08,    # matches DEFAULT_NB_DISPERSION in services.py
        rho=-0.16,          # matches DEFAULT_DIXON_COLES_RHO
        home_penalty_win_probability=(
            penalty_context.team_a_shootout_win_probability if penalty_context else None
        ),
    )


def _find_next_knockout_fixture(repo: Repository, match_id: int) -> str | None:
    """Return a human-readable description of the next knockout slot that
    depends on the winner of ``match_id`` (e.g. "Octavos · 4 jul · ganador
    vs ganador M77"). Returns None if no downstream slot references it.
    """
    from wcpredict.knockout_bracket import list_bracket_slots
    try:
        slots = list_bracket_slots(repo)
    except Exception:
        return None
    # The slot for the *current* match has match_id == match_id; downstream
    # slots reference it via 'W:M73', 'W:M74', etc.
    current_slot = next((s for s in slots if s.match_id == match_id), None)
    if current_slot is None:
        return None
    needle = f"W:{current_slot.slot_id}"
    downstream = next(
        (s for s in slots
         if s.home_source == needle or s.away_source == needle),
        None,
    )
    if downstream is None:
        return None
    other_src = downstream.away_source if downstream.home_source == needle else downstream.home_source
    rival_label = other_src
    if other_src.startswith("W:") and downstream.match_id is None:
        rival_label = f"ganador de {other_src.split(':', 1)[1]}"
    elif other_src.startswith("W:"):
        rival_label = f"ganador de {other_src.split(':', 1)[1]}"
    date_label = downstream.kickoff_utc[:10] if downstream.kickoff_utc else "fecha por confirmar"
    return f"{downstream.stage} · {date_label} · enfrenta a {rival_label}"


def _render_knockout_panel(
    match,
    bundle,
    team_a: str,
    team_b: str,
    repo: Repository,
    predictions,
    primary,
    expected_xg,
) -> bool:
    """Knockout-stage prediction panel with four stacked blocks.

    Returns True when the match is a knockout fixture and the panel was
    rendered; False otherwise so the caller falls back to the standard
    group-stage 1X2 layout.

    Bloques:
      1. PRINCIPAL  — quién avanza + vía (90' / ET / penaltis) + próximo cruce
      2. RESULTADO AL 90' — 1X2 estándar + marcador modo + xG
      3. PRÓRROGA Y PENALTIS — xG adicional + tabla de vías + datos de portería
      4. (los mercados de córners/tarjetas/etc se mantienen vía el flujo
         normal después de este panel)
    """
    pred = _knockout_prediction_for_match(match, bundle, repo)
    if pred is None:
        return False

    # ────────── 1. PRINCIPAL ──────────
    st.subheader("Quién avanza al siguiente cruce")
    section_note(
        "Funnel del partido: cada barra muestra la probabilidad CONDICIONAL "
        "dentro de su fase. Si llega la prórroga, esas son las probabilidades "
        "una vez ya estás en prórroga; lo mismo para los penaltis."
    )
    advance_html = (
        probability_bar(team_with_crest_html(team_a, size=18), pred.home_advances, "win")
        + probability_bar(team_with_crest_html(team_b, size=18), pred.away_advances, "loss")
    )
    st.markdown(advance_html, unsafe_allow_html=True)
    next_fixture = _find_next_knockout_fixture(repo, match.id)
    if next_fixture:
        st.caption(f"Cruce siguiente para el ganador: {next_fixture}.")

    # === Funnel de 3 barras condicionales ===
    st.markdown("**Al 90'**")
    funnel_90 = (
        probability_bar(team_with_crest_html(team_a, size=16), pred.home_wins_90, "win")
        + probability_bar("Empate (va a prórroga)", pred.p_draw_90, "draw")
        + probability_bar(team_with_crest_html(team_b, size=16), pred.away_wins_90, "loss")
    )
    st.markdown(funnel_90, unsafe_allow_html=True)

    st.markdown(f"**Si llega la prórroga** ({pred.p_draw_90:.1%} a-priori)")
    if pred.p_draw_90 > 0:
        funnel_et = (
            probability_bar(team_with_crest_html(team_a, size=16),
                            pred.cond_home_wins_et_given_draw_90, "win")
            + probability_bar("Empate (va a penaltis)",
                              pred.cond_draw_after_et_given_draw_90, "draw")
            + probability_bar(team_with_crest_html(team_b, size=16),
                              pred.cond_away_wins_et_given_draw_90, "loss")
        )
        st.markdown(funnel_et, unsafe_allow_html=True)
    else:
        st.caption("Sin probabilidad de prórroga relevante.")

    st.markdown(f"**Si se resuelve en penaltis** ({pred.p_draw_after_et:.1%} a-priori)")
    if pred.p_draw_after_et > 0:
        funnel_pen = (
            probability_bar(team_with_crest_html(team_a, size=16),
                            pred.cond_home_wins_penalties_given_draw_after_et, "win")
            + probability_bar(team_with_crest_html(team_b, size=16),
                              pred.cond_away_wins_penalties_given_draw_after_et, "loss")
        )
        st.markdown(funnel_pen, unsafe_allow_html=True)
    else:
        st.caption("Sin probabilidad de tanda de penaltis relevante.")

    # ────────── 2. RESULTADO AL 90' ──────────
    st.subheader("Resultado al 90'")
    p_home_90 = next(
        (row.probability for row in primary if row.selection_name == team_a), pred.home_wins_90
    )
    p_draw_90 = next(
        (row.probability for row in primary if row.selection_name == "Draw"), pred.p_draw_90
    )
    p_away_90 = next(
        (row.probability for row in primary if row.selection_name == team_b), pred.away_wins_90
    )
    bars_html = (
        probability_bar(team_with_crest_html(team_a, size=16), p_home_90, "win")
        + probability_bar("Empate", p_draw_90, "draw")
        + probability_bar(team_with_crest_html(team_b, size=16), p_away_90, "loss")
    )
    st.markdown(bars_html, unsafe_allow_html=True)
    if expected_xg and len(expected_xg) == 2:
        xa, xb = float(expected_xg[0]), float(expected_xg[1])
        c4, c5 = st.columns(2)
        c4.metric(f"xG {team_a} (90')", f"{xa:.2f}")
        c5.metric(f"xG {team_b} (90')", f"{xb:.2f}")
    exact_row = next((row for row in predictions if row.market_name == "Exact Score"), None)
    if exact_row is not None:
        st.caption(
            f"Marcador modo al 90': **{exact_row.selection_name}** ({exact_row.probability:.1%}). "
            "El empate al 90' implica prórroga; no es resultado final."
        )

    # ────────── 3. PRÓRROGA Y PENALTIS ──────────
    st.subheader("Prórroga y penaltis")
    if expected_xg and len(expected_xg) == 2:
        xa, xb = float(expected_xg[0]), float(expected_xg[1])
        et_a, et_b = xa * 0.30, xb * 0.30
        c6, c7 = st.columns(2)
        c6.metric(f"xG {team_a} en prórroga", f"{et_a:.2f}", help="xG_90 × 0.30 (30 min de tiempo extra)")
        c7.metric(f"xG {team_b} en prórroga", f"{et_b:.2f}", help="xG_90 × 0.30 (30 min de tiempo extra)")
    method_rows = [
        {"Vía": f"{team_a} en 90'", "Probabilidad (%)": pred.home_wins_90 * 100},
        {"Vía": f"{team_b} en 90'", "Probabilidad (%)": pred.away_wins_90 * 100},
        {"Vía": f"{team_a} en prórroga", "Probabilidad (%)": pred.home_wins_et * 100},
        {"Vía": f"{team_b} en prórroga", "Probabilidad (%)": pred.away_wins_et * 100},
        {"Vía": f"{team_a} en penaltis", "Probabilidad (%)": pred.home_wins_penalties * 100},
        {"Vía": f"{team_b} en penaltis", "Probabilidad (%)": pred.away_wins_penalties * 100},
    ]
    st.dataframe(
        pd.DataFrame(method_rows), width="stretch", hide_index=True,
        column_config={
            "Probabilidad (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        },
    )
    st.caption(
        f"Empate al 90': **{pred.p_draw_90:.1%}** · Empate tras prórroga: **{pred.p_draw_after_et:.1%}**."
    )
    # Penalty narrative: convertimos los datos del module dedicado en un
    # callout para el usuario.
    try:
        penalty_context = build_penalty_match_context(
            team_a, team_b, _penalty_attempts_for_match(repo, team_a, team_b),
        )
        if penalty_context and getattr(penalty_context, "explanation", ""):
            callout(penalty_context.explanation, tone="blue")
    except Exception:
        pass
    return True


def _render_knockout_advance_section(match, bundle, team_a: str, team_b: str, repo: Repository) -> None:
    """Legacy shim kept for callers that haven't migrated to the full panel
    yet. The new ``_render_knockout_panel`` covers the same ground and more.
    """
    _render_knockout_panel(
        match, bundle, team_a, team_b, repo,
        predictions=bundle.predictions,
        primary=bundle.primary,
        expected_xg=bundle.expected_xg,
    )


def _build_saved_odds_index(saved_odds: list[dict]) -> dict[tuple[str, str, float | None], float]:
    """Latest decimal_odds per (market_name, selection_name, line).

    Multiple entries with different captured_at_utc may exist; we keep the
    most recent one for each market/selection/line triple.
    """
    index: dict[tuple[str, str, float | None], tuple[str, float]] = {}
    for row in saved_odds:
        market = str(row.get("market_name") or "")
        selection = str(row.get("selection_name") or "")
        line_raw = row.get("line")
        line = float(line_raw) if line_raw is not None else None
        try:
            decimal = float(row.get("decimal_odds") or 0.0)
        except (TypeError, ValueError):
            continue
        if decimal <= 1.0:
            continue
        captured = str(row.get("captured_at_utc") or "")
        key = (market, selection, line)
        existing = index.get(key)
        if existing is None or captured > existing[0]:
            index[key] = (captured, decimal)
    return {key: value[1] for key, value in index.items()}


def _edge_pill(edge: float) -> str:
    """Render the BET / SKIP / FADE pill for a given edge (e.g. 0.07 → BET)."""
    if edge >= 0.05:
        return "<span class='pill pill-green'>BET</span>"
    if edge <= -0.05:
        return "<span class='pill pill-red'>FADE</span>"
    return "<span class='pill pill-neutral'>SKIP</span>"


def _edge_class(edge: float) -> str:
    if edge >= 0.05:
        return "edge-pos"
    if edge <= -0.05:
        return "edge-neg"
    return "edge-neu"


def _score_grid_html(
    team_a: str,
    team_b: str,
    predictions: list[MarketPrediction],
    max_goals: int = 5,
) -> str:
    rows = [
        row for row in predictions
        if row.market_name == "Exact Score Grid"
    ]
    if not rows:
        return ""
    values: dict[tuple[int, int], float] = {}
    for row in rows:
        try:
            a_text, b_text = row.selection_name.split("-", 1)
            a_goals, b_goals = int(a_text), int(b_text)
        except ValueError:
            continue
        if a_goals <= max_goals and b_goals <= max_goals:
            values[(a_goals, b_goals)] = row.probability
    if not values:
        return ""
    max_probability = max(values.values()) or 1.0
    header = (
        "<div class='score-axis'></div>"
        + "".join(f"<div class='score-axis'>{b}</div>" for b in range(max_goals + 1))
    )
    cells = [header]
    for a_goals in range(max_goals + 1):
        cells.append(f"<div class='score-axis'>{a_goals}</div>")
        for b_goals in range(max_goals + 1):
            probability = values.get((a_goals, b_goals), 0.0)
            intensity = min(1.0, probability / max_probability)
            alpha = 0.10 + 0.82 * intensity
            label = f"{probability * 100:.0f}" if probability >= 0.005 else "·"
            title = (
                f"{escape(team_a)} {a_goals}-{b_goals} {escape(team_b)} · "
                f"{probability:.2%}"
            )
            cells.append(
                "<div class='score-cell' "
                f"style='--heat:{alpha:.3f}' title='{title}'>{label}</div>"
            )
    columns = "28px " + " ".join("minmax(28px, 1fr)" for _ in range(max_goals + 1))
    return (
        "<div class='score-grid-wrap'>"
        "<div class='score-grid-head'>"
        "<span>Marcadores posibles</span>"
        f"<small>{escape(team_a)} goles ↓ · {escape(team_b)} goles →</small>"
        "</div>"
        f"<div class='score-grid' style='grid-template-columns:{columns}'>"
        + "".join(cells)
        + "</div></div>"
    )


def _render_exact_score_panel(
    team_a: str,
    team_b: str,
    predictions: list[MarketPrediction],
) -> None:
    """Standalone "Marcadores" view: top-3 cards + full probability grid."""
    score_cards: list[tuple[str, str, float]] = []
    main_exact = next((row for row in predictions if row.market_name == "Exact Score"), None)
    if main_exact is not None:
        score_cards.append(("MÁS PROBABLE", main_exact.selection_name, main_exact.probability))
    alt_scores = [row for row in predictions if row.market_name == "Exact Score (alt)"]
    for idx, row in enumerate(alt_scores[:2], start=2):
        label = f"#{idx}"
        score_text = row.selection_name.split(" ")[0]
        score_cards.append((label, score_text, row.probability))

    if score_cards:
        cards_html = "".join(
            f'<div class="score-card{" rank-1" if i == 0 else ""}">'
            f'<span class="rank-tag">{label}</span>'
            f'<span class="score-value">{score.replace("-", " - ")}</span>'
            f'<span class="score-prob">{prob:.1%}</span>'
            "</div>"
            for i, (label, score, prob) in enumerate(score_cards)
        )
        st.markdown(
            '<div class="eyebrow">Marcadores exactos más probables</div>'
            f'<div class="score-cards">{cards_html}</div>',
            unsafe_allow_html=True,
        )
        st.caption("Probabilidades Dixon-Coles. Úsalo como contexto, no como apuesta directa.")

    grid_html = _score_grid_html(team_a, team_b, predictions)
    if grid_html:
        st.markdown(grid_html, unsafe_allow_html=True)
    else:
        st.info("Sin probabilidades de marcador exacto para este partido.")


def _render_market_visual_panel(
    team_a: str,
    team_b: str,
    predictions: list[MarketPrediction],
    volume_predictions: dict[str, float],
    saved_odds: list[dict] | None = None,
    *,
    match=None,
    ko_prediction=None,
) -> None:
    """Visual top-of-tab summary for "Mercados y EV".

    * Main markets (1X2, O/U 2.5, BTTS) with model %, fair odds and —
      when the user has saved odds — Tu cuota / Edge / Pick (BET/SKIP/FADE).
    * Heuristic secondary markets (corners, cards, shots) with a
      suggested line and a LEAN over/under hint, plus edge when there
      are saved odds for that line.

    Exact-score cards/grid moved to the dedicated "Marcadores" section.
    """
    odds_index = _build_saved_odds_index(saved_odds or [])
    has_odds = bool(odds_index)

    def _fair_odds(p: float) -> str:
        return f"{1.0 / p:.2f}" if p and p > 0.01 else "—"

    # (market_label, market_name_canonical, selection_label, selection_canonical, line, probability)
    main_rows: list[tuple[str, str, str, str, float | None, float]] = []
    for row in predictions:
        if row.market_name == "1X2":
            main_rows.append(("1X2", "1X2", localize_selection(row.selection_name),
                              row.selection_name, None, row.probability))
        elif row.market_name == "Over/Under 2.5":
            main_rows.append(("Total 2.5 goles", "Over/Under 2.5",
                              localize_selection(row.selection_name),
                              row.selection_name, 2.5, row.probability))
        elif row.market_name == "Both Teams To Score":
            sel = "Sí" if row.selection_name == "Yes" else "No"
            main_rows.append(("Ambos marcan", "Both Teams To Score", sel,
                              row.selection_name, None, row.probability))

    if main_rows:
        rows_html = []
        for mkt, mkt_canon, sel, sel_canon, line, prob in main_rows:
            cells = (
                f"<td class='market-name'>{mkt}<div class='market-sub'>{sel}</div></td>"
                f"<td class='num'>{prob:.1%}</td>"
                f"<td class='num'>{_fair_odds(prob)}</td>"
            )
            if has_odds:
                user_odds = odds_index.get((mkt_canon, sel_canon, line))
                if user_odds:
                    edge = prob * user_odds - 1.0
                    cells += (
                        f"<td class='num'>{user_odds:.2f}</td>"
                        f"<td class='num {_edge_class(edge)}'>{edge * 100:+.1f}%</td>"
                        f"<td class='center'>{_edge_pill(edge)}</td>"
                    )
                else:
                    cells += "<td class='num'>—</td><td class='num'>—</td><td class='center'>—</td>"
            rows_html.append(f"<tr>{cells}</tr>")
        header = (
            "<th>Mercado</th><th class='num'>Modelo</th><th class='num'>Cuota justa</th>"
            + ("<th class='num'>Tu cuota</th><th class='num'>Edge</th><th class='center'>Pick</th>" if has_odds else "")
        )
        st.markdown(
            '<div class="eyebrow">Mercados principales · Modelo</div>'
            "<table class='mk-table'>"
            f"<thead><tr>{header}</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table>",
            unsafe_allow_html=True,
        )

    # Knockout fixtures keep all the volume markets, but with a slightly
    # higher cards line (KO matches tend to be tighter and more physical),
    # plus a "llega a penaltis" probability that only makes sense in KOs.
    is_knockout = _is_knockout_stage(getattr(match, "stage", None))
    cards_line = 5.5 if is_knockout else 4.5
    secondary = [
        ("Córners totales", "corners", 9.5),
        (f"Tarjetas totales (KO {cards_line})" if is_knockout else "Tarjetas totales", "cards", cards_line),
        ("Tiros totales", "shots", 22.5),
        ("Tiros a puerta totales", "shots_on_target", 8.5),
    ]
    sec_rows: list[str] = []
    for label, key, line in secondary:
        est = volume_predictions.get(key)
        if est is None:
            continue
        gap = est - line
        if gap >= 0.5:
            lean_html = f"<span class='pill pill-green'>OVER {line}</span>"
        elif gap <= -0.5:
            lean_html = f"<span class='pill pill-amber'>UNDER {line}</span>"
        else:
            lean_html = "<span class='pill pill-neutral'>PUSH</span>"
        sec_rows.append(
            f"<tr><td class='market-name'>{label}</td>"
            f"<td class='num'>{est:.1f}</td>"
            f"<td class='num'>{line}</td>"
            f"<td class='center'>{lean_html}</td></tr>"
        )
    if is_knockout and ko_prediction is not None:
        # Add the "llega a penaltis" probability row. Pre-kickoff this
        # uses the predicted draw-after-ET probability from the KO model.
        ko_pred = ko_prediction
        if ko_pred is not None:
            pen_prob = ko_pred.p_draw_after_et
            if pen_prob >= 0.18:
                lean_pen = "<span class='pill pill-amber'>POSIBLE</span>"
            elif pen_prob <= 0.08:
                lean_pen = "<span class='pill pill-neutral'>BAJO</span>"
            else:
                lean_pen = "<span class='pill pill-neutral'>SKIP</span>"
            sec_rows.append(
                f"<tr><td class='market-name'>Llega a penaltis</td>"
                f"<td class='num'>{pen_prob*100:.1f}%</td>"
                f"<td class='num'>—</td>"
                f"<td class='center'>{lean_pen}</td></tr>"
            )
    if sec_rows:
        st.markdown(
            '<div class="eyebrow">Mercados secundarios · Heurístico</div>'
            "<table class='mk-table'>"
            "<thead><tr><th>Mercado</th><th class='num'>Estimación</th><th class='num'>Línea</th><th class='center'>Lean</th></tr></thead>"
            f"<tbody>{''.join(sec_rows)}</tbody></table>",
            unsafe_allow_html=True,
        )


def _database_summary() -> dict[str, int | bool]:
    """Counters shown in the Resumen hero. Filtered to the 2026 World Cup so
    the historical backfill (~4k matches, ~150 teams from past tournaments)
    doesn't drown out the figures we actually care about."""
    repo = _repo()
    wc_filter = "competition = 'FIFA World Cup 2026'"
    with sqlite3.connect(repo.path) as con:
        return {
            "exists": repo.path.exists(),
            "matches": con.execute(
                f"SELECT COUNT(*) FROM matches WHERE {wc_filter}"
            ).fetchone()[0],
            # Teams: only those appearing in WC2026 matches.
            "teams": con.execute(
                "SELECT COUNT(DISTINCT t.id) FROM teams t "
                "JOIN matches m ON t.id IN (m.team_a_id, m.team_b_id) "
                f"WHERE m.{wc_filter}"
            ).fetchone()[0],
            "odds": con.execute(
                "SELECT COUNT(*) FROM manual_odds o "
                "JOIN matches m ON m.id = o.match_id "
                f"WHERE m.{wc_filter}"
            ).fetchone()[0],
            "predictions": con.execute(
                "SELECT COUNT(*) FROM predictions p "
                "JOIN matches m ON m.id = p.match_id "
                f"WHERE m.{wc_filter}"
            ).fetchone()[0],
            "imports": con.execute(
                "SELECT COUNT(*) FROM import_runs i "
                "JOIN matches m ON m.id = i.match_id "
                f"WHERE m.{wc_filter}"
            ).fetchone()[0],
        }


_KNOCKOUT_STAGE_TOKENS = (
    "round of 32", "round of 16", "octavos", "dieciseisavos",
    "quarter-final", "quarter final", "cuartos",
    "semi-final", "semi final", "semifinal",
    "third-place", "third place", "tercer puesto", "3rd place",
    "final",
)


def _is_knockout_stage(stage: str | None) -> bool:
    if not stage:
        return False
    s = str(stage).lower()
    if s.startswith("group stage"):
        return False
    return any(token in s for token in _KNOCKOUT_STAGE_TOKENS)


def _match_labels(matches) -> tuple[list[str], dict[str, object]]:
    """Build the (labels, lookup) pair for st.selectbox.

    Adds a visual separator between group-stage matches and knockout-stage
    matches so the dropdown reads naturally: groups first, then a "─── Fase
    eliminatoria ───" divider row that cannot be selected (lookup returns
    None on it, the UI handles that gracefully).
    """
    group_matches = [m for m in matches if not _is_knockout_stage(getattr(m, "stage", None))]
    knockout_matches = [m for m in matches if _is_knockout_stage(getattr(m, "stage", None))]

    def _label(match):
        return f"{_display_time(match.kickoff_utc, '%d %b · %H:%M')} — {match.label}"

    labels: list[str] = []
    lookup: dict[str, object] = {}

    if group_matches:
        labels.append("─── Fase de grupos ───")
        for m in group_matches:
            l = _label(m)
            labels.append(l)
            lookup[l] = m
    if knockout_matches:
        labels.append("─── Fase eliminatoria ───")
        for m in knockout_matches:
            l = _label(m)
            labels.append(l)
            lookup[l] = m
    return labels, lookup


def _cached_bundle(match) -> CollectorEventBundle | None:
    return _collector_bundle_cached(
        match.team_a.name,
        match.team_b.name,
        match.kickoff_utc.date().isoformat(),
        _sports_db_signature(),
    )


def _coverage_status(bundle: CollectorEventBundle | None) -> tuple[str, str]:
    if bundle is None:
        return "Sin datos", "red"
    if bundle.missing_critical:
        return "Cobertura parcial", "amber"
    return "Datos listos", "green"


def _probability_chart(predictions: list[MarketPrediction]) -> alt.Chart:
    rows = probability_chart_rows(predictions, "1X2")
    frame = pd.DataFrame(rows)
    base = alt.Chart(frame).encode(
        y=alt.Y("Seleccion:N", sort=None, title=None),
        tooltip=["Seleccion", alt.Tooltip("Probabilidad:Q", format=".1%"), "Confianza"],
    )
    bars = base.mark_bar(cornerRadiusEnd=5, height=27, color="#1769E0").encode(
        x=alt.X("Probabilidad:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", title=None))
    )
    labels = base.mark_text(align="left", dx=7, color="#10233F", fontWeight=700).encode(
        x="Probabilidad:Q", text="Etiqueta:N"
    )
    return (bars + labels).properties(height=150)


def _render_bundle(bundle: CollectorEventBundle, deep_count: int = 0, daily_players: int = 0) -> None:
    label, tone = _coverage_status(bundle)
    st.markdown(
        '<div class="status-row">'
        + status_pill(label, tone)
        + status_pill(f"Actualizado {_display_time(bundle.updated_at_utc, '%d/%m %H:%M')}")
        + status_pill(f"Fuente event #{bundle.event_id}")
        + "</div>",
        unsafe_allow_html=True,
    )
    summary = coverage_summary(
        collector_statistics=len(bundle.statistics),
        imported_lineups=len(bundle.lineups),
        daily_players=daily_players,
        sources=len(bundle.sources),
        deep_statistics=deep_count,
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Estadísticas disponibles", summary["Estadísticas disponibles"])
    c2.metric("Jugadores disponibles", summary["Jugadores disponibles"])
    c3.metric("Fuentes", summary["Fuentes"])
    c4.metric("Profundidad", summary["Estadísticas profundas"])
    c5.metric("Alineación", summary["Alineación"])
    if bundle.missing_critical or bundle.missing_optional:
        missing_labels = {
            "team_statistics": "estadísticas de equipo",
            "players": "alineación confirmada",
            "availability": "disponibilidad",
            "lineups": "alineaciones",
            "event": "evento",
        }
        callout(
            "Faltan: "
            + ", ".join(missing_labels.get(value, value) for value in bundle.missing_critical + bundle.missing_optional)
            + ". La app no sustituye esos campos con valores inventados.",
            tone="amber",
        )


@dataclass
class MatchAnalysisBundle:
    predictions: list
    score_only_predictions: list
    primary: list
    exact_score: object
    ml_probabilities: dict | None
    ml_features: dict | None
    ml_model_meta: dict | None
    current_players: list
    squad_notes: list
    results: list
    deep_count: int
    prior_deep_samples: int
    observations: list
    cached_collector_bundle: object
    deep_rows_before: list = field(default_factory=list)
    match_result: dict | None = None
    team_match_stats: list = field(default_factory=list)
    backtests: list = field(default_factory=list)
    volume_predictions: dict = field(default_factory=dict)
    team_volume_predictions: dict = field(default_factory=dict)
    volume_market_rows: list[dict] = field(default_factory=list)
    team_volume_stat_rows: list[dict] = field(default_factory=list)
    deep_ml_probabilities: dict | None = None
    deep_outcome_weight: float = 0.0
    expected_xg: tuple = field(default_factory=tuple)
    goalkeeper_baselines: dict = field(default_factory=dict)
    corrections: object = None


@dataclass
class MatchVolumeBundle:
    volume_predictions: dict = field(default_factory=dict)
    team_volume_predictions: dict = field(default_factory=dict)
    volume_market_rows: list[dict] = field(default_factory=list)
    team_volume_stat_rows: list[dict] = field(default_factory=list)


@dataclass
class TeamVolumeContext:
    team_volume_predictions: dict = field(default_factory=dict)
    team_volume_stat_rows: list[dict] = field(default_factory=list)


@dataclass
class MatchAuxiliaryBundle:
    match_result: dict | None = None
    team_match_stats: list = field(default_factory=list)
    backtests: list = field(default_factory=list)
    volume_predictions: dict = field(default_factory=dict)
    team_volume_predictions: dict = field(default_factory=dict)
    volume_market_rows: list[dict] = field(default_factory=list)
    team_volume_stat_rows: list[dict] = field(default_factory=list)
    goalkeeper_baselines: dict = field(default_factory=dict)


@st.cache_resource(show_spinner=False)
def _match_analysis_bundle_cached(
    match_id: int,
    db_sig: tuple[int, int],
    sports_db_sig: tuple[int, int],
    model_sig: tuple[int, int] | None,
    engine_version: str,
    apply_corrections: bool = False,
) -> MatchAnalysisBundle:
    repo = _repo()
    match = next(item for item in _matches_cached(db_sig) if item.id == match_id)
    team_a, team_b = match.team_a.name, match.team_b.name

    current_players, squad_notes = _player_context(repo, match)
    deep_rows_before = repo.list_deep_xg_rows_before(match.kickoff_utc)
    observations_for_match = repo.list_observations(match.id)
    prior_deep_samples = sum(
        1
        for row in deep_rows_before
        if any(
            same_team(str(row.get(team_key) or ""), team_name)
            for team_key in ("team_a", "team_b")
            for team_name in (team_a, team_b)
        )
    )
    deep_count = sum(
        row.get("evidence_status") == "verified_user_json"
        for row in observations_for_match
    )
    cached_collector_bundle = _store_cached().find_event(
        team_a, team_b, match.kickoff_utc.date()
    )

    collector_results = _store_cached().list_finished_results(match.kickoff_utc)
    historical_rows = repo.list_historical_rows_before(match.kickoff_utc)
    historical_results = _historical_rows_to_results(historical_rows)
    local_results = repo.list_match_results_before(match.kickoff_utc)
    keyed_results = {
        (row.played_on, row.team_a, row.team_b): row
        for row in historical_results + collector_results + local_results
    }
    results = list(keyed_results.values())

    calibration_summary = _calibration_summary_cached(db_sig)
    ratings_for_match = build_team_ratings(results, as_of=match.kickoff_utc.date())
    strength_context = {
        name: {"attack": rating.attack, "defense": rating.defense}
        for name, rating in ratings_for_match.items()
    }
    xg_form = build_xg_form_adjustment(
        team_a, team_b, deep_rows_before, match.kickoff_utc,
        team_strengths=strength_context,
    )

    # Layer on top: a richer factor derived from the full deep-stat profile
    # (offense / defense / goalkeeper dimensions). The simple xg_form above
    # only uses ~9 metrics; this brings in the remaining 60+ but keeps the
    # multiplier bounded so it complements rather than replaces the base.
    from wcpredict.team_profile import build_team_profiles
    from wcpredict.team_volume_markets import derive_xg_factors_from_profile
    from wcpredict.advanced_form import XgFormAdjustment
    deep_obs_for_profile = repo.list_deep_team_metric_observations_before(match.kickoff_utc)
    # Phase: mark MD3 dead-rubber rows so their weight is cut to 30% in
    # team_profile. Detects the case where a team was already mathematically
    # classified (or eliminated) before its MD3 fixture and likely fielded
    # a rotated squad (Spain 0-0 with subs after sealing 1st place, etc).
    low_intensity_pairs = _low_intensity_pairs_cached(db_sig)
    if low_intensity_pairs:
        from wcpredict.low_intensity import mark_low_intensity_rows
        deep_obs_for_profile = mark_low_intensity_rows(deep_obs_for_profile, low_intensity_pairs)
    # Opponent strength = (attack + defense) average per team, derived from the
    # Elo-style ratings. Used so metrics produced against strong sides count
    # for more than the same numbers against weak ones.
    opponent_strengths = {
        name: (rating.attack + rating.defense) / 2
        for name, rating in ratings_for_match.items()
    }
    team_profiles = build_team_profiles(
        (team_a, team_b),
        deep_obs_for_profile,
        match.kickoff_utc,
        opponent_strengths=opponent_strengths,
    )
    profile_a_xg = team_profiles[team_a]
    profile_b_xg = team_profiles[team_b]
    if profile_a_xg.sample_weight > 0 or profile_b_xg.sample_weight > 0:
        pf_a, pf_b, pf_note = derive_xg_factors_from_profile(profile_a_xg, profile_b_xg)
        xg_form = XgFormAdjustment(
            factor_a=xg_form.factor_a * pf_a,
            factor_b=xg_form.factor_b * pf_b,
            sample_a=xg_form.sample_a + int(profile_a_xg.sample_weight),
            sample_b=xg_form.sample_b + int(profile_b_xg.sample_weight),
            explanation=xg_form.explanation + " " + pf_note,
        )

    # Adaptive 1X2 blend weight: the higher our per-team deep-stat sample,
    # the more we trust the score-matrix branch over the ML branch. Below 5
    # effective matches per team the matrix is noisy so ML stays dominant
    # (default 0.80). Above ~15 matches both sides have enough signal that
    # we cut ML's share to 0.65.
    min_profile_weight = min(profile_a_xg.sample_weight, profile_b_xg.sample_weight)
    if min_profile_weight >= 15:
        outcome_weight = 0.65
    elif min_profile_weight >= 5:
        outcome_weight = 0.75
    else:
        outcome_weight = 0.85
    host_factor_a = _host_factor(team_a)
    host_factor_b = _host_factor(team_b)
    local_result_rows = match_results_to_feature_rows(local_results)
    chronological_rows = historical_rows + local_result_rows

    ml_model = _load_outcome_model_cached(str(OUTCOME_MODEL_PATH), model_sig)
    ml_features = None
    ml_probabilities = None
    ml_model_meta = None
    if ml_model is not None and ml_model.status == "ready":
        ml_features = current_match_features(
            chronological_rows, team_a, team_b, match.neutral_site
        )
        ml_probabilities = ml_model.predict(ml_features)
        ml_model_meta = {
            "sample_size": ml_model.sample_size,
            "training_cutoff_utc": ml_model.training_cutoff_utc,
            "validation_cutoff_utc": ml_model.validation_cutoff_utc,
        }

    # Deep-stats classifier (HistGBM). Only contributes when both teams have
    # enough effective profile sample to make the features meaningful.
    deep_ml_probabilities = None
    deep_weight = 0.0
    if (
        ml_features is not None
        and profile_a_xg.sample_weight >= 3
        and profile_b_xg.sample_weight >= 3
    ):
        deep_model_sig = _file_signature(DEEP_OUTCOME_MODEL_PATH)
        deep_ml_model = _load_deep_outcome_model_cached(str(DEEP_OUTCOME_MODEL_PATH), deep_model_sig)
        from wcpredict.outcome_ml_deep import build_deep_features
        if deep_ml_model is not None and getattr(deep_ml_model, "status", "") == "ready":
            deep_features = build_deep_features(ml_features, profile_a_xg, profile_b_xg)
            try:
                deep_ml_probabilities = deep_ml_model.predict(deep_features)
                # Scale deep classifier influence by min profile sample. Empirical
                # backtest on the WC 2026 first matchday showed cap 0.50 hurt the
                # ensemble Brier; cap 0.25 keeps Brier neutral and adds +2.5pp on
                # accuracy (1 extra correct pick in 40 matches).
                mn = min(profile_a_xg.sample_weight, profile_b_xg.sample_weight)
                deep_weight = min(0.25, max(0.0, (mn - 3.0) / 48.0))
            except Exception:
                deep_ml_probabilities = None

    corrections = None
    if apply_corrections:
        try:
            _, _bias = _calibration_bias_report_cached(db_sig)
            corrections = derive_corrections(_bias)
            if not corrections_active(corrections):
                corrections = None
        except Exception:
            corrections = None
    # Phase 5b: load per-team 1X2 shifts from the historical residual pool
    # (built via scripts/build_historical_team_residuals.py). Validation
    # showed +3.3pp hit rate, Brier -0.0106 on the 60 closed WC2026 matches.
    team_shifts_cache = _load_team_shifts_cached(db_sig)
    draw_context = draw_incentive_for_match(
        match,
        _matches_cached(db_sig),
        local_results,
    )
    predictions = predict_match_markets(
        team_a, team_b, results, match.kickoff_utc.date(), calibration_summary,
        player_context=current_players or None,
        advanced_form=xg_form,
        outcome_probabilities=ml_probabilities,
        outcome_weight=outcome_weight,
        deep_outcome_probabilities=deep_ml_probabilities,
        deep_outcome_weight=deep_weight,
        host_factor_a=host_factor_a,
        host_factor_b=host_factor_b,
        corrections=corrections,
        precomputed_ratings=ratings_for_match,
        draw_incentive=draw_context.logit_boost,
        draw_incentive_note=draw_context.explanation,
        team_corrections=team_shifts_cache,
    )
    score_only_predictions = predict_match_markets(
        team_a, team_b, results, match.kickoff_utc.date(), calibration_summary,
        player_context=current_players or None,
        advanced_form=xg_form,
        host_factor_a=host_factor_a,
        host_factor_b=host_factor_b,
        corrections=corrections,
        precomputed_ratings=ratings_for_match,
        draw_incentive=draw_context.logit_boost,
        draw_incentive_note=draw_context.explanation,
        team_corrections=team_shifts_cache,
    )
    primary = [row for row in predictions if row.market_name == "1X2"]
    exact_score = next(row for row in predictions if row.market_name == "Exact Score")

    # Expected goals per team from the unified model (Expected Score row).
    expected_xg: tuple = ()
    expected_row = next(
        (row for row in predictions if row.market_name == "Expected Score"), None,
    )
    if expected_row is not None:
        try:
            ea, eb = (float(value) for value in expected_row.selection_name.split("-"))
            expected_xg = (ea, eb)
        except (ValueError, AttributeError):
            expected_xg = ()

    bundle = MatchAnalysisBundle(
        predictions=predictions,
        score_only_predictions=score_only_predictions,
        primary=primary,
        exact_score=exact_score,
        ml_probabilities=ml_probabilities,
        ml_features=ml_features,
        ml_model_meta=ml_model_meta,
        current_players=current_players,
        squad_notes=squad_notes,
        results=results,
        deep_count=deep_count,
        prior_deep_samples=prior_deep_samples,
        observations=observations_for_match,
        cached_collector_bundle=cached_collector_bundle,
        deep_rows_before=deep_rows_before,
        expected_xg=expected_xg,
        corrections=corrections,
        deep_ml_probabilities=deep_ml_probabilities,
        deep_outcome_weight=deep_weight,
    )
    # Persist a frozen pre-kickoff snapshot of this prediction. Only fires
    # for matches whose kickoff is still in the future so post-match cache
    # rebuilds don't pollute the historical record. Idempotent on
    # (match_id, model_version, data_as_of_utc).
    try:
        now_utc = datetime.now(timezone.utc)
        if match.kickoff_utc > now_utc:
            payload = _bundle_snapshot_payload(team_a, team_b, predictions, primary,
                                               expected_xg, deep_count, prior_deep_samples)
            repo.save_prediction_snapshot(
                match_id=match.id,
                payload=payload,
                data_as_of_utc=now_utc,
                model_version=engine_version,
                generated_at_utc=now_utc,
            )
    except Exception:
        # Snapshot persistence must never block prediction rendering.
        pass
    return bundle


def _bundle_snapshot_payload(
    team_a: str,
    team_b: str,
    predictions,
    primary,
    expected_xg,
    deep_count: int,
    prior_deep_samples: int,
) -> dict:
    """Compact serialisable snapshot of the predictions delivered to the UI."""
    def _row(row) -> dict:
        return {
            "market_family": str(getattr(row, "market_family", "")),
            "market_name": str(getattr(row, "market_name", "")),
            "selection_name": str(getattr(row, "selection_name", "")),
            "line": getattr(row, "line", None),
            "probability": float(getattr(row, "probability", 0.0)),
            "confidence": str(getattr(getattr(row, "confidence", None), "value", "")),
        }
    return {
        "team_a": team_a,
        "team_b": team_b,
        "expected_xg": list(expected_xg) if expected_xg else [],
        "deep_count": int(deep_count),
        "prior_deep_samples": int(prior_deep_samples),
        "primary": [_row(p) for p in primary],
        "predictions": [_row(p) for p in predictions],
    }


@st.cache_resource(show_spinner=False)
def _team_volume_context_from_profiles_cached(
    match_id: int,
    db_sig: tuple[int, int],
    engine_version: str,
) -> TeamVolumeContext:
    repo = _repo()
    match = next(item for item in _matches_cached(db_sig) if item.id == match_id)
    team_a, team_b = match.team_a.name, match.team_b.name
    historical_rows = repo.list_historical_rows_before(match.kickoff_utc)
    historical_results = _historical_rows_to_results(historical_rows)
    collector_results = _store_cached().list_finished_results(match.kickoff_utc)
    local_results = repo.list_match_results_before(match.kickoff_utc)
    keyed_results = {
        (row.played_on, row.team_a, row.team_b): row
        for row in historical_results + collector_results + local_results
    }
    ratings_for_match = build_team_ratings(
        list(keyed_results.values()), as_of=match.kickoff_utc.date()
    )
    opponent_strengths = {
        name: (rating.attack + rating.defense) / 2
        for name, rating in ratings_for_match.items()
    }
    from wcpredict.team_profile import build_team_profiles
    from wcpredict.team_volume_markets import MARKET_CATALOG, predict_team_volume_markets

    team_profiles = build_team_profiles(
        (team_a, team_b),
        repo.list_deep_team_metric_observations_before(match.kickoff_utc),
        match.kickoff_utc,
        opponent_strengths=opponent_strengths,
    )
    team_lines = predict_team_volume_markets(team_profiles[team_a], team_profiles[team_b])
    team_volume_stat_rows: list[dict] = []
    team_volume_predictions: dict[str, dict[str, float]] = {}
    if not team_lines:
        return TeamVolumeContext()
    expected_by_team_metric: dict[tuple[str, str], dict] = {}
    for row in team_lines:
        key = (row.team_name, row.market)
        if key in expected_by_team_metric:
            continue
        expected_by_team_metric[key] = {
            "expected": row.expected,
            "confidence": row.confidence,
            "sample": row.sample_size,
        }
        team_volume_predictions.setdefault(row.market, {})[row.team_name] = float(row.expected)
    metric_aliases = {"shots_total": "shots", "yellow_cards": "cards"}
    for source_metric, target_metric in metric_aliases.items():
        values = team_volume_predictions.get(source_metric)
        if values:
            team_volume_predictions[target_metric] = dict(values)
    for market_id in MARKET_CATALOG.keys():
        label = MARKET_CATALOG[market_id]["label"]
        a = expected_by_team_metric.get((team_a, market_id))
        b = expected_by_team_metric.get((team_b, market_id))
        if not a and not b:
            continue
        team_volume_stat_rows.append({
            "Estadística": label,
            team_a: round(a["expected"], 2) if a else None,
            team_b: round(b["expected"], 2) if b else None,
            "Confianza": (a or b)["confidence"],
            "Muestra": round((a or b)["sample"], 1),
        })
    return TeamVolumeContext(
        team_volume_predictions=team_volume_predictions,
        team_volume_stat_rows=team_volume_stat_rows,
    )


def _team_volume_context_from_profiles(match) -> TeamVolumeContext:
    return _team_volume_context_from_profiles_cached(
        match.id,
        _db_signature(),
        PREDICTION_ENGINE_VERSION,
    )


@st.cache_resource(show_spinner=False)
def _match_volume_context_cached(
    match_id: int,
    db_sig: tuple[int, int],
    engine_version: str,
) -> MatchVolumeBundle:
    repo = _repo()
    match = next(item for item in _matches_cached(db_sig) if item.id == match_id)
    team_a, team_b = match.team_a.name, match.team_b.name
    observations_for_match = repo.list_observations(match.id)

    volume_predictions: dict[str, float] = {}
    team_volume = _team_volume_context_from_profiles_cached(match_id, db_sig, engine_version)
    volume_market_rows: list[dict] = []
    rate_observations = observations_for_match + build_volume_rate_observations(
        team_a, team_b, repo.list_deep_volume_rows_before(match.kickoff_utc)
    )
    volume_lines = {"corners": 8.5, "cards": 3.5, "shots": 21.5, "shots_on_target": 8.5}
    for metric, line in volume_lines.items():
        dispersion_row = next(
            (row for row in rate_observations if row.get("metric") == f"{metric}_dispersion"),
            None,
        )
        dispersion = (
            float(dispersion_row["value_number"])
            if dispersion_row and dispersion_row.get("value_number") is not None
            else None
        )
        estimate = estimate_total_market(
            team_a, team_b, rate_observations, metric, line, dispersion=dispersion
        )
        volume_market_rows.append(
            {
                "Mercado": localize_metric(metric),
                "Línea": line,
                "Modelo": localize_model(estimate.model_family),
                "Esperado": estimate.expected_total,
                "Probabilidad de más": estimate.over_probability,
                "Rango bajo": estimate.low_probability,
                "Rango alto": estimate.high_probability,
                "Confianza": estimate.confidence,
                "Muestra": estimate.sample_size,
                "Explicación": estimate.explanation,
            }
        )
        if estimate.expected_total is not None:
            volume_predictions[metric] = float(estimate.expected_total)

    return MatchVolumeBundle(
        volume_predictions=volume_predictions,
        team_volume_predictions=team_volume.team_volume_predictions,
        volume_market_rows=volume_market_rows,
        team_volume_stat_rows=team_volume.team_volume_stat_rows,
    )


def _match_volume_context(match) -> MatchVolumeBundle:
    return _match_volume_context_cached(
        match.id,
        _db_signature(),
        PREDICTION_ENGINE_VERSION,
    )


@st.cache_resource(show_spinner=False)
def _match_auxiliary_context_cached(
    match_id: int,
    db_sig: tuple[int, int],
    engine_version: str,
) -> MatchAuxiliaryBundle:
    repo = _repo()
    match = next(item for item in _matches_cached(db_sig) if item.id == match_id)
    team_a, team_b = match.team_a.name, match.team_b.name

    match_result = repo.get_match_result(match.id)
    team_match_stats = repo.list_team_match_stats(match.id)
    backtests = repo.list_backtests(match.id)
    volume = _match_volume_context_cached(match_id, db_sig, engine_version)

    goalkeeper_rows = repo.list_deep_goalkeeper_rows_before(match.kickoff_utc)
    goalkeeper_baselines = {
        team_a: build_goalkeeper_baseline(team_a, goalkeeper_rows, match.kickoff_utc),
        team_b: build_goalkeeper_baseline(team_b, goalkeeper_rows, match.kickoff_utc),
    }
    return MatchAuxiliaryBundle(
        match_result=dict(match_result) if match_result else None,
        team_match_stats=team_match_stats,
        backtests=backtests,
        volume_predictions=volume.volume_predictions,
        team_volume_predictions=volume.team_volume_predictions,
        volume_market_rows=volume.volume_market_rows,
        team_volume_stat_rows=volume.team_volume_stat_rows,
        goalkeeper_baselines=goalkeeper_baselines,
    )


def _match_auxiliary_context(match) -> MatchAuxiliaryBundle:
    return _match_auxiliary_context_cached(
        match.id,
        _db_signature(),
        PREDICTION_ENGINE_VERSION,
    )


def _render_volume_markets(auxiliary: MatchVolumeBundle | MatchAuxiliaryBundle) -> None:
    st.subheader("Mercados de volumen")
    if auxiliary.volume_market_rows:
        st.dataframe(
            pd.DataFrame(auxiliary.volume_market_rows),
            width="stretch",
            hide_index=True,
            column_config={
                "Probabilidad de más": st.column_config.ProgressColumn(
                    format="%.1f%%", min_value=0, max_value=1
                ),
                "Rango bajo": st.column_config.NumberColumn(format="%.1f%%"),
                "Rango alto": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
    else:
        st.info("No hay muestra suficiente para estimar mercados de volumen.")

    team_volume_stat_rows = getattr(auxiliary, "team_volume_stat_rows", [])
    if team_volume_stat_rows:
        st.subheader("Estadísticas estimadas por equipo")
        st.caption(
            "Valor esperado por partido para cada métrica, derivado del perfil "
            "deep (45% propio + 30% rival + 25% media del torneo). Las líneas "
            "over/under aparecen en la pestaña Mercados y EV cuando hay cuotas."
        )
        st.dataframe(
            pd.DataFrame(team_volume_stat_rows),
            width="stretch",
            hide_index=True,
        )


def _render_audit_table(rows) -> None:
    if not rows:
        return
    # Render as styled HTML rows: no pandas.Styler dependency on jinja2, and
    # the colour is applied per row using the AuditRow severity directly.
    records = audit_rows_to_records(rows)
    html_parts = [
        '<div class="audit-table-wrap" style="overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;margin:4px 0 12px;">',
        '<table style="width:100%; border-collapse:separate; border-spacing:0 4px; font-size:0.92rem; min-width:560px;">',
        '<thead><tr>'
        '<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">Métrica</th>'
        '<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">Predicho</th>'
        '<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">Real</th>'
        '<th style="text-align:right;padding:6px 10px;color:#5b6b80;font-weight:600">Δ</th>'
        '</tr></thead><tbody>',
    ]
    for record in records:
        colour = SEVERITY_COLORS.get(record.get("_severity", "ok"), "#3a8dde")
        html_parts.append(
            f'<tr style="background-color:{colour}1f;">'
            f'<td style="padding:8px 10px;border-left:4px solid {colour};font-weight:600;color:#10233F">{record["Métrica"]}</td>'
            f'<td style="padding:8px 10px;color:#10233F">{record["Predicho"]}</td>'
            f'<td style="padding:8px 10px;color:#10233F">{record["Real"]}</td>'
            f'<td style="padding:8px 10px;text-align:right;color:{colour};font-weight:700">{record["Δ"]}</td>'
            '</tr>'
        )
    html_parts.append('</tbody></table></div>')
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def _render_per_team_audit_table(rows: list[dict], team_a: str, team_b: str) -> None:
    if not rows:
        return
    html_parts = [
        '<div class="audit-table-wrap" style="overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;margin:4px 0 12px;">',
        '<table style="width:100%; border-collapse:separate; border-spacing:0 4px; font-size:0.92rem; min-width:560px;">',
        '<thead><tr>'
        '<th rowspan="2" style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">Métrica</th>'
        f'<th colspan="3" style="text-align:center;padding:6px 10px;color:#10233F;font-weight:700;background:#0b1f3a11">{team_a}</th>'
        f'<th colspan="3" style="text-align:center;padding:6px 10px;color:#10233F;font-weight:700;background:#0b1f3a11">{team_b}</th>'
        '</tr>'
        '<tr>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Pred.</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Real</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Δ</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Pred.</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Real</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Δ</th>'
        '</tr></thead><tbody>',
    ]
    for row in rows:
        ta = row["team_a"]
        tb = row["team_b"]
        ca = SEVERITY_COLORS.get(ta["severity"], "#3a8dde")
        cb = SEVERITY_COLORS.get(tb["severity"], "#3a8dde")
        html_parts.append(
            '<tr style="background-color:#ffffff;">'
            f'<td style="padding:8px 10px;font-weight:600;color:#10233F;border-left:4px solid #1769E0">{row["label"]}</td>'
            f'<td style="padding:8px 6px;text-align:right;color:#10233F">{ta["predicted"]}</td>'
            f'<td style="padding:8px 6px;text-align:right;color:#10233F">{ta["actual"]}</td>'
            f'<td style="padding:8px 10px;text-align:right;color:{ca};font-weight:700;background:{ca}1f">{ta["delta_label"]}</td>'
            f'<td style="padding:8px 6px;text-align:right;color:#10233F;border-left:1px solid #e6ecf2">{tb["predicted"]}</td>'
            f'<td style="padding:8px 6px;text-align:right;color:#10233F">{tb["actual"]}</td>'
            f'<td style="padding:8px 10px;text-align:right;color:{cb};font-weight:700;background:{cb}1f">{tb["delta_label"]}</td>'
            '</tr>'
        )
    html_parts.append('</tbody></table></div>')
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def _render_post_match_audit(
    bundle: MatchAnalysisBundle,
    auxiliary: MatchAuxiliaryBundle,
    team_a: str,
    team_b: str,
) -> None:
    result = auxiliary.match_result
    if not result:
        return
    primary_1x2 = {
        "home": next(row.probability for row in bundle.primary if row.selection_name == team_a),
        "draw": next(row.probability for row in bundle.primary if row.selection_name == "Draw"),
        "away": next(row.probability for row in bundle.primary if row.selection_name == team_b),
    }
    mode_row = bundle.exact_score
    try:
        mode_a, mode_b = (int(value) for value in mode_row.selection_name.split("-"))
    except (ValueError, AttributeError):
        mode_a = mode_b = None
    expected_row = next(
        (row for row in bundle.predictions if row.market_name == "Expected Score"),
        None,
    )
    expected_score_value = None
    if expected_row is not None:
        try:
            ea, eb = (float(value) for value in expected_row.selection_name.split("-"))
            expected_score_value = (ea, eb)
        except (ValueError, AttributeError):
            expected_score_value = None
    stats_by_team = {row["team_name"]: row for row in auxiliary.team_match_stats}
    cards = lambda team: (
        (team.get("yellow_cards") or 0) + (team.get("red_cards") or 0)
        if team else None
    )
    team_a_stats = stats_by_team.get(team_a)
    team_b_stats = stats_by_team.get(team_b)
    team_a_for_audit = team_b_for_audit = None
    if team_a_stats:
        team_a_for_audit = {
            "corners": team_a_stats.get("corners"),
            "shots": team_a_stats.get("shots"),
            "shots_on_target": team_a_stats.get("shots_on_target"),
            "cards": cards(team_a_stats),
            "possession": team_a_stats.get("possession"),
        }
    if team_b_stats:
        team_b_for_audit = {
            "corners": team_b_stats.get("corners"),
            "shots": team_b_stats.get("shots"),
            "shots_on_target": team_b_stats.get("shots_on_target"),
            "cards": cards(team_b_stats),
            "possession": team_b_stats.get("possession"),
        }
    brier_values = [
        float(row["brier_score"]) for row in auxiliary.backtests
        if row.get("brier_score") is not None
    ]
    brier_average = sum(brier_values) / len(brier_values) if brier_values else None

    audit = build_match_audit(
        team_a=team_a, team_b=team_b,
        goals_a=int(result["goals_a"]), goals_b=int(result["goals_b"]),
        primary_1x2=primary_1x2,
        mode_score=(mode_a, mode_b) if mode_a is not None and mode_b is not None else None,
        expected_score=expected_score_value,
        team_a_stats=team_a_for_audit, team_b_stats=team_b_for_audit,
        predicted_volume=auxiliary.volume_predictions,
        brier_average=brier_average,
        evaluations=len(auxiliary.backtests),
    )
    st.subheader("Auditoría del partido cerrado")
    st.caption(
        f"Resultado final {audit['actual_score']}. Verde = el modelo acertó · "
        "Azul = razonable · Ámbar = desviación notable · Rojo = error grande."
    )
    metric_cols = st.columns(3)
    metric_cols[0].metric("Marcador final", audit["actual_score"])
    metric_cols[1].metric(
        "Brier medio",
        f"{brier_average:.3f}" if brier_average is not None else "—",
        help=f"Promedio de {len(auxiliary.backtests)} apuestas evaluadas",
    )
    metric_cols[2].metric(
        "Estadísticas observadas",
        len(auxiliary.team_match_stats),
        help="Filas de team_match_stats: alimentan automáticamente las predicciones de partidos posteriores.",
    )
    _render_audit_table(audit["outcome"])
    _render_audit_table(audit["score"])

    # Per-team comparison using deep stats from team_match_stats: this is the
    # comparison the analyst actually wants to read after a match.
    per_team_rows = build_per_team_audit(
        team_a=team_a, team_b=team_b,
        goals_a=int(result["goals_a"]), goals_b=int(result["goals_b"]),
        expected_xg=bundle.expected_xg,
        team_volume_predictions=auxiliary.team_volume_predictions,
        team_a_stats=dict(auxiliary.team_match_stats[0]) if auxiliary.team_match_stats and auxiliary.team_match_stats[0]["team_name"] == team_a else next(
            (dict(row) for row in auxiliary.team_match_stats if row["team_name"] == team_a), None,
        ),
        team_b_stats=next(
            (dict(row) for row in auxiliary.team_match_stats if row["team_name"] == team_b), None,
        ),
    )
    if per_team_rows:
        st.markdown("#### Comparación por equipo (deep stats vs reales)")
        _render_per_team_audit_table(per_team_rows, team_a, team_b)

    _render_audit_table(audit["volume"])
    if not audit["volume"] and not per_team_rows:
        st.caption(
            "Sin estadísticas de equipo todavía. Cuando importes el JSON revisado o cierres "
            "el partido en Calibración con las stats, esta tabla mostrará córners/tarjetas/tiros."
        )
    st.caption(
        "Lo registrado aquí ya alimenta `build_xg_form_adjustment` y `build_volume_rate_observations` "
        "para los próximos partidos de ambas selecciones (auditoría usada, no solo registrada)."
    )


def _match_analysis_bundle(match) -> MatchAnalysisBundle:
    return _match_analysis_bundle_cached(
        match.id,
        _db_signature(),
        _sports_db_signature(),
        _file_signature(OUTCOME_MODEL_PATH),
        PREDICTION_ENGINE_VERSION,
        apply_corrections=_corrections_enabled(),
    )


def render_dashboard() -> None:
    repo = _repo()
    with st.spinner("Actualizando el calendario diario del Mundial…"):
        daily_result = _refresh_current_world_cup_banks(repo)
    _resolve_bracket_after_daily_refresh(repo, daily_result)
    summary = _database_summary()
    hero(
        "Mundial 2026 · Mesa de análisis",
        "Decidir con probabilidades, no con ruido.",
        "Forma actual, cobertura de datos, cuotas manuales, EV y calibración en un solo flujo.",
    )
    cols = st.columns(5)
    metrics = [
        ("Partidos", summary["matches"]),
        ("Selecciones", summary["teams"]),
        ("Importaciones", summary["imports"]),
        ("Predicciones", summary["predictions"]),
        ("Cuotas", summary["odds"]),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, int(value))

    st.subheader("Partidos de hoy y los próximos dos días")
    section_note(
        "El estado de cobertura se calcula por partido; una fuente parcial no bloquea el resto."
    )
    daily_tone = (
        "green" if daily_result.status in {"current", "updated"}
        else "amber" if daily_result.status in {"partial", "stale"}
        else "red"
    )
    st.markdown(
        '<div class="status-row">'
        + status_pill(f"Calendario diario: {localize_status(daily_result.status)}", daily_tone)
        + status_pill(f"Actualizadas {len(daily_result.updated)}", "green" if daily_result.updated else "neutral")
        + status_pill(f"Con error {len(daily_result.failed)}", "red" if daily_result.failed else "neutral")
        + "</div>",
        unsafe_allow_html=True,
    )
    now = datetime.now(timezone.utc)
    local_today = _display_dt(now).date()
    window_end = local_today + timedelta(days=2)
    focus = [
        match for match in _list_matches()
        if local_today <= _display_dt(match.kickoff_utc).date() <= window_end
        and match.status != "finished"
    ]
    if focus:
        # Render a custom HTML table with crests on team names and coloured
        # coverage pills, then a fallback dataframe for sortability.
        rows_html = []
        for match in focus:
            bundle = _cached_bundle(match)
            coverage_label, coverage_tone = _coverage_status(bundle)
            updated_label = (
                _display_time(bundle.updated_at_utc, "%d/%m %H:%M")
                if bundle else "—"
            )
            href = f"?page=lab&match_id={match.id}"
            rows_html.append(
                f"<tr class='match-row' onclick=\"window.location.search='page=lab&match_id={match.id}'\" style='cursor:pointer;'>"
                f'<td style="padding:10px 12px;color:var(--muted);white-space:nowrap;">'
                f'<a href="{href}" class="match-link">{_display_time(match.kickoff_utc, "%d/%m · %H:%M")}</a></td>'
                f'<td style="padding:10px 12px;color:var(--ink);font-weight:600;">'
                f'<a href="{href}" class="match-link"><span class="match-team">{crest_html(match.team_a.name, size=20)}'
                f'<span>{match.team_a.name}</span></span> '
                f'<span style="color:var(--muted);font-weight:500;margin:0 6px;">vs</span> '
                f'<span class="match-team">{crest_html(match.team_b.name, size=20)}'
                f'<span>{match.team_b.name}</span></span></a></td>'
                f'<td style="padding:10px 12px;color:var(--muted);">{match.venue or "—"}</td>'
                f'<td style="padding:10px 12px;">{status_pill(coverage_label, coverage_tone)}</td>'
                f'<td style="padding:10px 12px;color:var(--muted);white-space:nowrap;'
                f'font-feature-settings:\'tnum\' 1,\'lnum\' 1;">{updated_label}</td>'
                "</tr>"
            )
        st.markdown(
            '<div class="soft-panel match-table-wrap" style="padding:0;overflow-x:auto;">'
            '<table style="width:100%;min-width:640px;border-collapse:collapse;font-size:14px;">'
            '<thead><tr style="background:var(--panel-2);">'
            '<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">Hora local</th>'
            '<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">Partido</th>'
            '<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">Sede</th>'
            '<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">Datos</th>'
            '<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">Última captura</th>'
            '</tr></thead><tbody>' + "".join(rows_html) + "</tbody></table></div>",
            unsafe_allow_html=True,
        )
    else:
        empty_state(
            "Sin partidos en la ventana",
            "La fuente diaria no contiene todavía partidos con selecciones confirmadas "
            "en los próximos dos días.",
            icon="📅",
        )

    _render_bracket_section(repo)


BRACKET_STAGE_THEME = {
    "Round of 32":          {"label": "R32", "tone": "blue",   "title": "ROUND OF 32"},
    "Round of 16":          {"label": "R16", "tone": "teal",   "title": "ROUND OF 16"},
    "Quarter-final":        {"label": "QF",  "tone": "green",  "title": "QUARTER-FINALS"},
    "Semi-final":           {"label": "SF",  "tone": "orange", "title": "SEMI-FINALS"},
    "Final":                {"label": "F",   "tone": "gold",   "title": "FINAL"},
    "Third-place play-off": {"label": "3rd", "tone": "grey",   "title": "3RD PLACE"},
}
BRACKET_ORDER = ("Round of 32", "Round of 16", "Quarter-final",
                 "Semi-final", "Third-place play-off", "Final")


def _bracket_card_html(slot: dict, theme: dict) -> str:
    """Tournament-style card: coloured stripe header + two team rows + VS."""
    kickoff_date = _display_time(slot["kickoff_utc"], "%Y-%m-%d")
    venue = slot.get("venue") or ""
    venue_html = f"<span class='bk-venue'>📍 {venue}</span>" if venue else ""
    match_id = slot.get("match_id")
    href = f"?page=lab&match_id={int(match_id)}" if match_id else ""
    open_tag = f"<a class='bk-card-link' href='{href}'>" if href else ""
    close_tag = "</a>" if href else ""

    def team_cell(name: str, pending: bool) -> str:
        if pending:
            return (
                f"<div class='bk-team bk-pending'>"
                f"<span class='bk-flag-placeholder'>?</span>"
                f"<span class='bk-name'>{name}</span></div>"
            )
        return f"<div class='bk-team'>{team_with_crest_html(name, size=20)}</div>"

    return (
        f"{open_tag}<div class='bk-card bk-{theme['tone']}'>"
        f"<div class='bk-card-head'>"
        f"<span class='bk-slot'>{slot['slot_id']}</span>"
        f"<span class='bk-date'>{kickoff_date}</span>"
        f"</div>"
        f"<div class='bk-card-meta'>{venue_html}</div>"
        f"{team_cell(slot['home'], slot['home_pending'])}"
        f"<div class='bk-vs'>VS</div>"
        f"{team_cell(slot['away'], slot['away_pending'])}"
        f"</div>{close_tag}"
    )


def _render_bracket_section(repo: Repository) -> None:
    """Tournament-style bracket: cards in stage columns, colour-coded per
    round, with crest + name + VS. Pending slots show the source token."""
    try:
        resolve_knockout_bracket(repo)
    except Exception:
        pass
    slots = bracket_view(repo)
    if not slots:
        return
    st.subheader("Bracket eliminatorio")
    by_stage: dict[str, list[dict]] = {}
    for slot in slots:
        by_stage.setdefault(slot["stage"], []).append(slot)

    columns_html = []
    for stage in BRACKET_ORDER:
        items = by_stage.get(stage, [])
        if not items:
            continue
        theme = BRACKET_STAGE_THEME[stage]
        cards = "".join(_bracket_card_html(slot, theme) for slot in items)
        columns_html.append(
            f"<div class='bk-column bk-col-{theme['tone']}'>"
            f"<div class='bk-col-title'>{theme['title']}</div>"
            f"{cards}"
            "</div>"
        )
    st.markdown(
        f"<div class='bk-board'>{''.join(columns_html)}</div>",
        unsafe_allow_html=True,
    )


def render_prediction_lab() -> None:
    repo = _repo()
    with st.spinner("Comprobando calendario y bancos diarios del Mundial…"):
        daily_result = _refresh_current_world_cup_banks(repo)
    _resolve_bracket_after_daily_refresh(repo, daily_result)
    matches = _list_matches()
    if not matches:
        empty_state("Sin partidos", "No hay partidos cargados en el calendario.", icon="📅")
        return
    labels, by_label = _match_labels(matches)
    calibration_labels = {
        "Czechia vs South Africa", "Switzerland vs Bosnia and Herzegovina",
        "Canada vs Qatar", "Mexico vs South Korea",
    }
    # Preselection from the dashboard's match links (?match_id=X). We
    # validate against ``by_label`` so we never land on a separator row.
    requested_match_id = st.query_params.get("match_id")
    try:
        requested_match_id = int(requested_match_id) if requested_match_id else None
    except ValueError:
        requested_match_id = None
    preselect_index = None
    if requested_match_id is not None:
        preselect_index = next(
            (index for index, label in enumerate(labels)
             if label in by_label and by_label[label].id == requested_match_id),
            None,
        )
    if preselect_index is None:
        preselect_index = next(
            (index for index, label in enumerate(labels)
             if label in by_label and by_label[label].label in calibration_labels),
            None,
        )
    if preselect_index is None:
        preselect_index = next(
            (index for index, label in enumerate(labels) if label in by_label),
            0,
        )
    selected_label = st.selectbox("Partido", labels, index=preselect_index, label_visibility="collapsed")
    if selected_label not in by_label:
        st.info("Selecciona un partido de la lista (no un separador).")
        return
    match = by_label[selected_label]
    team_a, team_b = match.team_a.name, match.team_b.name
    crest_a = crest_html(team_a, size=44)
    crest_b = crest_html(team_b, size=44)
    title_html = (
        f'<span class="hero-team">{crest_a}<span>{team_a}</span></span>'
        f'<span class="hero-vs">vs</span>'
        f'<span class="hero-team">{crest_b}<span>{team_b}</span></span>'
    )
    hero(
        f"{match.stage} · {_display_time(match.kickoff_utc, '%d %b %Y · %H:%M')}",
        title_html,
        f"{match.venue or 'Sede por confirmar'} · horario local del sistema",
    )

    tone = "green" if daily_result.status in {"current", "updated"} else "amber" if daily_result.status in {"partial", "stale"} else "red"
    st.markdown(
        '<div class="status-row">'
        + status_pill(f"Datos del Mundial: {localize_status(daily_result.status)}", tone)
        + status_pill(f"Actualizados: {len(daily_result.updated)}")
        + status_pill(f"Sin cambios: {len(daily_result.unchanged) + len(daily_result.skipped_recent)}")
        + "</div>",
        unsafe_allow_html=True,
    )

    cache_key = f"refresh_{match.id}"
    cached = _cached_bundle(match)
    # The "Actualizar datos" button uses a Python script that lives in the
    # user's local ~/.codex/skills/ directory. It isn't shipped in the repo
    # and therefore doesn't exist on Streamlit Cloud — clicking it there
    # only produced a red "El recolector local no está instalado" error.
    # We hide the button entirely in environments without the script.
    from wcpredict.refresh import default_collector_script
    collector_available = default_collector_script().exists()
    if collector_available:
        button_col, note_col = st.columns([1, 2.2])
        with button_col:
            refresh_clicked = st.button("Actualizar datos", type="primary", width="stretch")
        with note_col:
            st.caption("Consulta acotada: un partido, máximo 14 llamadas y 0 créditos de cuotas. Conserva la caché si falla.")
    else:
        refresh_clicked = False
    if refresh_clicked:
        with st.spinner("Recopilando y normalizando datos del partido…"):
            result = refresh_match(team_a, team_b, match.kickoff_utc, SPORTS_DATA_DIR)
        st.session_state[cache_key] = result
        if result.bundle is not None:
            repo.import_collector_bundle(match.id, result.bundle)
            cached = result.bundle
            # New bundle data → invalidate cached analysis so the prediction
            # actually reflects the freshly imported evidence.
            st.cache_data.clear(); st.cache_resource.clear()
        status_tone = {
            "complete": ("success", "Datos del partido completos."),
            "partial": ("warning", "Datos parciales: el modelo usa lo disponible y marca lo que falta."),
            "cached": ("info", "No se pudieron añadir datos nuevos; se conserva la caché previa."),
            "failed": ("error", "La actualización falló y no había caché previa."),
            "unavailable": ("error", "El recolector local no está instalado o accesible."),
        }
        tone, default_message = status_tone.get(result.status, ("warning", result.message))
        getattr(st, tone)(default_message)
        metric_cols = st.columns(4)
        metric_cols[0].metric("Llamadas hechas", result.calls_made)
        metric_cols[1].metric("Proveedores OK", len(result.providers))
        metric_cols[2].metric("Cuotas tocadas", len(result.odds_providers))
        metric_cols[3].metric("Faltantes", len(result.missing_critical))
        if result.providers:
            st.caption("Proveedores que respondieron: " + ", ".join(result.providers))
        if result.missing_critical:
            st.warning(
                "Campos no obtenidos en este partido: "
                + ", ".join(result.missing_critical)
                + ". La app no los inventa: aparecerán como vacíos en cobertura."
            )
        if result.odds_status == "skipped_zero_budget":
            st.caption("Cuotas automáticas deshabilitadas (presupuesto 0); usa la pestaña Mercados para meterlas manualmente.")
        if result.stderr_tail:
            with st.expander("Salida técnica del recolector"):
                st.code(result.stderr_tail)
    bundle = _match_analysis_bundle(match)
    current_players = bundle.current_players
    squad_notes = bundle.squad_notes
    deep_rows_before = bundle.deep_rows_before
    prior_deep_samples = bundle.prior_deep_samples
    deep_count = bundle.deep_count
    if cached:
        _render_bundle(cached, deep_count, len(current_players))
    elif not (deep_count or current_players or prior_deep_samples):
        callout(
            "No hay evidencia previa ni caché automática suficiente para modelar este partido con confianza.",
            tone="red", title="Sin datos",
        )

    results = bundle.results
    predictions = bundle.predictions
    score_only_predictions = bundle.score_only_predictions
    primary = bundle.primary
    ml_probabilities = bundle.ml_probabilities
    ml_features = bundle.ml_features
    ml_model_meta = bundle.ml_model_meta
    if bundle.corrections is not None and corrections_active(bundle.corrections):
        callout(describe_corrections(bundle.corrections), tone="blue", title="Corrección automática activa")
    knockout_prediction = _knockout_prediction_for_match(match, bundle, repo)
    is_knockout = knockout_prediction is not None
    top_left, top_right = st.columns([1.55, 1])
    with top_left:
        home_p = next((row.probability for row in primary if row.selection_name == team_a), 0)
        draw_p = next((row.probability for row in primary if row.selection_name == "Draw"), 0)
        away_p = next((row.probability for row in primary if row.selection_name == team_b), 0)
        if is_knockout:
            st.subheader("Probabilidad de clasificación")
            section_note(
                "Partido de eliminatoria: la probabilidad principal es avanzar al siguiente cruce. "
                "Incluye victoria en 90', prórroga y tanda de penaltis; el empate al 90' solo alimenta esas vías."
            )
            bars_html = (
                probability_bar(team_with_crest_html(team_a, size=18), knockout_prediction.home_advances, "win")
                + probability_bar(team_with_crest_html(team_b, size=18), knockout_prediction.away_advances, "loss")
            )
        else:
            st.subheader("Probabilidad 1X2")
            section_note(
                "Modelo unificado: matriz de marcadores (xG ajustado + Dixon-Coles) + "
                "ML cronológico (Elo, ~50k partidos) + ML deep stats (HistGBM con xG/posesión/tiros/defensa). "
                "Pesos adaptativos según la muestra deep disponible para cada equipo."
            )
            bars_html = (
                probability_bar(team_with_crest_html(team_a, size=18), home_p, "win")
                + probability_bar("Empate", draw_p, "draw")
                + probability_bar(team_with_crest_html(team_b, size=18), away_p, "loss")
            )
        st.markdown(bars_html, unsafe_allow_html=True)
    with top_right:
        best = max(primary, key=lambda row: row.probability)
        exact_score = next(row for row in predictions if row.market_name == "Exact Score")
        alt_scores = [row for row in predictions if row.market_name == "Exact Score (alt)"]
        expected_row = next(
            (row for row in predictions if row.market_name == "Expected Score"),
            None,
        )
        st.subheader("Lectura inmediata")
        if is_knockout:
            advancing_team = team_a if knockout_prediction.home_advances >= knockout_prediction.away_advances else team_b
            advancing_probability = max(knockout_prediction.home_advances, knockout_prediction.away_advances)
            st.metric("Clasifica", advancing_team, f"{advancing_probability:.1%}")
        else:
            st.metric("Resultado más probable", localize_selection(best.selection_name), f"{best.probability:.1%}")
        st.metric("Marcador más probable (modo)", exact_score.selection_name, f"{exact_score.probability:.1%}")
        if expected_row is not None:
            st.metric(
                "Marcador esperado (goles xG)",
                expected_row.selection_name,
                help="Goles esperados según la distribución conjunta. Es una lectura promedio, no un marcador entero.",
            )
        if alt_scores:
            alt_lines = " · ".join(
                f"{row.selection_name.split(' ')[0]} ({row.probability:.1%})"
                for row in alt_scores[:3]
            )
            st.caption(f"Alternativos más probables: {alt_lines}")
        short_explanation = best.explanation.split("Ajuste de jugadores:", 1)[0].strip()
        if is_knockout:
            st.caption(
                f"Resultado a 90': {team_a} {home_p:.1%} · empate {draw_p:.1%} · {team_b} {away_p:.1%}. "
                "Si hay empate, el modelo continúa con prórroga y penaltis."
            )
        else:
            st.caption(short_explanation)
        with st.expander("Ver cálculo y jugadores usados"):
            st.caption(best.explanation)
        if best.confidence.value == "low":
            st.warning("Confianza baja: la base observada para estos equipos aún es insuficiente.")

    section = st.segmented_control(
        "Vista de análisis",
        ["Modelo", "Marcadores", "Mercados y EV", "Jugadores", "Datos / SofaScore", "Guardado"],
        default="Modelo",
        label_visibility="collapsed",
    )
    deep_ml_probabilities = bundle.deep_ml_probabilities
    deep_weight = bundle.deep_outcome_weight
    if section == "Modelo":
        if match.status == "finished":
            _render_post_match_audit(bundle, _match_auxiliary_context(match), team_a, team_b)
        with st.expander("Cómo se elige el modelo de cada mercado"):
            st.caption("Activo es lo que calcula hoy la app; challenger solo se promueve si gana una validación temporal.")
            st.dataframe(pd.DataFrame(model_policy_rows()), width="stretch", hide_index=True)
        if ml_probabilities is not None and ml_features is not None and ml_model_meta is not None:
            score_probabilities = {
                "home": next(row.probability for row in score_only_predictions if row.market_name == "1X2" and row.selection_name == team_a),
                "draw": next(row.probability for row in score_only_predictions if row.market_name == "1X2" and row.selection_name == "Draw"),
                "away": next(row.probability for row in score_only_predictions if row.market_name == "1X2" and row.selection_name == team_b),
            }
            unified_probabilities = {
                "home": next(row.probability for row in primary if row.selection_name == team_a),
                "draw": next(row.probability for row in primary if row.selection_name == "Draw"),
                "away": next(row.probability for row in primary if row.selection_name == team_b),
            }
            
            # Diagnostic with up to 4 columns: unified / ML / ML deep / matrix
            comparison_rows = model_comparison_rows(
                team_a, team_b, score_probabilities, ml_probabilities,
                unified_probabilities,
                deep_ml_probabilities=deep_ml_probabilities,
            )
            with st.expander("Diagnóstico de señales"):
                col_cfg = {
                    "Modelo unificado 1X2 (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "ML cronológico (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "Matriz de marcadores (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "Diferencia (pp)": st.column_config.NumberColumn(format="%+.1f"),
                }
                if deep_ml_probabilities is not None:
                    col_cfg["ML deep stats (%)"] = st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)
                st.dataframe(
                    pd.DataFrame(comparison_rows),
                    width="stretch",
                    hide_index=True,
                    column_config=col_cfg,
                )
        else:
            callout("Modelo ML no activado: ejecuta scripts/import_open_history.py para crear el artefacto calibrado.")

        # Panel completo para partidos de eliminatoria (sustituye al 1X2
        # estándar con avance/vía/cruce + 90' + ET/penaltis). Para grupos
        # devuelve False y caemos al flujo de mercados normales abajo.
        _render_knockout_panel(
            match, bundle, team_a, team_b, repo,
            predictions=predictions,
            primary=primary,
            expected_xg=bundle.expected_xg,
        )

        st.subheader("Mercados modelados")
        frame = pd.DataFrame(prediction_rows(predictions)).rename(
            columns={"Market": "Mercado", "Selection": "Selección", "Line": "Línea", "Probability": "Prob.", "Low": "Mín.", "High": "Máx.", "Confidence": "Confianza", "Sample": "Muestra", "Origin": "Origen", "Explanation": "Explicación"}
        )
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={
                "Prob.": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=1),
                "Mín.": st.column_config.NumberColumn(format="%.1f%%"),
                "Máx.": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        _render_volume_markets(_match_volume_context(match))
        if st.button("Guardar snapshot de predicciones", width="stretch"):
            now = datetime.now(timezone.utc)
            persisted_predictions = [
                row for row in predictions if row.market_name != "Exact Score Grid"
            ]
            for row in persisted_predictions:
                repo.add_prediction(match.id, row.market_family.value, row.market_name, row.selection_name, row.line, row.probability, row.confidence.value, now, row.explanation)
            st.success(f"Snapshot guardado: {len(persisted_predictions)} mercados.")

    elif section == "Marcadores":
        _render_exact_score_panel(team_a, team_b, predictions)

    elif section == "Mercados y EV":
        saved_odds_for_match = repo.list_manual_odds(match.id)
        auxiliary = _match_auxiliary_context(match)
        ko_prediction = _knockout_prediction_for_match(match, bundle, repo)
        _render_market_visual_panel(
            team_a, team_b, predictions, auxiliary.volume_predictions, saved_odds_for_match,
            match=match, ko_prediction=ko_prediction,
        )
        st.markdown("---")
        st.markdown("**Introducir cuotas manualmente para calcular EV**")
        st.caption("Rellena únicamente las cuotas que quieras comparar. La app no envía apuestas ni accede a tu cuenta.")
        uploaded_odds = st.file_uploader("Importar cuotas CSV", type=["csv"], key=f"odds_csv_{match.id}")
        if uploaded_odds is not None:
            try:
                csv_odds = parse_odds_csv(
                    uploaded_odds.getvalue().decode("utf-8-sig"),
                    match.id,
                    datetime.now(timezone.utc),
                )
            except (UnicodeDecodeError, ValueError) as exc:
                st.error(f"CSV no válido: {exc}")
            else:
                st.success(f"CSV validado: {len(csv_odds)} cuotas listas para guardar.")
                if st.button("Guardar cuotas del CSV", width="stretch"):
                    for row in csv_odds:
                        repo.add_manual_odds(row.match_id, row.market_family.value, row.market_name, row.selection_name, row.line, row.decimal_odds, row.bookmaker, row.captured_at_utc)
                    st.success(f"Guardadas {len(csv_odds)} cuotas del CSV.")
        odds_frame = pd.DataFrame(default_market_rows(team_a, team_b))
        odds_frame["market_family"] = odds_frame["market_family"].map(localize_market_family)
        odds_frame["market_name"] = odds_frame["market_name"].map(localize_market)
        odds_frame["selection_name"] = odds_frame["selection_name"].map(localize_selection)
        editor = st.data_editor(
            odds_frame,
            key=f"odds_{match.id}", width="stretch", num_rows="dynamic", hide_index=True,
            column_config={
                "market_family": st.column_config.TextColumn("Familia"),
                "market_name": st.column_config.TextColumn("Mercado"),
                "selection_name": st.column_config.TextColumn("Selección"),
                "line": st.column_config.NumberColumn("Línea"),
                "decimal_odds": st.column_config.NumberColumn("Cuota", min_value=1.01, step=0.01),
                "bookmaker": st.column_config.TextColumn("Casa"),
            },
        )
        edited_odds = editor.to_dict("records")
        for row in edited_odds:
            row["market_family"] = canonical_market_family(str(row.get("market_family") or ""))
            row["market_name"] = canonical_market(str(row.get("market_name") or ""))
            row["selection_name"] = canonical_selection(str(row.get("selection_name") or ""))
        entered = normalize_market_rows(edited_odds)
        prediction_index = _prediction_index(predictions)
        comparisons = []
        for row in entered:
            model = prediction_index.get((row["market_name"], row["selection_name"]))
            if model:
                push_probability = 0.0
                if model.market_name == "Draw No Bet":
                    draw_model = prediction_index.get(("1X2", "Draw"))
                    push_probability = draw_model.probability if draw_model else 0.0
                    model_probability = model.probability * max(0.0, 1.0 - push_probability)
                else:
                    model_probability = model.probability
                comparisons.append(compare_odds_to_probability(
                    model_probability,
                    row["decimal_odds"],
                    model.market_family,
                    model.market_name,
                    model.selection_name,
                    model.confidence.value,
                    push_probability=push_probability,
                ))
        if comparisons:
            ranked = sorted(ev_rows(comparisons), key=lambda row: row["EV"], reverse=True)
            st.subheader("Ranking EV")
            st.dataframe(pd.DataFrame(ranked), width="stretch", hide_index=True)
        elif entered:
            callout("Las cuotas introducidas aún no tienen un modelo comparable; quedan guardables, pero sin EV inventado.")
        if st.button("Guardar cuotas rellenadas", width="stretch"):
            captured = datetime.now(timezone.utc)
            for row in entered:
                repo.add_manual_odds(match.id, row["market_family"], row["market_name"], row["selection_name"], row["line"], row["decimal_odds"], row["bookmaker"], captured)
            st.success(f"Guardadas {len(entered)} cuotas.")

    elif section == "Jugadores":
        auxiliary = _match_auxiliary_context(match)
        lineups = repo.list_imported_lineups(match.id)
        if lineups:
            st.success("Alineación importada para este partido.")
            with st.expander("Ver alineación"):
                st.dataframe(_visible_frame(lineups), width="stretch", hide_index=True)
        else:
            st.info("Alineación no confirmada: las tasas observadas sí están disponibles, pero la confianza se mantiene baja.")
        st.caption("Elige jugador, mercado, línea y cuota. La tasa por 90, los minutos y la titularidad se calculan desde el banco de jugadores.")
        selected_team = st.segmented_control(
            "Equipo",
            [team_a, team_b],
            default=team_a,
            label_visibility="collapsed",
        )
        default_lines = {
            MarketFamily.PLAYER_GOAL: 0.5,
            MarketFamily.PLAYER_ASSIST: 0.5,
            MarketFamily.PLAYER_SHOTS: 1.5,
            MarketFamily.PLAYER_SHOTS_ON_TARGET: 0.5,
            MarketFamily.PLAYER_CARDS: 0.5,
            MarketFamily.PLAYER_PASSES: 29.5,
            MarketFamily.PLAYER_SAVES: 2.5,
            MarketFamily.PLAYER_GOALS_CONCEDED: 1.5,
            MarketFamily.PLAYER_CLEAN_SHEET: 0.5,
        }
        # Pre-compute opponent SOT expectation per team so GK markets get a
        # realistic baseline instead of the global default.
        sot_predictions = auxiliary.team_volume_predictions.get("shots_on_target", {})
        opponent_sot_for = {
            team_a: sot_predictions.get(team_b),
            team_b: sot_predictions.get(team_a),
        }
        for team_name in (selected_team,):
                team_players = sorted(
                    (
                        row for row in current_players
                        if same_team(str(row.get("team_name") or ""), team_name)
                        and int(row.get("minutes") or 0) > 0
                    ),
                    key=lambda row: (-int(row.get("minutes") or 0), str(row.get("player_name") or "")),
                )
                if not team_players:
                    st.warning(f"No hay estadísticas observadas de jugadores de {team_name}.")
                    continue
                goalkeepers = [row for row in team_players if is_goalkeeper(row)]
                field_players = [row for row in team_players if not is_goalkeeper(row)]
                # Player roster table: lets the user scan candidates and pick
                # interesting names before drilling into market/odds entry.
                st.markdown(f"**Plantilla disponible de {team_name}**")

                position_filter = st.radio(
                    "Filtrar por posición",
                    ["Todos", "Campo", "Porteros"],
                    horizontal=True,
                    key=f"roster_pos_{match.id}_{team_name}",
                )
                if position_filter == "Campo":
                    roster_source = field_players
                elif position_filter == "Porteros":
                    roster_source = goalkeepers
                else:
                    roster_source = team_players

                if not roster_source:
                    st.info(
                        "No hay jugadores en este filtro." +
                        ("" if goalkeepers else " (Aún sin porteros con minutos publicados.)")
                    )
                    continue

                def per90(value, minutes):
                    minutes = int(minutes or 0)
                    if not minutes:
                        return None
                    return round(90.0 * float(value or 0) / minutes, 2)

                roster_rows = []
                for row in roster_source:
                    minutes = int(row.get("minutes") or 0)
                    games = max(1, int(row.get("games") or 0))
                    starts = int(row.get("starts") or 0)
                    base = {
                        "Jugador": row.get("player_name"),
                        "Posición": row.get("position") or "—",
                        "Min": minutes,
                        "Partidos": games,
                        "Titularidad": f"{min(1.0, starts / games):.0%}",
                    }
                    if is_goalkeeper(row):
                        save_pct = row.get("save_percentage")
                        base.update({
                            "Save %": round(float(save_pct), 1) if save_pct is not None else None,
                            "Paradas": int(row.get("saves") or 0),
                            "GC": int(row.get("goals_conceded") or 0),
                            "Intercepciones": row.get("interceptions") or 0,
                            "Pases": int(row.get("passes") or 0),
                            "Amarillas": int(row.get("yellow_cards") or 0),
                            "Rojas": int(row.get("red_cards") or 0),
                        })
                    else:
                        base.update({
                            "Goles": int(row.get("goals") or 0),
                            "Asist.": int(row.get("assists") or 0),
                            "Tiros": int(row.get("shots") or 0),
                            "SOT": int(row.get("shots_on_target") or 0),
                            "Amarillas": int(row.get("yellow_cards") or 0),
                            "Rojas": int(row.get("red_cards") or 0),
                            "Pases": int(row.get("passes") or 0),
                            "G/90": per90(row.get("goals"), minutes),
                            "A/90": per90(row.get("assists"), minutes),
                            "Tiros/90": per90(row.get("shots"), minutes),
                            "SOT/90": per90(row.get("shots_on_target"), minutes),
                        })
                    roster_rows.append(base)
                roster_frame = pd.DataFrame(roster_rows)
                min_minutes = st.slider(
                    "Minutos mínimos para mostrar",
                    0, max(90, int(roster_frame["Min"].max())),
                    0, 30,
                    key=f"roster_min_{match.id}_{team_name}_{position_filter}",
                )
                visible_roster = roster_frame[roster_frame["Min"] >= min_minutes]
                column_config = {
                    "G/90": st.column_config.NumberColumn(format="%.2f"),
                    "A/90": st.column_config.NumberColumn(format="%.2f"),
                    "Tiros/90": st.column_config.NumberColumn(format="%.2f"),
                    "SOT/90": st.column_config.NumberColumn(format="%.2f"),
                    "Save %": st.column_config.NumberColumn(format="%.1f%%"),
                }
                st.dataframe(
                    visible_roster, width="stretch", hide_index=True,
                    column_config={k: v for k, v in column_config.items() if k in visible_roster.columns},
                )
                st.caption(
                    f"{len(visible_roster)}/{len(roster_frame)} jugadores visibles. "
                    "Usa esta tabla para identificar a quién meterle cuota antes del cálculo de EV."
                )
                player_by_name = {str(row["player_name"]): row for row in roster_source}
                selected_player = st.selectbox(
                    f"Jugador de {team_name}",
                    list(player_by_name),
                    key=f"player_select_{match.id}_{team_name}_{position_filter}",
                )
                player_row = player_by_name[selected_player]
                gk_mode = is_goalkeeper(player_row)
                if gk_mode:
                    # Goalkeeper markets only — saves / goals conceded / clean sheet.
                    available_families = [
                        family for family in (
                            MarketFamily.PLAYER_SAVES,
                            MarketFamily.PLAYER_GOALS_CONCEDED,
                            MarketFamily.PLAYER_CLEAN_SHEET,
                        )
                        if player_row.get(PLAYER_MARKET_METRICS[family]) is not None
                    ]
                else:
                    available_families = [
                        family for family, metric in PLAYER_MARKET_METRICS.items()
                        if family not in GOALKEEPER_MARKETS and player_row.get(metric) is not None
                    ]
                if not available_families:
                    st.warning("Este jugador tiene minutos, pero ninguna métrica de mercado publicada.")
                    continue
                family_picker = st.selectbox(
                    "Mercado",
                    available_families,
                    format_func=lambda value: localize_market_family(value.value),
                    key=f"player_market_{match.id}_{team_name}_{position_filter}",
                )
                family = family_picker
                # Clean sheet is binary; line is meaningless. For the rest the
                # user picks the line.
                if family == MarketFamily.PLAYER_CLEAN_SHEET:
                    line = 0.5
                    odds_col, = st.columns(1)
                    player_odds = odds_col.number_input(
                        "Cuota",
                        min_value=1.01,
                        value=2.0,
                        step=0.01,
                        key=f"player_odds_{match.id}_{team_name}_{family.value}",
                    )
                else:
                    line_col, odds_col = st.columns(2)
                    line = line_col.number_input(
                        "Línea",
                        min_value=0.0,
                        value=float(default_lines[family]),
                        step=0.5,
                        key=f"player_line_{match.id}_{team_name}_{family.value}",
                    )
                    player_odds = odds_col.number_input(
                        "Cuota",
                        min_value=1.01,
                        value=2.0,
                        step=0.01,
                        key=f"player_odds_{match.id}_{team_name}_{family.value}",
                    )
                if family in GOALKEEPER_MARKETS:
                    baseline = auxiliary.goalkeeper_baselines.get(team_name)
                    bank_save_pct = (
                        float(player_row.get("bank_save_percentage") or player_row.get("save_percentage") or 0)
                    ) / 100.0
                    save_override = None
                    if baseline and baseline.save_rate is not None and baseline.sample_matches >= 1:
                        # Sample-weighted blend: with 1 deep match the blend is
                        # only 1/3 of the way toward the deep value; at 3+ it
                        # fully replaces the bank rate. This is honest given the
                        # group-stage sample sizes (most teams have 1-2 matches).
                        weight = min(1.0, baseline.sample_matches / 3.0)
                        save_override = weight * baseline.save_rate + (1 - weight) * bank_save_pct
                        st.caption(
                            f"Histórico deep de {team_name}: {baseline.sample_matches} partido(s) profundo(s) · "
                            f"save_rate observado {baseline.save_rate:.0%} · banco diario {bank_save_pct:.0%} → "
                            f"mezcla {weight:.0%} deep / {(1-weight):.0%} banco = save% efectivo {save_override:.0%}."
                        )
                    elif baseline and baseline.sample_matches == 0:
                        st.caption(
                            f"Histórico deep de {team_name}: aún sin paradas registradas; "
                            f"se usa save% {bank_save_pct:.0%} del banco diario."
                        )
                    derived = derive_player_assumption(
                        player_row, family,
                        opponent_sot_per90=opponent_sot_for.get(team_name),
                        team_save_rate_override=save_override,
                    )
                else:
                    derived = derive_player_assumption(player_row, family)
                if derived is None:
                    st.warning("La fuente no aporta minutos o la métrica necesaria; este mercado no se estima.")
                    continue
                rate_labels = {
                    MarketFamily.PLAYER_SAVES: "Paradas esperadas / 90",
                    MarketFamily.PLAYER_GOALS_CONCEDED: "Goles concedidos / 90",
                    MarketFamily.PLAYER_CLEAN_SHEET: "Goles concedidos esperados / 90",
                }
                detail_cols = st.columns(3)
                detail_cols[0].metric(
                    rate_labels.get(family, "Tasa observada / 90"),
                    f"{derived.assumption.per90_rate:.2f}",
                )
                detail_cols[1].metric("Minutos esperados", derived.assumption.expected_minutes)
                detail_cols[2].metric("Prob. de titularidad", f"{derived.assumption.starter_probability:.0%}")
                st.caption(derived.explanation)
                estimate = estimate_player_market_probability(
                    derived.assumption, family, line, derived.sample_size
                )
                if estimate.probability is None:
                    st.warning(estimate.explanation)
                elif estimate.probability <= 0:
                    st.info("Probabilidad estimada 0.0%; no hay cuota justa ni EV calculable para este mercado.")
                    st.caption(estimate.explanation)
                else:
                    player_ev_comparison = compare_odds_to_probability(
                        estimate.probability,
                        player_odds,
                        family,
                        family.value,
                        selected_player,
                        estimate.confidence.value,
                    )
                    st.dataframe(pd.DataFrame(ev_rows([player_ev_comparison])), width="stretch", hide_index=True)
                    st.caption(estimate.explanation)

    elif section == "Datos / SofaScore":
        st.subheader("Importar estadísticas profundas revisadas")
        st.caption("Admite el JSON estructurado obtenido de capturas y conserva su procedencia. No crea sanciones nominales sin identificar al jugador.")
        deep_upload = st.file_uploader("JSON de estadísticas de partidos", type=["json"], key=f"deep_json_{match.id}")
        reviewed_json = st.checkbox("He revisado que equipos y valores corresponden a las capturas", key=f"deep_json_reviewed_{match.id}")
        if st.button("Validar e importar JSON", disabled=deep_upload is None or not reviewed_json, key=f"deep_json_import_{match.id}"):
            content = deep_upload.getvalue()
            evidence_dir = DATA_DIR / "evidence" / "reviewed-json"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            stored = evidence_dir / f"{sha256(content).hexdigest()}.json"
            stored.write_bytes(content)
            try:
                collection = load_deep_match_file(stored)
                imported_deep = repo.import_deep_match_collection(
                    collection,
                    datetime.now(timezone.utc),
                    intended_match_id=match.id,
                )
                st.cache_data.clear(); st.cache_resource.clear()
                st.success(
                    f"Importados {imported_deep.imported_matches}; sin cambios {imported_deep.unchanged_matches}; "
                    f"observaciones {imported_deep.observations}."
                )
                if imported_deep.ambiguous_matches or imported_deep.unmatched_matches:
                    st.warning(
                        f"Ambiguos: {imported_deep.ambiguous_matches}; sin partido: {imported_deep.unmatched_matches}."
                    )
                # Show which UPCOMING fixtures will actually use this evidence
                # through advanced_form / volume rates.
                teams_with_new_evidence = sorted({
                    record.team_a for record in collection.matches
                } | {
                    record.team_b for record in collection.matches
                })
                now = datetime.now(timezone.utc)
                upcoming = [
                    upcoming for upcoming in repo.list_matches()
                    if upcoming.kickoff_utc > now
                    and any(
                        same_team(upcoming.team_a.name, team) or same_team(upcoming.team_b.name, team)
                        for team in teams_with_new_evidence
                    )
                ]
                if upcoming:
                    affected_rows = [
                        {
                            "Partido": item.label,
                            "Kickoff": _display_time(item.kickoff_utc, "%d/%m %H:%M"),
                            "Selecciones afectadas": ", ".join(
                                team for team in teams_with_new_evidence
                                if same_team(item.team_a.name, team) or same_team(item.team_b.name, team)
                            ),
                        }
                        for item in upcoming[:25]
                    ]
                    st.info(
                        f"Esta evidencia alimenta el modelo (xG, tiros, posesión, córners y tarjetas) "
                        f"para {len(upcoming)} partidos futuros de selecciones cargadas."
                    )
                    st.dataframe(pd.DataFrame(affected_rows), width="stretch", hide_index=True)
                else:
                    st.caption(
                        "Las estadísticas quedaron persistidas. Aún no hay partidos futuros de estas selecciones en el calendario; "
                        "se aplicarán automáticamente cuando los haya."
                    )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                st.error(f"JSON rechazado: {exc}")
        st.divider()
        if cached:
            if cached.statistics:
                st.subheader("Evidencia normalizada")
                st.dataframe(_visible_frame(cached.statistics), width="stretch", hide_index=True)
            if cached.lineups:
                st.subheader("Jugadores")
                st.dataframe(_visible_frame(cached.lineups), width="stretch", hide_index=True)

    elif section == "Guardado":
        saved_predictions = repo.list_predictions(match.id)
        saved_odds = repo.list_manual_odds(match.id)
        imports = repo.list_import_runs(match.id)
        if imports:
            st.subheader("Historial de datos")
            st.dataframe(_visible_frame(imports), width="stretch", hide_index=True)
        if saved_predictions:
            st.subheader("Predicciones")
            st.dataframe(_visible_frame(saved_predictions), width="stretch", hide_index=True)
        if saved_odds:
            st.subheader("Cuotas")
            st.dataframe(_visible_frame(saved_odds), width="stretch", hide_index=True)
        if not imports and not saved_predictions and not saved_odds:
            empty_state("Sin predicciones guardadas", "Guarda un snapshot antes del partido para evaluarlo después.", icon="📭")


def _render_global_bias_panel() -> None:
    """Show the model's systematic biases across all matches with deep stats,
    independent of which teams are involved.

    Heavy: each refresh reconstructs N predictions. Therefore it sits inside a
    collapsed expander and only runs when the user opts in via the button (or
    the toggle for auto-corrections is already on).
    """
    st.subheader("Sesgo global del modelo")
    auto_corrections_on = bool(st.session_state.get("apply_corrections", False))
    with st.expander(
        "Recalcular reporte de calibración (pesado)",
        expanded=auto_corrections_on,
    ):
        st.caption(
            "Reconstruye la predicción de cada partido cerrado con stats profundas "
            "usando SOLO datos anteriores a su kickoff y compara con lo real. "
            "Tarda unos segundos por la cantidad de partidos. Sólo es necesario "
            "cuando quieras revisar el sesgo o activar la corrección automática."
        )
        run = st.button(
            "Calcular reporte ahora", key="run_bias_report", type="primary"
        )
        if not run and not auto_corrections_on:
            st.info(
                "Reporte no calculado. Pulsa el botón para generarlo, o activa "
                "la corrección automática para que se mantenga al día."
            )
            return
        samples, report = _calibration_bias_report()
    if report.sample_size == 0:
        st.info(report.notes[0] if report.notes else "Sin datos.")
        return
    cols = st.columns(4)
    cols[0].metric("Partidos auditados", report.sample_size)
    cols[1].metric("Acierto 1X2 (argmax)", f"{report.outcome_accuracy:.0%}")
    if report.xg_bias_per_team is not None:
        cols[2].metric(
            "Sesgo xG /equipo",
            f"{report.xg_bias_per_team:+.2f}",
            help=f"MAE {report.xg_mean_absolute_error:.2f}. Positivo = el modelo sobreestima.",
        )
    if report.total_goals_bias is not None:
        cols[3].metric(
            "Sesgo total de goles",
            f"{report.total_goals_bias:+.2f}",
            help=f"MAE {report.total_goals_mae:.2f}. Positivo = el modelo sobreestima.",
        )
    # 1X2 calibration table.
    st.markdown("**Calibración 1X2 (frecuencia real vs media predicha)**")
    cal_rows = [
        {"Resultado": "Local", "Media predicha": f"{report.home_predicted_avg:.1%}",
         "Frecuencia real": f"{report.home_actual_frequency:.1%}",
         "Gap (pp)": f"{(report.home_predicted_avg - report.home_actual_frequency)*100:+.1f}"},
        {"Resultado": "Empate", "Media predicha": f"{report.draw_predicted_avg:.1%}",
         "Frecuencia real": f"{report.draw_actual_frequency:.1%}",
         "Gap (pp)": f"{(report.draw_predicted_avg - report.draw_actual_frequency)*100:+.1f}"},
        {"Resultado": "Visitante", "Media predicha": f"{report.away_predicted_avg:.1%}",
         "Frecuencia real": f"{report.away_actual_frequency:.1%}",
         "Gap (pp)": f"{(report.away_predicted_avg - report.away_actual_frequency)*100:+.1f}"},
    ]
    st.dataframe(pd.DataFrame(cal_rows), width="stretch", hide_index=True)
    # Favourites calibration.
    if report.favourites_calibration:
        fav_rows = [
            {"Confianza del modelo": label, "N": data["n"],
             "Media predicha": f"{data['predicted']:.0%}",
             "Acertados realmente": f"{data['actual']:.0%}",
             "Gap (pp)": f"{(data['predicted'] - data['actual'])*100:+.1f}"}
            for label, data in report.favourites_calibration.items()
        ]
        st.markdown("**Cuando el modelo elige favorito, ¿acierta?**")
        st.dataframe(pd.DataFrame(fav_rows), width="stretch", hide_index=True)
    if report.notes:
        callout(
            "Sesgos significativos detectados:<br>• " + "<br>• ".join(report.notes),
            tone="amber", title="Sesgos detectados",
        )
    else:
        callout("Sin sesgos significativos detectados con la muestra actual.", tone="green")

    # Bayesian-shrunk auto-correction toggle.
    st.markdown("**Corrección automática del motor**")
    preview = derive_corrections(report)
    st.caption(describe_corrections(preview))
    apply_toggle = st.toggle(
        "Aplicar corrección al motor para todas las predicciones",
        key="apply_corrections",
        help=(
            "Aplica un shrinkage bayesiano: con muestra pequeña sólo aplica una "
            "fracción del sesgo medido, y se acerca al sesgo completo conforme "
            "se acumulan más partidos. El cambio invalida la caché y se ve "
            "reflejado en la pestaña Predicción y valor."
        ),
    )
    if apply_toggle and not corrections_active(preview):
        st.info(
            "Toggle ON pero ningún parámetro supera los umbrales mínimos; "
            "no se está aplicando corrección."
        )
    with st.expander("Ver muestras individuales auditadas"):
        rows = [{
            "Partido": f"{s.team_a} vs {s.team_b}",
            "Kickoff": _display_time(s.kickoff_utc, "%d/%m %H:%M"),
            "Pred. local": f"{s.predicted_1x2['home']:.0%}",
            "Pred. empate": f"{s.predicted_1x2['draw']:.0%}",
            "Pred. visitante": f"{s.predicted_1x2['away']:.0%}",
            "Real": s.actual_outcome,
            "xG pred. A": f"{s.predicted_xg_a:.2f}" if s.predicted_xg_a else "—",
            "xG real A": f"{s.actual_xg_a:.2f}" if s.actual_xg_a else "—",
            "xG pred. B": f"{s.predicted_xg_b:.2f}" if s.predicted_xg_b else "—",
            "xG real B": f"{s.actual_xg_b:.2f}" if s.actual_xg_b else "—",
        } for s in samples]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.divider()


def _int_stat(value) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _card_totals_by_team(existing_stats: list[dict], team_names: tuple[str, str]) -> dict[str, dict[str, int]]:
    stats_by_team = {str(row.get("team_name") or ""): row for row in existing_stats}
    totals: dict[str, dict[str, int]] = {}
    for team_name in team_names:
        row = stats_by_team.get(team_name, {})
        totals[team_name] = {
            "yellow_cards": _int_stat(row.get("yellow_cards")),
            "red_cards": _int_stat(row.get("red_cards")),
        }
    return totals


def _card_player_options(repo: Repository, match, current_players: list[dict]) -> dict[str, list[str]]:
    team_names = (match.team_a.name, match.team_b.name)
    options: dict[str, list[str]] = {team_name: [] for team_name in team_names}
    seen: dict[str, set[str]] = {team_name: set() for team_name in team_names}

    def add(team_name: str, player_name: str | None) -> None:
        name = str(player_name or "").strip()
        if not name or name in seen[team_name]:
            return
        seen[team_name].add(name)
        options[team_name].append(name)

    for row in current_players:
        row_team = str(row.get("team_name") or "")
        for team_name in team_names:
            if same_team(row_team, team_name):
                add(team_name, row.get("player_name"))

    for row in repo.list_imported_lineups(match.id):
        row_team = str(row.get("team_name") or "")
        for team_name in team_names:
            if same_team(row_team, team_name):
                add(team_name, row.get("player_name"))

    for team_name in team_names:
        if options[team_name]:
            continue
        for row in repo.list_current_world_cup_players(team_name):
            add(team_name, row.get("player_name"))

    return {team_name: sorted(names) for team_name, names in options.items()}


def _aggregate_card_assignments(assignments: list[dict]) -> tuple[list[dict], list[str]]:
    missing: list[str] = []
    counts: dict[tuple[str, str, str], int] = {}
    for row in assignments:
        player_name = str(row.get("player_name") or "").strip()
        if not player_name:
            missing.append(f"{row['team_name']} · {row['label']}")
            continue
        key = (str(row["team_name"]), player_name, str(row["metric"]))
        counts[key] = counts.get(key, 0) + 1
    rows = [
        {"team_name": team_name, "player_name": player_name, "metric": metric, "count": count}
        for (team_name, player_name, metric), count in sorted(counts.items())
    ]
    return rows, missing


def render_backtesting() -> None:
    hero("Calibración", "¿Predice bien el modelo?", "Brier score, acierto y bandas de probabilidad; no confundir una muestra corta con rentabilidad demostrada.")
    _render_global_bias_panel()
    repo = _repo()
    matches = _list_matches()
    all_statuses = _all_evidence_statuses()
    due = [m for m in matches if m.kickoff_utc < datetime.now(timezone.utc) and not all_statuses.get(m.id, {}).get("has_result")]
    if due:
        with_statistics = sum(
            all_statuses.get(item.id, {}).get("has_team_statistics") or all_statuses.get(item.id, {}).get("deep_observations", 0) > 0
            for item in due
        )
        callout(
            postmatch_queue_message(
                pending_scores=len(due),
                with_imported_statistics=with_statistics,
                missing_statistics=len(due) - with_statistics,
            ),
            tone="amber", title="Partidos pendientes de cierre",
        )
    now_utc = datetime.now(timezone.utc)
    status_by_id: dict[int, dict] = {}
    for item in matches:
        info = all_statuses.get(item.id, {})
        has_result = bool(info.get("has_result"))
        has_stats = bool(info.get("has_team_statistics") or info.get("deep_observations"))
        future = item.kickoff_utc > now_utc
        if future and not has_result and not has_stats:
            tag, label_word = "⏭️", "Sin jugar"
        elif has_result and has_stats:
            tag, label_word = "✅", "Completo"
        elif has_stats and not has_result:
            tag, label_word = "🟡", "Falta marcador"
        elif has_result and not has_stats:
            tag, label_word = "📊", "Falta estadísticas"
        else:
            tag, label_word = "🔴", "Faltan stats y marcador"
        status_by_id[item.id] = {"tag": tag, "label": label_word, "has_result": has_result, "has_stats": has_stats}
    annotated_labels = [
        f"{status_by_id[item.id]['tag']} {_display_time(item.kickoff_utc, '%d %b · %H:%M')} — {item.label}  ·  {status_by_id[item.id]['label']}"
        for item in matches
    ]
    by_annotated = dict(zip(annotated_labels, matches))
    st.caption("Marcas: ✅ completo · 🟡 falta marcador · 📊 falta estadísticas · 🔴 faltan ambos · ⏭️ aún por jugar")
    label = st.selectbox("Partido", annotated_labels)
    match = by_annotated[label]
    st.subheader("Cierre postpartido")
    existing_result = repo.get_match_result(match.id)
    evidence_status = all_statuses.get(match.id, repo.get_match_evidence_status(match.id))
    has_result = bool(evidence_status.get("has_result"))
    has_stats = bool(evidence_status.get("has_team_statistics") or evidence_status.get("deep_observations"))
    status_cols = st.columns(3)
    status_cols[0].metric("Estadísticas profundas", evidence_status["deep_observations"])
    status_cols[1].metric("Equipos con estadísticas", evidence_status["team_stat_rows"])
    status_cols[2].metric("Marcador final", "Guardado" if has_result else "Pendiente")
    if has_stats and has_result:
        callout(
            "Partido completo: estadísticas importadas y marcador guardado. "
            "Las predicciones compatibles ya se han evaluado y todo alimenta la forma de partidos posteriores.",
            tone="green", title="Partido completo",
        )
    elif has_stats and not has_result:
        callout(
            "Estadísticas importadas, pero falta el marcador final. "
            "Guárdalo abajo para evaluar las predicciones y calcular Brier.",
            tone="amber", title="Falta marcador",
        )
    elif has_result and not has_stats:
        callout(
            "Marcador guardado, pero faltan estadísticas profundas. "
            "Importa el JSON revisado o complétalas manualmente para enriquecer la forma de los próximos partidos.",
            tone="blue",
        )
    reviewed_batch_id = render_capture_review(
        repo,
        match,
        DATA_DIR / "evidence" / "screenshots",
    )
    existing_stats = repo.list_team_match_stats(match.id)
    deep_observations = repo.list_observations(match.id)
    # Show ALL deep observations, grouped by category. The JSON brings 70+
    # metrics per team and only ~8 are mirrored into the structured columns;
    # the rest live as observations and are exposed here so the analyst sees
    # the full picture.
    team_observations = [
        row for row in deep_observations
        if row.get("subject_type") == "team" and row.get("value_number") is not None
    ]
    if team_observations:
        from collections import defaultdict
        by_category: dict[str, list[dict]] = defaultdict(list)
        for row in team_observations:
            metric = str(row.get("metric") or "")
            category = metric.split(".")[0] if "." in metric else "otros"
            by_category[category].append(row)
        category_labels = {
            "resumen_del_partido": "Resumen del partido",
            "ataque": "Ataque",
            "defensa": "Defensa",
            "duelos": "Duelos",
            "pases": "Pases",
            "tiros": "Tiros",
            "porteria": "Portería",
            "otros": "Otros",
        }
        unique_metric_count = len({row["metric"] for row in team_observations})
        with st.expander(
            f"Ver todas las estadísticas profundas ({unique_metric_count} métricas por equipo)"
        ):
            st.caption(
                "Todas las métricas presentes en el JSON deep importado, una columna por equipo. "
                "Las métricas estructuradas se usan ya en el modelo; el resto "
                "alimenta auditoría y futuras extensiones."
            )
            for category in (
                "resumen_del_partido", "ataque", "defensa", "duelos",
                "pases", "tiros", "porteria", "otros",
            ):
                rows = by_category.get(category)
                if not rows:
                    continue
                # Pivot: metric → {team_name: value}
                pivot: dict[str, dict[str, float]] = {}
                for row in rows:
                    metric = str(row["metric"])
                    if "." in metric:
                        metric_short = metric.split(".", 1)[1]
                    else:
                        metric_short = metric
                    pivot.setdefault(metric_short, {})[str(row["subject_name"])] = float(row["value_number"])
                team_a_name = match.team_a.name
                team_b_name = match.team_b.name
                table_rows = []
                for metric_short, by_team in sorted(pivot.items()):
                    table_rows.append({
                        "Métrica": metric_short.replace("_", " "),
                        team_a_name: by_team.get(team_a_name),
                        team_b_name: by_team.get(team_b_name),
                    })
                st.markdown(f"**{category_labels.get(category, category)}** ({len(pivot)} métricas)")
                st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
    st.caption("La tabla siguiente contiene solo campos de equipo ausentes. Puedes añadir filas de jugador u otras métricas; las vacías no se guardan.")
    stats_by_team = {row["team_name"]: row for row in existing_stats}
    team_names = (match.team_a.name, match.team_b.name)
    settlement_rows = []
    for team_name in team_names:
        for metric in ("shots", "shots_on_target", "corners", "yellow_cards", "possession"):
            if stats_by_team.get(team_name, {}).get(metric) is None:
                settlement_rows.append({"subject_type": "team", "subject_name": team_name, "metric": metric, "value_number": None, "value_text": "", "unit": "match", "sample_size": 1})
    settlement_columns = ["subject_type", "subject_name", "metric", "value_number", "value_text", "unit", "sample_size"]
    with st.form(key=f"settlement_form_{match.id}", clear_on_submit=False):
        score_a, score_b = st.columns(2)
        goals_a = score_a.number_input(
            f"Goles · {match.team_a.name}", 0, 20,
            int(existing_result["goals_a"]) if existing_result else 0,
        )
        goals_b = score_b.number_input(
            f"Goles · {match.team_b.name}", 0, 20,
            int(existing_result["goals_b"]) if existing_result else 0,
        )
        settlement_stats = st.data_editor(
            pd.DataFrame(settlement_rows, columns=settlement_columns),
            hide_index=True, width="stretch", num_rows="dynamic",
            key=f"settlement_{match.id}",
        )
        st.caption(
            "Las tarjetas individuales se importan automáticamente desde el banco diario "
            "de jugadores (Actualizar datos) — ya no hace falta asignarlas a mano aquí. "
            "Si un jugador acumula 2 amarillas o ve una roja, la sanción para el siguiente "
            "partido se genera al pulsar Guardar."
        )
        submit_settlement = st.form_submit_button(
            "Guardar resultado, estadísticas y recalibrar", type="primary", width="stretch"
        )
    if submit_settlement:
        rows = [row for row in settlement_stats.to_dict("records") if pd.notna(row.get("value_number")) or str(row.get("value_text") or "").strip()]
        recorded_at = datetime.now(timezone.utc)
        repo.settle_match_versioned(
            match.id,
            int(goals_a),
            int(goals_b),
            reviewed_batch_id,
            recorded_at,
        )
        if rows:
            repo.save_manual_observations(match.id, rows, recorded_at)
        # Re-resolve the knockout bracket: a finished group game may now
        # complete a group → fill R32 slots; a finished knockout game lets
        # its winner bubble up into the next round's slot.
        try:
            resolve_knockout_bracket(repo, recorded_at)
        except Exception:
            pass
        try:
            created_suspensions = repo.auto_apply_discipline_suspensions(recorded_at)
        except Exception:
            created_suspensions = 0
        if created_suspensions:
            st.info(f"Sanciones automáticas generadas: {created_suspensions}.")
        st.success("Partido cerrado. Predicciones compatibles evaluadas y forma disponible para partidos posteriores.")
    predictions = repo.list_predictions(match.id)
    backtests = repo.list_backtests(match.id)
    c1, c2, c3 = st.columns(3)
    c1.metric("Snapshots", len(predictions))
    c2.metric("Evaluaciones", len(backtests))
    c3.metric("Brier medio", f"{sum(row['brier_score'] for row in backtests if row['brier_score'] is not None) / len(backtests):.3f}" if backtests else "—")
    if not predictions:
        empty_state("Sin snapshots", "Guarda un snapshot en Modelo antes de evaluar.", icon="📊")
        return
    options = {f"#{row['id']} · {row['market_name']} · {row['selection_name']} · p={row['probability']:.1%}": row for row in predictions}
    chosen_label = st.selectbox("Predicción guardada", list(options))
    chosen = options[chosen_label]
    occurred = st.checkbox("La selección ocurrió")
    if st.button("Guardar evaluación", type="primary"):
        score = brier_score(float(chosen["probability"]), occurred)
        repo.add_backtest(int(chosen["id"]), 1.0 if occurred else 0.0, score, occurred, datetime.now(timezone.utc))
        st.success(f"Evaluación guardada · Brier {score:.4f}")
        backtests = repo.list_backtests(match.id)
    if backtests:
        st.dataframe(_visible_frame(backtests), width="stretch", hide_index=True)
        bands = calibration_bands([(float(row["probability"]), bool(row["hit"])) for row in backtests], band_size=0.2)
        chart_rows = [{"Banda": key, "Predicha": value["avg_probability"], "Observada": value["hit_rate"], "N": value["count"]} for key, value in bands.items()]
        st.subheader("Calibración por banda")
        st.dataframe(pd.DataFrame(chart_rows), width="stretch", hide_index=True)
        family = summarize_by_market_family(backtests)
        st.subheader("Fiabilidad por familia")
        family_rows = [{"Familia": name, **values} for name, values in family.items()]
        st.dataframe(pd.DataFrame(family_rows), width="stretch", hide_index=True)
        st.caption("Menos de 20 evaluaciones por familia se etiqueta como provisional y no aumenta la confianza del modelo.")
        drift = calibration_drift(backtests)
        if drift:
            st.subheader("Deriva acumulada")
            st.line_chart(pd.DataFrame(drift).set_index("evaluated_at_utc")["cumulative_brier"])


def render_data_quality() -> None:
    hero("Control de evidencia", "Qué sabemos, qué falta y de cuándo es.", "La ausencia de un registro no se interpreta como una ausencia real; se marca como dato no disponible.")
    repo = _repo()
    repo.sync_source_catalog(default_source_catalog(), datetime.now(timezone.utc))
    st.subheader("Frescura de los bancos diarios")
    section_note("El estado de cada proveedor se actualiza automáticamente antes de la primera previsión del día.")
    freshness_rows = _freshness_rows_now()
    if freshness_rows:
        st.dataframe(pd.DataFrame(freshness_rows), width="stretch", hide_index=True)
    else:
        empty_state("Sin bancos registrados", "Los bancos diarios se comprobarán antes de la primera previsión.", icon="🗄️")
    matches = _list_matches()
    all_daily_players = repo.list_current_world_cup_players()
    db_sig = _db_signature()
    deep_counts = _deep_obs_counts_cached(db_sig)
    import_flags = _import_runs_cached(db_sig)
    players_by_team: dict[str, int] = {}
    for player in all_daily_players:
        tname = canonical_team_name(str(player.get("team_name") or ""))
        if tname:
            players_by_team[tname] = players_by_team.get(tname, 0) + 1
    rows = []
    for match in matches:
        bundle = _cached_bundle(match)
        match_player_count = sum(
            players_by_team.get(canonical_team_name(team), 0)
            for team in (match.team_a.name, match.team_b.name)
        )
        deep_count = deep_counts.get(match.id, 0)
        missing = list((bundle.missing_critical + bundle.missing_optional) if bundle else ["evento"])
        if match_player_count:
            missing = [value for value in missing if value != "players"]
        rows.append(
            {
                "Partido": match.label,
                "Fecha": _display_time(match.kickoff_utc, "%d/%m %H:%M"),
                "Cobertura": _coverage_status(bundle)[0],
                "Estadísticas": (len(bundle.statistics) if bundle else 0) + deep_count,
                "Jugadores disponibles": match_player_count,
                "Alineación": "Confirmada" if bundle and bundle.lineups else "No confirmada",
                "Última captura": _display_time(bundle.updated_at_utc, "%d/%m %H:%M") if bundle else "—",
                "Importado": "Sí" if import_flags.get(match.id) else "No",
                "Faltantes": ", ".join(missing) if missing else "Ninguno crítico",
            }
        )
    frame = pd.DataFrame(rows)
    coverage_filter = st.multiselect(
        "Filtrar cobertura",
        sorted(frame["Cobertura"].unique()),
        default=[],
        placeholder="Selecciona uno o varios estados",
    )
    if coverage_filter:
        frame = frame[frame["Cobertura"].isin(coverage_filter)]
    st.dataframe(frame, width="stretch", hide_index=True)
    st.subheader("Corrección manual trazable")
    labels, by_label = _match_labels(matches)
    selected_label = st.selectbox("Partido a corregir", labels)
    if selected_label not in by_label:
        st.info("Selecciona un partido de la lista (no un separador).")
        return
    selected = by_label[selected_label]
    existing = repo.list_observations(selected.id)
    editable_columns = ["subject_type", "subject_name", "metric", "value_number", "value_text", "unit", "sample_size"]
    manual_rows = [
        {column: row.get(column) for column in editable_columns}
        for row in existing
        if row.get("evidence_status") == "manual"
    ]
    if not manual_rows:
        manual_rows = [
            {"subject_type": "team", "subject_name": selected.team_a.name, "metric": "", "value_number": None, "value_text": "", "unit": "per_match", "sample_size": None},
            {"subject_type": "team", "subject_name": selected.team_b.name, "metric": "", "value_number": None, "value_text": "", "unit": "per_match", "sample_size": None},
        ]
    edited = st.data_editor(
        pd.DataFrame(manual_rows),
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        key=f"manual_observations_{selected.id}",
    )
    if st.button("Guardar correcciones manuales", width="stretch"):
        repo.save_manual_observations(
            selected.id,
            edited.to_dict("records"),
            datetime.now(timezone.utc),
        )
        st.success("Correcciones guardadas con fuente manual y marca temporal.")
    st.subheader("Sanciones, lesiones y cambios de entrenador")
    st.caption("Solo una incidencia nominal revisada afecta a la disponibilidad. Las tarjetas agregadas no identifican por sí solas al jugador.")
    c1, c2 = st.columns(2)
    context_team = c1.selectbox("Selección afectada", [selected.team_a.name, selected.team_b.name], key="context_team")
    event_label = c2.selectbox(
        "Tipo de incidencia",
        ["Sanción por roja", "Sanción por amarillas", "Lesión", "Enfermedad", "Cambio de entrenador"],
        key="context_event_type",
    )
    event_types = {
        "Sanción por roja": "suspension_red", "Sanción por amarillas": "suspension_yellows",
        "Lesión": "injury", "Enfermedad": "illness", "Cambio de entrenador": "coach_change",
    }
    player_name = st.text_input("Jugador (obligatorio salvo cambio de entrenador)", key="context_player_name")
    source_reference = st.text_input("Fuente o referencia revisada", key="context_source")
    incident_notes = st.text_area("Notas de la incidencia", key="context_notes")
    if st.button("Guardar incidencia de plantilla", key="save_context_event"):
        event_type = event_types[event_label]
        if event_type != "coach_change" and not player_name.strip():
            st.error("Debes identificar al jugador para aplicar una ausencia.")
        elif not source_reference.strip():
            st.error("Debes indicar una fuente o referencia revisada.")
        else:
            repo.save_squad_context_event({
                "team_name": context_team, "player_name": player_name.strip() or None,
                "event_type": event_type, "starts_at_utc": datetime.now(timezone.utc).isoformat(),
                "ends_at_utc": (selected.kickoff_utc + timedelta(hours=6)).isoformat(),
                "affected_match_id": selected.id,
                "source_id": f"manual-context-{sha256(source_reference.encode('utf-8')).hexdigest()[:16]}",
                "evidence_status": "reviewed",
                "notes": f"{incident_notes}\nFuente: {source_reference}".strip(),
            }, datetime.now(timezone.utc))
            st.success("Incidencia guardada y aplicable a la predicción de este partido.")
    st.subheader("Estado del proveedor local")
    provider_rows = [
        {"Componente": "sports-data SQLite", "Estado": "Disponible" if SPORTS_DB_PATH.exists() else "No disponible", "Ruta": str(SPORTS_DB_PATH)},
        {"Componente": "Collector analisis-de-datos", "Estado": "Se comprueba al actualizar", "Ruta": "CODEX_HOME/skills/analisis-de-datos"},
        {"Componente": "SofaScore URL", "Estado": "Experimental", "Ruta": "Sin cookies ni sesión"},
    ]
    st.dataframe(pd.DataFrame(provider_rows), width="stretch", hide_index=True)
    st.caption("Fuentes: SQLite local del collector, importaciones manuales y SofaScore experimental cuando el usuario lo solicita.")
    st.subheader("Bancos de información")
    bank_labels = {0: "Prioritario / autoridad", 1: "Primario abierto", 2: "Secundario API", 3: "Terciario / experimental"}
    catalog_rows = [
        {
            "Banco": bank_labels.get(int(row["bank"]), str(row["bank"])),
            "Fuente": row["label"],
            "Fiabilidad": f'{float(row["reliability"]):.0%}',
            "Coste": localize_cost_tier(row["cost_tier"]),
            "Consumo": localize_resource_tier(row["resource_tier"]),
            "Credencial": "Sí" if row["requires_credentials"] else "No",
            "Dominios": ", ".join(json.loads(row["domains_json"])),
            "Notas": row["notes"],
        }
        for row in repo.list_source_catalog()
    ]
    st.dataframe(pd.DataFrame(catalog_rows), width="stretch", hide_index=True)
    st.caption("El enrutador elige por dominio el banco disponible más alto; los desacuerdos del mismo banco se marcan como conflicto.")


def render_player_intelligence() -> None:
    hero(
        "Inteligencia de jugadores",
        "Rendimiento verificado, impacto interpretable y estilos comparables.",
        "Los clusters describen perfiles; no elevan por sí solos la confianza de una predicción.",
    )
    repo = _repo()
    # Manual refresh of the daily player bank. Useful right after a fixture
    # ends because the provider may publish updated minutes/goals minutes later.
    refresh_col, info_col = st.columns([1, 2.5])
    with refresh_col:
        refresh_players = st.button(
            "Actualizar datos de jugadores",
            key="refresh_players_intelligence",
            type="primary", width="stretch",
            help="Fuerza la recarga del banco diario (swaptr_wc2026_players), "
                 "bypaseando la ventana de 24h. Trae goles, asistencias, paradas y demás recientes.",
        )
    with info_col:
        st.caption(
            "Los rankings de abajo se calculan sobre el banco diario. "
            "Si falta un jugador o un gol reciente, usa el botón para forzar la recarga ahora."
        )
    if refresh_players:
        with st.spinner("Recargando banco de jugadores…"):
            try:
                refresh_result = _force_refresh_players(repo)
            except Exception as exc:
                st.error(f"No se pudo recargar el banco: {type(exc).__name__}: {exc}")
                refresh_result = None
        if refresh_result is not None:
            if refresh_result.updated:
                st.success(
                    f"Banco actualizado: {len(refresh_result.updated)} fuente(s) recibida(s)."
                )
            elif refresh_result.unchanged:
                st.info("Sin cambios: el proveedor no ha publicado nuevas estadísticas desde la última recarga.")
            if refresh_result.failed:
                st.warning(
                    "Proveedor con error: " + ", ".join(refresh_result.failed)
                    + ". Se conservan los datos cacheados."
                )
            st.cache_data.clear(); st.cache_resource.clear()
            st.rerun()
    minimum_minutes = st.slider(
        "Minutos mínimos (solo afecta a Impacto)", 0, 900, 60, 30,
        help="Los rankings de Goles/Asistencias/Tiros siempre muestran a quien tenga al menos uno, sin importar minutos.",
    )
    rows = _player_intelligence_rows_cached(_db_signature(), 0)
    if not rows:
        empty_state("Sin estadísticas verificadas", "Las capturas postpartido revisadas alimentarán esta vista.", icon="👤")
    else:
        frame = pd.DataFrame(rows)
        selected_ranking = st.segmented_control(
            "Ranking",
            ["Impacto", "Goles", "Asistencias", "Tiros"],
            default="Impacto",
            label_visibility="collapsed",
        )
        ranking_specs = {
            "Impacto": ("impact", "Impacto relativo", "impact", "Impacto", None, None),
            "Goles": ("goals_per90", "Goles / 90", "goals", "Goles", "goals_per90", "Goles/90"),
            "Asistencias": ("assists_per90", "Asistencias / 90", "assists", "Asistencias", "assists_per90", "Asist./90"),
            "Tiros": ("shots_per90", "Tiros / 90", "shots", "Tiros", "shots_per90", "Tiros/90"),
        }
        metric, title, total_col, total_label, rate_col, rate_label = ranking_specs[selected_ranking]
        _render_player_panel(
            frame, metric, title, total_col, total_label, rate_col, rate_label,
            minimum_minutes=int(minimum_minutes),
        )
        if "passes_per90" not in frame:
            callout("Pases: sin cobertura en el banco diario actual. Se conserva como dato desconocido y no como 0; aparecerá cuando una fuente revisada lo aporte.")
        st.caption("Impacto estandariza solo métricas realmente disponibles. Todas las clasificaciones muestran minutos y partidos para contextualizar la muestra.")
