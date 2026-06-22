from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hashlib import sha256
import json
import os
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

from wcpredict.backtesting import brier_score, calibration_bands, summarize_by_market_family, calibration_drift
from wcpredict.collector_store import CollectorEventBundle, CollectorStore
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
from wcpredict.ratings import build_team_ratings, explain_team_form
from wcpredict.refresh import refresh_match
from wcpredict.repository import Repository
from wcpredict.schedule import seed_schedule
from wcpredict.services import MarketPrediction, predict_match_markets
from wcpredict.sofascore import import_sofascore_event
from wcpredict.sentiment import normalize_sentiment_snapshot, x_collection_gate
from wcpredict.source_catalog import default_source_catalog
from wcpredict.daily_refresh import DEFAULT_PROVIDERS, DatasetDownload, ensure_current_world_cup_data
from wcpredict.world_cup_data import fetch_kaggle_world_cup_dataset, import_world_cup_download
from wcpredict.prediction_report import build_prediction_report
from wcpredict.ai_copilot import explain_context
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
    model_disagreement_note,
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
WORKSPACE_ROOT = ROOT.parent
SPORTS_DATA_DIR = WORKSPACE_ROOT / "sports-data"
SPORTS_DB_PATH = SPORTS_DATA_DIR / "sports.db"
OUTCOME_MODEL_PATH = DATA_DIR / "models" / "outcome_ml.joblib"
DEEP_OUTCOME_MODEL_PATH = DATA_DIR / "models" / "outcome_ml_deep.joblib"
OPEN_SCHEDULE_PATH = DATA_DIR / "open" / "martj42-results.csv"
DAILY_PROVIDERS = (*DEFAULT_PROVIDERS, "martj42_world_schedule")
HOST_TEAMS = {"USA", "Canada", "Mexico"}


def _host_factor(team_name: str) -> float:
    return 1.10 if canonical_team_name(team_name) in HOST_TEAMS else 1.0


def _team_strengths(results, as_of_date) -> dict[str, dict[str, float]]:
    return {
        team_name: {"attack": rating.attack, "defense": rating.defense}
        for team_name, rating in build_team_ratings(results, as_of_date).items()
    }


@st.cache_resource(show_spinner=False)
def _repo() -> Repository:
    repo = Repository(DATABASE_PATH)
    repo.initialize()
    if SCHEDULE_PATH.exists():
        seed_schedule(repo, SCHEDULE_PATH)
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
    profiles = build_player_profiles(
        repo.list_player_performance_rows(), min_minutes=minimum_minutes
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
) -> None:
    """Render one ranking panel inside the Jugadores tab.

    Wrapped in ``st.fragment`` so that the search input and sort radio
    inside this panel only re-run THIS panel's body on interaction, not the
    whole player-intelligence view (which had to rebuild every other tab's
    HTML on every keystroke — the source of the lag the user reported).
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


def _match_labels(matches) -> tuple[list[str], dict[str, object]]:
    labels = [
        f"{match.kickoff_utc.astimezone().strftime('%d %b · %H:%M')} — {match.label}"
        for match in matches
    ]
    return labels, dict(zip(labels, matches))


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
        + status_pill(f"Actualizado {bundle.updated_at_utc.astimezone().strftime('%d/%m %H:%M')}")
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
    expected_xg: tuple = field(default_factory=tuple)
    goalkeeper_baselines: dict = field(default_factory=dict)
    corrections: object = None


@st.cache_resource(show_spinner=False)
def _match_analysis_bundle_cached(
    match_id: int,
    db_sig: tuple[int, int],
    sports_db_sig: tuple[int, int],
    model_sig: tuple[int, int] | None,
    apply_corrections: bool = False,
) -> MatchAnalysisBundle:
    repo = _repo()
    match = next(item for item in repo.list_matches() if item.id == match_id)
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
    historical_results = repo.list_historical_results_before(match.kickoff_utc)
    local_results = repo.list_match_results_before(match.kickoff_utc)
    keyed_results = {
        (row.played_on, row.team_a, row.team_b): row
        for row in historical_results + collector_results + local_results
    }
    results = list(keyed_results.values())

    calibration_summary = _calibration_summary_cached(db_sig)
    strength_context = _team_strengths(results, match.kickoff_utc.date())
    xg_form = build_xg_form_adjustment(
        team_a, team_b, deep_rows_before, match.kickoff_utc,
        team_strengths=strength_context,
    )

    # Layer on top: a richer factor derived from the full deep-stat profile
    # (offense / defense / goalkeeper dimensions). The simple xg_form above
    # only uses ~9 metrics; this brings in the remaining 60+ but keeps the
    # multiplier bounded so it complements rather than replaces the base.
    from wcpredict.team_profile import build_team_profile
    from wcpredict.team_volume_markets import derive_xg_factors_from_profile
    from wcpredict.advanced_form import XgFormAdjustment
    deep_obs_for_profile = repo.list_deep_team_metric_observations_before(match.kickoff_utc)
    # Opponent strength = (attack + defense) average per team, derived from the
    # Elo-style ratings. Used so metrics produced against strong sides count
    # for more than the same numbers against weak ones.
    opponent_strengths = {
        name: (rating.attack + rating.defense) / 2
        for name, rating in build_team_ratings(results, as_of=match.kickoff_utc.date()).items()
    }
    profile_a_xg = build_team_profile(
        team_a, deep_obs_for_profile, match.kickoff_utc,
        opponent_strengths=opponent_strengths,
    )
    profile_b_xg = build_team_profile(
        team_b, deep_obs_for_profile, match.kickoff_utc,
        opponent_strengths=opponent_strengths,
    )
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
    historical_rows = repo.list_historical_rows_before(match.kickoff_utc)
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
    deep_model_sig = _file_signature(DEEP_OUTCOME_MODEL_PATH)
    deep_ml_model = _load_deep_outcome_model_cached(str(DEEP_OUTCOME_MODEL_PATH), deep_model_sig)
    deep_ml_probabilities = None
    deep_weight = 0.0
    if (
        deep_ml_model is not None
        and getattr(deep_ml_model, "status", "") == "ready"
        and ml_features is not None
        and profile_a_xg.sample_weight >= 3
        and profile_b_xg.sample_weight >= 3
    ):
        from wcpredict.outcome_ml_deep import build_deep_features
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
    )
    score_only_predictions = predict_match_markets(
        team_a, team_b, results, match.kickoff_utc.date(), calibration_summary,
        player_context=current_players or None,
        advanced_form=xg_form,
        host_factor_a=host_factor_a,
        host_factor_b=host_factor_b,
        corrections=corrections,
    )
    primary = [row for row in predictions if row.market_name == "1X2"]
    exact_score = next(row for row in predictions if row.market_name == "Exact Score")

    # Post-match audit support: final score, team stats, evaluated bets, and
    # the model's volume-market expectations for this fixture (so the audit can
    # compare predicted vs observed corners/cards/shots without recomputing).
    match_result = repo.get_match_result(match.id)
    team_match_stats = repo.list_team_match_stats(match.id)
    backtests = repo.list_backtests(match.id)
    volume_predictions: dict[str, float] = {}
    team_volume_predictions: dict[str, dict[str, float]] = {}
    rate_observations = observations_for_match + build_volume_rate_observations(
        team_a, team_b, repo.list_deep_volume_rows_before(match.kickoff_utc)
    )
    for metric in ("corners", "cards", "shots", "shots_on_target"):
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
            team_a, team_b, rate_observations, metric, 0.5, dispersion=dispersion
        )
        if estimate.expected_total is not None:
            volume_predictions[metric] = float(estimate.expected_total)
        if estimate.expected_team_a is not None and estimate.expected_team_b is not None:
            team_volume_predictions[metric] = {
                team_a: float(estimate.expected_team_a),
                team_b: float(estimate.expected_team_b),
            }
    # Goalkeeper baseline per team from deep stats (saves vs SOT against).
    goalkeeper_rows = repo.list_deep_goalkeeper_rows_before(match.kickoff_utc)
    goalkeeper_baselines = {
        team_a: build_goalkeeper_baseline(team_a, goalkeeper_rows, match.kickoff_utc),
        team_b: build_goalkeeper_baseline(team_b, goalkeeper_rows, match.kickoff_utc),
    }
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

    return MatchAnalysisBundle(
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
        match_result=dict(match_result) if match_result else None,
        team_match_stats=team_match_stats,
        backtests=backtests,
        volume_predictions=volume_predictions,
        team_volume_predictions=team_volume_predictions,
        expected_xg=expected_xg,
        goalkeeper_baselines=goalkeeper_baselines,
        corrections=corrections,
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


def _render_post_match_audit(bundle: MatchAnalysisBundle, team_a: str, team_b: str) -> None:
    result = bundle.match_result
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
    stats_by_team = {row["team_name"]: row for row in bundle.team_match_stats}
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
        float(row["brier_score"]) for row in bundle.backtests
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
        predicted_volume=bundle.volume_predictions,
        brier_average=brier_average,
        evaluations=len(bundle.backtests),
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
        help=f"Promedio de {len(bundle.backtests)} apuestas evaluadas",
    )
    metric_cols[2].metric(
        "Estadísticas observadas",
        len(bundle.team_match_stats),
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
        team_volume_predictions=bundle.team_volume_predictions,
        team_a_stats=dict(bundle.team_match_stats[0]) if bundle.team_match_stats and bundle.team_match_stats[0]["team_name"] == team_a else next(
            (dict(row) for row in bundle.team_match_stats if row["team_name"] == team_a), None,
        ),
        team_b_stats=next(
            (dict(row) for row in bundle.team_match_stats if row["team_name"] == team_b), None,
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
        apply_corrections=_corrections_enabled(),
    )


def render_dashboard() -> None:
    repo = _repo()
    with st.spinner("Actualizando el calendario diario del Mundial…"):
        daily_result = _refresh_current_world_cup_banks(repo)
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
    window_end = now + timedelta(days=2)
    focus = [
        match for match in _list_matches()
        if now.date() <= match.kickoff_utc.date() <= window_end.date()
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
                bundle.updated_at_utc.astimezone().strftime("%d/%m %H:%M")
                if bundle else "—"
            )
            rows_html.append(
                "<tr>"
                f'<td style="padding:10px 12px;color:var(--muted);white-space:nowrap;">'
                f'{match.kickoff_utc.astimezone().strftime("%d/%m · %H:%M")}</td>'
                f'<td style="padding:10px 12px;color:var(--ink);font-weight:600;">'
                f'<span class="match-team">{crest_html(match.team_a.name, size=20)}'
                f'<span>{match.team_a.name}</span></span> '
                f'<span style="color:var(--muted);font-weight:500;margin:0 6px;">vs</span> '
                f'<span class="match-team">{crest_html(match.team_b.name, size=20)}'
                f'<span>{match.team_b.name}</span></span></td>'
                f'<td style="padding:10px 12px;color:var(--muted);">{match.venue or "—"}</td>'
                f'<td style="padding:10px 12px;">{status_pill(coverage_label, coverage_tone)}</td>'
                f'<td style="padding:10px 12px;color:var(--muted);white-space:nowrap;'
                f'font-feature-settings:\'tnum\' 1,\'lnum\' 1;">{updated_label}</td>'
                "</tr>"
            )
        st.markdown(
            '<div class="soft-panel" style="padding:0;overflow:hidden;">'
            '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
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

    st.write("")
    left, right = st.columns([1.4, 1])
    with left:
        st.subheader("Ruta de lectura")
        st.markdown(
            "1. **Actualiza el partido** y revisa qué datos existen.  \n"
            "2. **Lee probabilidades y rango**, no solo el punto central.  \n"
            "3. **Introduce tus cuotas** para comparar precio justo y EV.  \n"
            "4. **Guarda el snapshot** antes del partido y evalúalo después."
        )
    with right:
        st.subheader("Regla de honestidad")
        callout(
            "Si faltan datos de equipo, jugador o alineación, el mercado baja de "
            "confianza o queda como <strong>no estimable</strong>. La ausencia de "
            "un registro nunca se interpreta como 0.",
            tone="blue",
        )


def render_prediction_lab() -> None:
    repo = _repo()
    with st.spinner("Comprobando calendario y bancos diarios del Mundial…"):
        daily_result = _refresh_current_world_cup_banks(repo)
    matches = _list_matches()
    if not matches:
        empty_state("Sin partidos", "No hay partidos cargados en el calendario.", icon="📅")
        return
    labels, by_label = _match_labels(matches)
    calibration_labels = {
        "Czechia vs South Africa", "Switzerland vs Bosnia and Herzegovina",
        "Canada vs Qatar", "Mexico vs South Korea",
    }
    default_index = next(
        (index for index, label in enumerate(labels) if by_label[label].label in calibration_labels),
        0,
    )
    selected_label = st.selectbox("Partido", labels, index=default_index, label_visibility="collapsed")
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
        f"{match.stage} · {match.kickoff_utc.astimezone().strftime('%d %b %Y · %H:%M')}",
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
    button_col, note_col = st.columns([1, 2.2])
    with button_col:
        refresh_clicked = st.button("Actualizar datos", type="primary", width="stretch")
    with note_col:
        st.caption("Consulta acotada: un partido, máximo 14 llamadas y 0 créditos de cuotas. Conserva la caché si falla.")
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
    elif deep_count or current_players or prior_deep_samples:
        callout(
            f"Evidencia de modelo disponible: {prior_deep_samples} partidos profundos previos relacionados, "
            f"{deep_count} estadísticas revisadas de este partido y {len(current_players)} jugadores del banco diario. "
            "Falta solo la caché automática del collector para este evento concreto.",
            tone="green",
        )
    else:
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
    top_left, top_right = st.columns([1.55, 1])
    with top_left:
        st.subheader("Probabilidad 1X2")
        section_note("Este valor integra el historial (80%) y la matriz de goles (20%).")
        home_p = next((row.probability for row in primary if row.selection_name == team_a), 0)
        draw_p = next((row.probability for row in primary if row.selection_name == "Draw"), 0)
        away_p = next((row.probability for row in primary if row.selection_name == team_b), 0)
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
        st.caption(short_explanation)
        with st.expander("Ver cálculo y jugadores usados"):
            st.caption(best.explanation)
        if best.confidence.value == "low":
            st.warning("Confianza baja: la base observada para estos equipos aún es insuficiente.")

    tab_predictions, tab_odds, tab_players, tab_data, tab_saved = st.tabs(
        ["Modelo", "Mercados y EV", "Jugadores", "Datos / SofaScore", "Guardado"]
    )
    with tab_predictions:
        _render_post_match_audit(bundle, team_a, team_b)
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
            
            # New diagnostic view with 3 columns
            comparison_rows = model_comparison_rows(
                team_a, team_b, score_probabilities, ml_probabilities, unified_probabilities
            )
            st.subheader("Diagnóstico de señales")
            st.caption("La columna 'Modelo Unificado' es la que se usa para las predicciones y el EV. Las otras sirven para entender la discrepancia.")
            st.dataframe(
                pd.DataFrame(comparison_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "Modelo unificado 1X2 (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "ML cronológico (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "Matriz de marcadores (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                    "Diferencia (pp)": st.column_config.NumberColumn(format="%+.1f"),
                },
            )
            callout(model_disagreement_note(comparison_rows))
            st.caption(
                "ML cronológico: diferencia Elo "
                f"{ml_features['rating_diff']:.3f}; "
                "forma reciente y diferencia de goles de los cinco últimos partidos, incluyendo resultados locales ya cerrados del Mundial. "
                "La estadística profunda ajusta el modelo de marcadores."
            )
            st.caption(f"ML entrenado con {ml_model_meta['sample_size']} partidos · corte {ml_model_meta['training_cutoff_utc']} · validación temporal hasta {ml_model_meta['validation_cutoff_utc']}.")
        else:
            callout("Modelo ML no activado: ejecuta scripts/import_open_history.py para crear el artefacto calibrado.")
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
        observations = repo.list_observations(match.id) + build_volume_rate_observations(
            team_a, team_b, repo.list_deep_volume_rows_before(match.kickoff_utc)
        )
        st.subheader("Mercados de volumen")
        volume_rows = []
        for metric, line in (("corners", 8.5), ("cards", 3.5), ("shots", 21.5), ("shots_on_target", 8.5)):
            dispersion_row = next(
                (row for row in observations if row.get("metric") == f"{metric}_dispersion"),
                None,
            )
            dispersion = (
                float(dispersion_row["value_number"])
                if dispersion_row and dispersion_row.get("value_number") is not None
                else None
            )
            estimate = estimate_total_market(
                team_a, team_b, observations, metric, line, dispersion=dispersion
            )
            volume_rows.append(
                {"Mercado": localize_metric(metric), "Línea": line, "Modelo": localize_model(estimate.model_family), "Esperado": estimate.expected_total, "Probabilidad de más": estimate.over_probability, "Rango bajo": estimate.low_probability, "Rango alto": estimate.high_probability, "Confianza": estimate.confidence, "Muestra": estimate.sample_size, "Explicación": estimate.explanation}
            )
        st.dataframe(pd.DataFrame(volume_rows), width="stretch", hide_index=True)

        # Per-team predictions derived from the full deep-stat profile (all 70+
        # offensive/defensive/goalkeeper/style metrics, recency-weighted and
        # shrunk toward tournament means). This gives over/under per team for
        # corners, yellow cards, shots, etc. — markets the global "total" view
        # cannot resolve at the team level.
        from wcpredict.team_profile import build_team_profile
        from wcpredict.team_volume_markets import predict_team_volume_markets, MARKET_CATALOG
        deep_obs = repo.list_deep_team_metric_observations_before(match.kickoff_utc)
        # Opponent strengths for the team_profile reweighting. Same shape used
        # earlier in the xG factor block (helper `_team_strengths` returns
        # attack/defense per team; we collapse to a single index here).
        team_strengths_for_profile = {
            name: (rating.attack + rating.defense) / 2
            for name, rating in build_team_ratings(results, as_of=match.kickoff_utc.date()).items()
        }
        profile_a = build_team_profile(
            team_a, deep_obs, match.kickoff_utc,
            opponent_strengths=team_strengths_for_profile,
        )
        profile_b = build_team_profile(
            team_b, deep_obs, match.kickoff_utc,
            opponent_strengths=team_strengths_for_profile,
        )
        team_lines = predict_team_volume_markets(profile_a, profile_b)
        if team_lines:
            st.subheader("Estadísticas estimadas por equipo")
            st.caption(
                "Valor esperado por partido para cada métrica, derivado del perfil "
                "deep (45% propio + 30% rival + 25% media del torneo). Las líneas "
                "over/under aparecen en la pestaña Mercados y EV cuando hay cuotas."
            )
            # Collapse the per-line rows into a single per-(team, market) row
            # showing only the expected value. Confidence/sample come along
            # so the user knows how trustworthy each number is.
            expected_by_team_metric: dict[tuple[str, str], dict] = {}
            for row in team_lines:
                key = (row.team_name, row.market)
                if key in expected_by_team_metric:
                    continue
                expected_by_team_metric[key] = {
                    "team": row.team_name,
                    "market": row.market,
                    "label": row.label,
                    "expected": row.expected,
                    "confidence": row.confidence,
                    "sample": row.sample_size,
                }
            # Pivot: rows = metric, columns = [team_a, team_b], values = expected.
            market_order = [m for m in MARKET_CATALOG.keys()]
            stat_rows = []
            for market_id in market_order:
                label = MARKET_CATALOG[market_id]["label"]
                a = expected_by_team_metric.get((team_a, market_id))
                b = expected_by_team_metric.get((team_b, market_id))
                if not a and not b:
                    continue
                stat_rows.append({
                    "Estadística": label,
                    team_a: round(a["expected"], 2) if a else None,
                    team_b: round(b["expected"], 2) if b else None,
                    "Confianza": (a or b)["confidence"],
                    "Muestra": round((a or b)["sample"], 1),
                })
            st.dataframe(
                pd.DataFrame(stat_rows),
                width="stretch",
                hide_index=True,
            )
        if st.button("Guardar snapshot de predicciones", width="stretch"):
            now = datetime.now(timezone.utc)
            for row in predictions:
                repo.add_prediction(match.id, row.market_family.value, row.market_name, row.selection_name, row.line, row.probability, row.confidence.value, now, row.explanation)
            st.success(f"Snapshot guardado: {len(predictions)} mercados.")

        st.subheader("Informe estructurado")
        form_notes = []
        for team_name in (team_a, team_b):
            ledger = explain_team_form(team_name, results, match.kickoff_utc.date())[-5:]
            form_notes.extend(f"{team_name}: {item.explanation}" for item in ledger)
        player_notes = [
            f"{row['player_name']} ({row['team_name']}): {row.get('minutes') or 0} min, "
            f"{row.get('goals') or 0} goles, {row.get('assists') or 0} asistencias"
            for row in sorted(
                current_players,
                key=lambda item: int(item.get("minutes") or 0),
                reverse=True,
            )[:12]
        ]
        freshness = _freshness_rows_now()
        report = build_prediction_report(
            team_a=team_a,
            team_b=team_b,
            probabilities={localize_selection(row.selection_name): row.probability for row in primary},
            form_notes=form_notes,
            player_notes=player_notes,
            context_notes=[
                f"Sede: {match.venue or 'por confirmar'}",
                "Campo neutral" if match.neutral_site else "Ventaja de localía aplicable",
            ] + squad_notes,
            sources=[
                {"label": row["Proveedor"], "status": row["Estado"], "updated_at": row["Datos actualizados"]}
                for row in freshness
            ],
            model={"active": "unified_1x2_blend", "challenger": "score_matrix"},
            missing_data=([] if current_players else ["Sin estadísticas diarias de jugadores para estas selecciones"])
            + (["Algún banco diario no pudo actualizarse"] if daily_result.failed else []),
        )
        st.markdown(report)
        if st.button("Generar explicación contextual opcional con OpenAI", key=f"ai_context_{match.id}"):
            copilot = explain_context(
                {
                    "match": match.label,
                    "probabilities": {localize_selection(row.selection_name): row.probability for row in primary},
                    "form": form_notes,
                    "players": player_notes,
                    "missing_data": ["daily_provider_failure"] if daily_result.failed else [],
                }
            )
            if copilot.status == "ready":
                st.info(copilot.narrative)
            elif copilot.status == "disabled":
                st.caption("OpenAI no configurado. El informe determinista sigue disponible sin coste de API.")
            else:
                st.warning(f"No se pudo generar la explicación opcional: {copilot.reason}")

    with tab_odds:
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
                comparisons.append(compare_odds_to_probability(model.probability, row["decimal_odds"], model.market_family, model.market_name, model.selection_name, model.confidence.value))
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

    with tab_players:
        lineups = repo.list_imported_lineups(match.id)
        if lineups:
            st.success("Alineación importada para este partido.")
            with st.expander("Ver alineación"):
                st.dataframe(_visible_frame(lineups), width="stretch", hide_index=True)
        else:
            st.info("Alineación no confirmada: las tasas observadas sí están disponibles, pero la confianza se mantiene baja.")
        st.caption("Elige jugador, mercado, línea y cuota. La tasa por 90, los minutos y la titularidad se calculan desde el banco de jugadores.")
        team_tabs = st.tabs([team_a, team_b])
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
        sot_predictions = bundle.team_volume_predictions.get("shots_on_target", {})
        opponent_sot_for = {
            team_a: sot_predictions.get(team_b),
            team_b: sot_predictions.get(team_a),
        }
        for team_name, team_panel in zip((team_a, team_b), team_tabs):
            with team_panel:
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
                            "Atajadas": row.get("tackles_won") or 0,
                            "Intercepciones": row.get("interceptions") or 0,
                            "Pases": int(row.get("passes") or 0),
                            "Amarillas": int(row.get("yellow_cards") or 0),
                        })
                    else:
                        base.update({
                            "Goles": int(row.get("goals") or 0),
                            "Asist.": int(row.get("assists") or 0),
                            "Tiros": int(row.get("shots") or 0),
                            "SOT": int(row.get("shots_on_target") or 0),
                            "Amarillas": int(row.get("yellow_cards") or 0),
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
                    baseline = bundle.goalkeeper_baselines.get(team_name)
                    bank_save_pct = (float(player_row.get("save_percentage") or 0)) / 100.0
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

    with tab_data:
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
                imported_deep = repo.import_deep_match_collection(collection, datetime.now(timezone.utc))
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
                            "Kickoff": item.kickoff_utc.astimezone().strftime("%d/%m %H:%M"),
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
        st.divider()
        st.subheader("Importador experimental de SofaScore")
        st.caption("Pega una URL pública de partido. Se muestra una vista previa; no usa cookies, sesión ni credenciales del navegador.")
        sofa_url = st.text_input("URL de SofaScore", key=f"sofa_{match.id}")
        preview_key = f"sofa_preview_{match.id}"
        if st.button("Obtener vista previa", disabled=not bool(sofa_url), width="stretch"):
            try:
                imported = import_sofascore_event(sofa_url)
            except Exception as exc:
                st.error(f"SofaScore no pudo importarse: {type(exc).__name__}. La caché local sigue intacta.")
            else:
                st.session_state[preview_key] = imported
        imported = st.session_state.get(preview_key)
        if imported is not None:
            if imported.status == "incomplete":
                st.warning("Importación parcial: " + ", ".join(imported.missing))
            st.write(f"**{imported.team_a} vs {imported.team_b}** · evento {imported.event_id}")
            if imported.statistics:
                st.dataframe(_visible_frame(imported.statistics), width="stretch", hide_index=True)
            if imported.players:
                st.dataframe(_visible_frame(imported.players), width="stretch", hide_index=True)
            if st.button("Persistir vista previa con procedencia", width="stretch"):
                repo.import_sofascore_preview(match.id, imported, datetime.now(timezone.utc))
                st.success("Datos de SofaScore persistidos; revisa Calidad de datos para corregirlos o completarlos.")

    with tab_saved:
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
            "Kickoff": s.kickoff_utc.astimezone().strftime("%d/%m %H:%M"),
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
        f"{status_by_id[item.id]['tag']} {item.kickoff_utc.astimezone().strftime('%d %b · %H:%M')} — {item.label}  ·  {status_by_id[item.id]['label']}"
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
    if existing_stats:
        visible_stats = [
            {
                "Selección": row["team_name"], "xG": row["xg"], "Tiros": row["shots"],
                "Tiros a puerta": row["shots_on_target"], "Posesión": row["possession"],
                "Córners": row["corners"], "Amarillas": row["yellow_cards"],
                "Rojas": row["red_cards"], "Paradas": row["saves"], "Fuente": row["source_id"],
            }
            for row in existing_stats
        ]
        with st.expander("Resumen estructurado (team_match_stats)"):
            st.dataframe(pd.DataFrame(visible_stats), width="stretch", hide_index=True)
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
                "Las que aparecen en el resumen estructurado se usan ya en el modelo; el resto "
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
    settlement_rows = []
    for team_name in (match.team_a.name, match.team_b.name):
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
                "Fecha": match.kickoff_utc.astimezone().strftime("%d/%m %H:%M"),
                "Cobertura": _coverage_status(bundle)[0],
                "Estadísticas": (len(bundle.statistics) if bundle else 0) + deep_count,
                "Jugadores disponibles": match_player_count,
                "Alineación": "Confirmada" if bundle and bundle.lineups else "No confirmada",
                "Última captura": bundle.updated_at_utc.astimezone().strftime("%d/%m %H:%M") if bundle else "—",
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
    performance_tab, sentiment_tab = st.tabs(["Rendimiento y estilos", "Sentimiento prepartido"])
    with performance_tab:
        minimum_minutes = st.slider("Minutos mínimos", 0, 900, 60, 30)
        rows = _player_intelligence_rows_cached(_db_signature(), int(minimum_minutes))
        if not rows:
            empty_state("Sin estadísticas verificadas", "Las capturas postpartido revisadas alimentarán esta vista.", icon="👤")
        else:
            frame = pd.DataFrame(rows)
            ranking_tabs = st.tabs(["Impacto", "Goles", "Asistencias", "Tiros"])
            ranking_specs = [
                ("impact", "Impacto relativo", "impact", "Impacto", None, None),
                ("goals_per90", "Goles / 90", "goals", "Goles", "goals_per90", "Goles/90"),
                ("assists_per90", "Asistencias / 90", "assists", "Asistencias", "assists_per90", "Asist./90"),
                ("shots_per90", "Tiros / 90", "shots", "Tiros", "shots_per90", "Tiros/90"),
            ]
            for panel, (metric, title, total_col, total_label, rate_col, rate_label) in zip(ranking_tabs, ranking_specs):
                with panel:
                    _render_player_panel(
                        frame, metric, title, total_col, total_label, rate_col, rate_label,
                    )
            if "passes_per90" not in frame:
                callout("Pases: sin cobertura en el banco diario actual. Se conserva como dato desconocido y no como 0; aparecerá cuando una fuente revisada lo aporte.")
            st.caption("Impacto estandariza solo métricas realmente disponibles. Todas las clasificaciones muestran minutos y partidos para contextualizar la muestra.")
    with sentiment_tab:
        callout("Señal experimental: se muestra como contexto y queda excluida del modelo y de la calibración.", tone="amber")
        gate = x_collection_gate(os.getenv("X_API_BEARER_TOKEN"), float(os.getenv("X_API_BUDGET_USD", "0") or 0))
        st.write(f"Estado del conector X: **{localize_status(gate.status)}** — {gate.detail}")
        st.caption("La aplicación no inicia streaming ni gasta créditos sin clave y presupuesto positivo.")
        matches = _list_matches()
        labels, by_label = _match_labels(matches)
        selected = by_label[st.selectbox("Partido", labels, key="sentiment_match")]
        existing = repo.list_sentiment_snapshots(selected.id)
        if existing:
            st.dataframe(_visible_frame(existing), width="stretch", hide_index=True)
        with st.expander("Registrar snapshot prepartido"):
            query = st.text_input("Consulta utilizada", f'"{selected.team_a.name}" OR "{selected.team_b.name}"')
            language = st.text_input("Idioma", "es")
            hours = st.number_input("Ventana anterior (horas)", min_value=1, max_value=168, value=24)
            c1, c2, c3 = st.columns(3)
            positive = c1.number_input("Positivos", min_value=0, value=0)
            neutral = c2.number_input("Neutros", min_value=0, value=0)
            negative = c3.number_input("Negativos", min_value=0, value=0)
            estimated_cost = st.number_input("Coste estimado (USD)", min_value=0.0, value=0.0, step=0.01)
            if st.button("Guardar snapshot experimental"):
                end = min(datetime.now(timezone.utc), selected.kickoff_utc)
                start = end - pd.Timedelta(hours=int(hours))
                snapshot = normalize_sentiment_snapshot(
                    match_id=selected.id, provider_id="x_api", window_start_utc=start.to_pydatetime() if hasattr(start, "to_pydatetime") else start,
                    window_end_utc=end, positive=int(positive), neutral=int(neutral), negative=int(negative),
                    query=query, language=language, estimated_cost_usd=float(estimated_cost),
                )
                repo.save_sentiment_snapshot(snapshot, datetime.now(timezone.utc))
                st.success("Snapshot guardado como evidencia experimental; no modifica las probabilidades.")
