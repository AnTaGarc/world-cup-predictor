from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from dataclasses import replace
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
from wcpredict.models import MarketFamily
from wcpredict.outcome_ml import current_match_features, load_outcome_model, match_results_to_feature_rows
from wcpredict.player_projections import (
    GOALKEEPER_MARKETS,
    PLAYER_MARKET_METRICS,
    derive_player_assumption,
    estimate_player_projection,
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
from wcpredict.penalty_history_model import PENALTY_MODEL_VERSION, build_penalty_match_context
from wcpredict.penalty_context_cache import (
    load_precomputed_context,
    repository_penalty_input_fingerprint,
)
from wcpredict.extra_time_model import adjust_extra_time_xg
from wcpredict.knockout_audit import (
    build_knockout_snapshot_section,
    evaluate_knockout_snapshot,
)
from wcpredict.services import MarketPrediction, predict_match_markets
from wcpredict.source_catalog import default_source_catalog
from wcpredict.daily_refresh import DEFAULT_PROVIDERS, DatasetDownload, ensure_current_world_cup_data
from wcpredict.world_cup_data import (
    build_daily_fetcher,
    build_daily_importer,
    fetch_kaggle_world_cup_dataset,
    import_world_cup_download,
)
from wcpredict.advanced_form import (
    build_goalkeeper_baseline,
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
from wcpredict.ui.knockout_settlement import render_knockout_settlement
from wcpredict.ui.bracket import bracket_result_display, render_bracket
from wcpredict.ui.crests import crest_html, team_with_crest_html
from wcpredict.ui.theme import (
    callout,
    empty_state,
    hero,
    knockout_advance_html,
    knockout_badge_html,
    probability_bar,
    section_note,
    status_pill,
)
from wcpredict.ui.translations import (
    localize_confidence,
    localize_cost_tier,
    localize_market,
    localize_market_family,
    localize_metric,
    localize_model,
    localize_resource_tier,
    localize_selection,
    localize_status,
    localize_table_columns,
    localize_team_name,
)
from wcpredict.ui.view_models import (
    coverage_summary,
    dataset_freshness_rows,
    model_comparison_rows,
    model_policy_rows,
    postmatch_queue_message,
    prediction_rows,
    probability_chart_rows,
)
from wcpredict.ui.interaction_models import prepare_player_match_context
from wcpredict.ui.i18n import (
    localize_controlled as localize_controlled_i18n,
    localize_table_columns as localize_table_columns_i18n,
    translate,
)
from wcpredict.ui.language_preference import current_language


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


def _lang():
    return current_language()


def _t(key: str, *, count: int | None = None, **values: object) -> str:
    return translate(key, language=_lang(), count=count, **values)


def _b(es: str, en: str) -> str:
    """Contextual paired copy for highly local, workflow-specific messages."""
    return es if _lang() == "es" else en


def _localized_match_label(match) -> str:
    if hasattr(match, "team_a") and hasattr(match, "team_b"):
        return f"{localize_team_name(match.team_a.name, _lang())} vs {localize_team_name(match.team_b.name, _lang())}"
    label = str(getattr(match, "label", ""))
    if " vs " in label:
        left, right = label.split(" vs ", 1)
        return f"{localize_team_name(left, _lang())} vs {localize_team_name(right, _lang())}"
    return label


def _localize_team_mentions(text: str, *team_names: str) -> str:
    if _lang() != "es":
        return text
    localized = text
    for name in sorted((name for name in team_names if name), key=len, reverse=True):
        localized = localized.replace(name, localize_team_name(name, _lang()))
    return localized


_AUDIT_LABEL_KEYS = {
    "Marcador (modo)": "audit.row.score_mode",
    "Goles esperados (xG)": "audit.row.expected_goals",
    "Goles": "audit.row.goals",
    "Tiros": "audit.row.shots",
    "Tiros a puerta": "audit.row.shots_on_target",
    "Córners": "audit.row.corners",
    "Tarjetas": "audit.row.cards",
    "Posesión %": "audit.row.possession",
}


def _localized_audit_label(label: str) -> str:
    key = _AUDIT_LABEL_KEYS.get(label)
    return _t(key) if key else label


def _localize_audit_row(record: dict) -> dict:
    localized = dict(record)
    localized["Métrica"] = _localized_audit_label(str(record["Métrica"]))
    if _lang() == "en":
        localized["Δ"] = str(localized["Δ"]).replace(" goles vs esperado", " goals vs expected").replace(" goles", " goals")
    return localized


def _localize_per_team_audit_row(row: dict) -> dict:
    localized = dict(row)
    localized["label"] = _localized_audit_label(str(row["label"]))
    return localized


def _localized_form_adjustment_note(note: str) -> str:
    if _lang() == "es":
        return note
    return (
        note.replace("Ajuste por forma del torneo:", "Tournament-form adjustment:")
        .replace("peso ", "weight ")
        .replace(" partidos)", " matches)")
    )


def _localized_team_stat_rows(rows: list[dict]) -> list[dict]:
    label_map = {
        "Tiros": "shots",
        "Tiros a puerta": "shots_on_target",
        "Córners": "corners",
        "Tarjetas": "cards",
        "Tarjetas amarillas": "cards",
    }
    localized = []
    for row in rows:
        item = dict(row)
        raw_label = str(item.pop("Estadística", item.pop("Statistic", "")))
        metric = label_map.get(raw_label)
        item[_b("Estadística", "Statistic")] = (
            localize_metric(metric) if metric else raw_label
        )
        if "Confianza" in item:
            item[_b("Confianza", "Confidence")] = localize_confidence(item.pop("Confianza"))
        if "Muestra" in item:
            item[_b("Muestra", "Sample")] = item.pop("Muestra")
        localized.append(item)
    return localized
PRECOMPUTED_PENALTY_DIR = DATA_DIR / "precomputed" / "penalties"
DAILY_PROVIDERS = (*DEFAULT_PROVIDERS, "martj42_world_schedule")
HOST_TEAMS = {"USA", "Canada", "Mexico"}
PREDICTION_ENGINE_VERSION = "2026-06-29-knockout-phases-v1"
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
    return _repo().list_matches(competition="FIFA World Cup 2026")


def _list_matches():
    """Return matches the UI cares about — i.e. the World Cup 2026 fixtures.

    The DB now also holds ~4k historical matches from the StatsBomb +
    eatpizzanot back-fills used to train the deep-stats classifier. Those
    should NOT appear in the schedule selectboxes, dashboard counters or
    backtesting panel — they're training data, not part of the tournament.
    """
    return _matches_cached(_db_signature())


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
    return _repo().get_all_match_evidence_statuses(competition="FIFA World Cup 2026")


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
        st.info(_b(f"La fuente actual no publica datos suficientes para {title.lower()}.", f"The current source does not publish enough data for {title.lower()}."))
        return
    if metric == "impact":
        st.caption(
            _b("Escala 0-100 (percentil del jugador dentro de su posición). "
            "Cada rol pondera lo suyo: delanteros premia goles/tiros, medios "
            "asistencias/pases, defensas tackles+despejes, porteros % paradas. "
            "El selector de minutos solo filtra la vista; la puntuación es estable.",
            "0-100 scale (the player's percentile within their position). Each role weights the relevant contributions: goals and shots for forwards, assists and passing for midfielders, tackles and clearances for defenders, and save percentage for goalkeepers. The minutes control only filters the view; the score itself remains stable.")
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
                _t("players.search"), key=f"search_{metric}",
                placeholder=_t("players.search_placeholder"), label_visibility="collapsed",
            ).strip()
        with sort_col2:
            sort_choice = st.radio(
                _t("players.sort"), [total_label, rate_label], horizontal=True,
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
        empty_state(_t("players.no_results"), _t("players.no_results_body"), icon="🔍")
        return
    _render_player_ranking_table(ranked, total_col, total_label, rate_col, rate_label)
    # Chart in an expander — Altair rendering is the heaviest step and most
    # users don't need to expand the bar chart every interaction.
    with st.expander(_t("players.chart", metric=title.lower())):
        chart = alt.Chart(ranked.head(15)).mark_bar(cornerRadiusEnd=4, color="#1769E0").encode(
            y=alt.Y("player_name:N", sort="-x", title=None),
            x=alt.X(f"{metric}:Q", title=title),
            tooltip=["player_name", "team_name", "minutes", alt.Tooltip(f"{metric}:Q", format=".2f")],
        ).properties(height=360)
        st.altair_chart(chart, width="stretch")


_POSITION_LABEL = {
    "es": {"ATT": "Delantero", "MID": "Centrocampista", "DEF": "Defensa", "GK": "Portero"},
    "en": {"ATT": "Forward", "MID": "Midfielder", "DEF": "Defender", "GK": "Goalkeeper"},
}


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
            pos_label = _POSITION_LABEL[_lang()].get(str(row.get("position_group") or ""), "")
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
    header_cells = [f'<th>{_t("players.player")}</th>']
    if show_position:
        header_cells.append(f'<th>{_t("players.position")}</th>')
    header_cells.extend([
        f'<th>{_t("players.team")}</th>', f'<th>{_t("players.minutes")}</th>', f'<th>{_t("players.matches")}</th>',
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
    return pd.DataFrame(localize_table_columns_i18n(frame.to_dict(orient="records"), language=_lang()))


@st.cache_resource(ttl=900, show_spinner=False)
def _refresh_current_world_cup_banks_cached(
    refresh_bucket: str,
    providers: tuple[str, ...],
):
    repo = _repo()
    now = datetime.now(timezone.utc)
    result = ensure_current_world_cup_data(
        repo,
        build_daily_fetcher(),
        importer=build_daily_importer(repo, now),
        now=now,
        providers=providers,
    )
    if "github_wc2026_team_stats" in result.updated:
        repo.sync_gh_team_stats_to_observations(now.isoformat())
    return result


def _refresh_current_world_cup_banks(repo: Repository):
    return _refresh_current_world_cup_banks_cached(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
        tuple(DAILY_PROVIDERS),
    )


def _daily_refresh_failure_details(repo: Repository, daily_result) -> list[str]:
    details: list[str] = []
    for provider_id in getattr(daily_result, "failed", ()):
        checks = repo.list_dataset_refresh_checks(provider_id)
        message = (
            str(checks[0].get("error_message") or "Error sin detalle")[:240]
            if checks else "Error sin detalle"
        )
        details.append(f"{provider_id}: {message}")
    return details


def _render_external_dataset_review(repo: Repository) -> None:
    """Surface score mismatches and unresolved entity aliases from the
    github_wc2026 external dataset. Nothing here mutates data automatically;
    every action requires an explicit user click."""
    try:
        mismatches = repo.list_score_mismatches(only_unresolved=True)
        pending_teams = repo.list_pending_aliases("team")
        pending_players = repo.list_pending_aliases("player")
    except Exception:
        return
    try:
        candidates = repo.list_gh_score_candidates()
    except Exception:
        candidates = []
    with st.expander(_t("source.external_dataset"), expanded=False):
        st.write(_t("source.pending_results", count=len(candidates)))
        for row in candidates:
            label = (
                f"{row['home_team_name']} {row['home_score']}-{row['away_score']} "
                f"{row['away_team_name']}"
            )
            max_minute = row.get("max_event_minute") or 0
            col1, col2 = st.columns([3, 1])
            col1.write(label)
            if max_minute > 90:
                col1.caption(
                    _b(
                        f"Eventos hasta el minuto {max_minute}: confirma solo si el partido terminó en los 90 minutos (incluido el descuento). Si hubo prórroga o penaltis, usa el cierre manual por periodos.",
                        f"Events are recorded through minute {max_minute}. Confirm only if the match ended in regulation, including stoppage time. If extra time or a shootout was played, use the period-by-period settlement workflow.",
                    )
                )
            if col2.button(_t("source.confirm"), key=f"gh_score_confirm_{row['match_id']}"):
                repo.settle_match(
                    int(row["match_id"]), int(row["goals_a"]), int(row["goals_b"]),
                    [], datetime.now(timezone.utc),
                    source_type="verified_external_confirmed",
                )
                try:
                    resolve_knockout_bracket(repo)
                except Exception:
                    pass
                st.rerun()
        st.write(_t("source.pending_score_mismatches", count=len(mismatches)))
        st.write(_t("source.pending_team_aliases", count=len(pending_teams)))
        st.write(_t("source.pending_player_aliases", count=len(pending_players)))
        if mismatches:
            st.dataframe(_visible_frame(mismatches), use_container_width=True)
        if pending_teams:
            st.markdown(_t("source.teams_to_assign"))
            team_options = {
                f"{row['name']} (id {row['id']})": int(row["id"])
                for row in sorted(
                    ({"id": t.id, "name": t.name} for t in _list_teams(repo)),
                    key=lambda item: item["name"],
                )
            }
            for row in pending_teams:
                col1, col2, col3 = st.columns([2, 2, 1])
                col1.write(f"{row['display_name']} (ext {row['external_id']})")
                choice = col2.selectbox(
                    _t("source.local_team"),
                    list(team_options),
                    key=f"gh_team_alias_{row['external_id']}",
                    label_visibility="collapsed",
                )
                if col3.button(_t("source.confirm"), key=f"gh_team_confirm_{row['external_id']}"):
                    repo.confirm_alias(
                        "team", "github_wc2026", str(row["external_id"]),
                        team_options[choice], "ui",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    repo.resolve_gh_foreign_keys()
                    st.rerun()


def _list_teams(repo: Repository):
    with repo.session() as con:
        rows = con.execute("SELECT id, name FROM teams ORDER BY name").fetchall()

    class _Team:
        __slots__ = ("id", "name")

        def __init__(self, row):
            self.id = row["id"]
            self.name = row["name"]

    return [_Team(row) for row in rows]


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


@st.cache_resource(show_spinner=False)
def _penalty_match_context_cached(
    match_id: int,
    db_sig: tuple[int, int],
    model_version: str,
):
    repo = _repo()
    match = repo.get_match(match_id)
    team_a, team_b = match.team_a.name, match.team_b.name
    input_fingerprint = repository_penalty_input_fingerprint(
        repo,
        match,
        model_version=model_version,
    )
    precomputed = load_precomputed_context(
        PRECOMPUTED_PENALTY_DIR,
        team_a,
        team_b,
        model_version=model_version,
        expected_input_fingerprint=input_fingerprint,
    )
    if precomputed is not None:
        return precomputed
    fallback = build_penalty_match_context(
        team_a,
        team_b,
        repo.list_penalty_attempts(team_a) + repo.list_penalty_attempts(team_b),
    )
    return replace(
        fallback,
        explanation=(
            fallback.explanation
            + " Cálculo detallado pendiente de precálculo tras cerrar la fase de grupos."
        ),
    )


def _penalty_match_context(match):
    return _penalty_match_context_cached(
        match.id,
        _db_signature(),
        PENALTY_MODEL_VERSION,
    )


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
    extra_time_xg = None
    if repo is not None:
        penalty_context = _penalty_match_context(match)
        adjustment = adjust_extra_time_xg(
            match.team_a.name,
            match.team_b.name,
            xa,
            xb,
            repo.list_extra_time_training_rows_before(match.kickoff_utc),
            match.kickoff_utc,
        )
        extra_time_xg = adjustment.adjusted_xg
    unified_1x2 = None
    primary_rows = getattr(bundle, "primary", None) or ()
    lookup = {str(row.selection_name): float(row.probability) for row in primary_rows}
    if lookup:
        home_p = lookup.get(match.team_a.name)
        away_p = lookup.get(match.team_b.name)
        draw_p = lookup.get("Draw")
        if home_p is not None and away_p is not None and draw_p is not None:
            unified_1x2 = {"home": home_p, "draw": draw_p, "away": away_p}
    return predict_knockout_match(
        xa, xb,
        dispersion=0.08,    # matches DEFAULT_NB_DISPERSION in services.py
        rho=-0.16,          # matches DEFAULT_DIXON_COLES_RHO
        home_penalty_win_probability=(
            penalty_context.team_a_shootout_win_probability if penalty_context else None
        ),
        extra_time_xg=extra_time_xg,
        regulation_1x2=unified_1x2,
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
    """No-op for knockout matches: the full KO identity (badge + advance card
    + conditional funnel + xG por fase + penalty narrative) is rendered
    upstream in ``render_prediction_lab`` to keep the layout cohesive.

    Returns True when the match is a knockout fixture (signalling the caller
    that the KO flow already took over), False otherwise so the caller falls
    back to the standard group-stage market panel.
    """
    pred = _knockout_prediction_for_match(match, bundle, repo)
    return pred is not None


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
        f"<span>{_b('Marcadores posibles', 'Possible scorelines')}</span>"
        f"<small>{escape(team_a)} {_b('goles', 'goals')} ↓ · {escape(team_b)} {_b('goles', 'goals')} →</small>"
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
        score_cards.append((_b("MÁS PROBABLE", "MOST LIKELY"), main_exact.selection_name, main_exact.probability))
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
            f'<div class="eyebrow">{_b("Marcadores exactos más probables", "Most likely exact scorelines")}</div>'
            f'<div class="score-cards">{cards_html}</div>',
            unsafe_allow_html=True,
        )
        st.caption(_b("Probabilidades estimadas con la distribución Dixon-Coles.", "Probabilities estimated with the Dixon-Coles distribution."))

    grid_html = _score_grid_html(team_a, team_b, predictions)
    if grid_html:
        st.markdown(grid_html, unsafe_allow_html=True)
    else:
        st.info(_b("Sin probabilidades de marcador exacto para este partido.", "No exact-score probabilities are available for this match."))


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
        return (
            f"{_display_time(match.kickoff_utc, '%d %b · %H:%M')} — "
            f"{_localized_match_label(match)}"
        )

    labels: list[str] = []
    lookup: dict[str, object] = {}

    if group_matches:
        labels.append(_b("─── Fase de grupos ───", "─── Group stage ───"))
        for m in group_matches:
            l = _label(m)
            labels.append(l)
            lookup[l] = m
    if knockout_matches:
        labels.append(_b("─── Fase eliminatoria ───", "─── Knockout stage ───"))
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
        return _b("Sin datos", "No data"), "red"
    if bundle.missing_critical:
        return _b("Cobertura parcial", "Partial coverage"), "amber"
    return _b("Datos listos", "Data ready"), "green"


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
        + status_pill(_b("Actualizado ", "Updated ") + _display_time(bundle.updated_at_utc, '%d/%m %H:%M'))
        + status_pill(_b("Evento de la fuente #", "Source event #") + str(bundle.event_id))
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
    c1.metric(_b("Estadísticas disponibles", "Statistics available"), summary["Estadísticas disponibles"])
    c2.metric(_b("Jugadores disponibles", "Players available"), summary["Jugadores disponibles"])
    c3.metric(_b("Fuentes", "Sources"), summary["Fuentes"])
    c4.metric(_b("Profundidad", "Deep statistics"), summary["Estadísticas profundas"])
    c5.metric(_b("Alineación", "Line-up"), _b(str(summary["Alineación"]), "Confirmed" if summary["Alineación"] == "Confirmada" else "Unconfirmed"))
    if bundle.missing_critical or bundle.missing_optional:
        missing_labels = {
            "team_statistics": _b("estadísticas de equipo", "team statistics"),
            "players": _b("alineación confirmada", "confirmed line-up"),
            "availability": _b("disponibilidad", "availability"),
            "lineups": _b("alineaciones", "line-ups"),
            "event": _b("evento", "event"),
        }
        callout(
            _b("Faltan: ", "Missing: ")
            + ", ".join(missing_labels.get(value, value) for value in bundle.missing_critical + bundle.missing_optional)
            + _b(". La aplicación no sustituye esos campos con valores inventados.", ". The application does not replace those fields with invented values."),
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
    form_adjustment_note: str | None = None


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
    deep_obs_for_profile = repo.list_deep_team_metric_observations_before(
        match.kickoff_utc, team_names=(team_a, team_b)
    )
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

    # Phase 1 (ghwc): tournament-form adjustment, applied inside the final
    # 1X2 ensemble (predict_match_markets) so production matches the
    # calibration semantics exactly. Here we only compute the logit shift.
    form_adjustment_note = None
    form_shift = 0.0
    try:
        import json as _json
        from wcpredict.tournament_form_adjustment import (
            alpha_for_match,
            build_match_adjustment,
        )
        _calibration = repo.latest_form_calibration()
        _alphas = {}
        if _calibration:
            if _calibration.get("alphas_json"):
                _alphas = _json.loads(str(_calibration["alphas_json"]))
            else:
                _alphas = {"3plus": float(_calibration["alpha"] or 0.0)}
        if any(float(v) > 0.0 for v in _alphas.values()):
            _adj = build_match_adjustment(repo, team_a, team_b, str(match.kickoff_utc))
            _alpha = alpha_for_match(_alphas, _adj.matches_min)
            if _alpha > 0.0 and _adj.weight > 0.0 and _adj.score != 0.0:
                form_shift = _alpha * _adj.weight * _adj.score
                form_adjustment_note = (
                    f"Ajuste por forma del torneo: score {_adj.score:+.2f}, "
                    f"peso {_adj.weight:.2f} ({_adj.matches_min} partidos), "
                    f"α {_alpha:.2f} → {form_shift:+.2f} logit"
                )
    except Exception:
        form_adjustment_note = None
        form_shift = 0.0

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
        form_shift=form_shift,
        form_note=form_adjustment_note or "",
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
        form_adjustment_note=form_adjustment_note,
    )
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
    neutral_names = {
        "1X2", "Exact Score", "Exact Score (favorito)",
        "Exact Score (alt)", "Expected Score", "Exact Score Grid",
    }
    return {
        "team_a": team_a,
        "team_b": team_b,
        "expected_xg": list(expected_xg) if expected_xg else [],
        "deep_count": int(deep_count),
        "prior_deep_samples": int(prior_deep_samples),
        "primary": [_row(p) for p in primary],
        "predictions": [_row(p) for p in predictions if p.market_name in neutral_names],
    }


def _persist_pre_match_snapshot(
    match,
    bundle: MatchAnalysisBundle,
    repo: Repository,
    knockout_prediction,
) -> None:
    """Persist one complete immutable payload after every model branch exists."""
    now_utc = datetime.now(timezone.utc)
    if match.kickoff_utc <= now_utc:
        return
    payload = _bundle_snapshot_payload(
        match.team_a.name,
        match.team_b.name,
        bundle.predictions,
        bundle.primary,
        bundle.expected_xg,
        bundle.deep_count,
        bundle.prior_deep_samples,
    )
    if knockout_prediction is not None and bundle.expected_xg:
        xa, xb = float(bundle.expected_xg[0]), float(bundle.expected_xg[1])
        adjustment = adjust_extra_time_xg(
            match.team_a.name,
            match.team_b.name,
            xa,
            xb,
            repo.list_extra_time_training_rows_before(match.kickoff_utc),
            match.kickoff_utc,
        )
        penalty_context = _penalty_match_context(match)
        payload["knockout"] = build_knockout_snapshot_section(
            knockout_prediction,
            adjustment.adjusted_xg,
            penalty_context,
        )
    repo.save_prediction_snapshot(
        match_id=match.id,
        payload=payload,
        data_as_of_utc=now_utc,
        model_version=PREDICTION_ENGINE_VERSION,
        generated_at_utc=now_utc,
    )


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
    from wcpredict.team_projections import predict_team_statistics

    deep_profile_rows = repo.list_deep_team_metric_observations_before(
        match.kickoff_utc, team_names=(team_a, team_b)
    )
    team_profiles = build_team_profiles(
        (team_a, team_b),
        deep_profile_rows,
        match.kickoff_utc,
        opponent_strengths=opponent_strengths,
    )
    from wcpredict.referee_cards_model import referee_card_multiplier_for_match
    try:
        card_multiplier, referee_name = referee_card_multiplier_for_match(repo, match.id)
    except Exception:
        card_multiplier, referee_name = 1.0, None
    team_lines = predict_team_statistics(
        team_profiles[team_a], team_profiles[team_b],
        card_multiplier=card_multiplier,
    )
    if referee_name and abs(card_multiplier - 1.0) >= 0.02:
        section_note(
            _b(
                f"Árbitro asignado: {referee_name} — tendencia de tarjetas x{card_multiplier:.2f} aplicada a la estimación de tarjetas amarillas.",
                f"Assigned referee: {referee_name} — card tendency x{card_multiplier:.2f} applied to the yellow-card estimate.",
            )
        )
    team_volume_stat_rows: list[dict] = []
    team_volume_predictions: dict[str, dict[str, float]] = {}
    if not team_lines:
        return TeamVolumeContext()
    expected_by_team_metric: dict[tuple[str, str], dict] = {}
    for row in team_lines:
        key = (row.team_name, row.metric)
        if key in expected_by_team_metric:
            continue
        expected_by_team_metric[key] = {
            "expected": row.expected,
            "confidence": row.confidence,
            "sample": row.sample_size,
        }
        team_volume_predictions.setdefault(row.metric, {})[row.team_name] = float(row.expected)
    metric_aliases = {"shots_total": "shots", "yellow_cards": "cards"}
    for source_metric, target_metric in metric_aliases.items():
        values = team_volume_predictions.get(source_metric)
        if values:
            team_volume_predictions[target_metric] = dict(values)
    metric_labels = {row.metric: row.label for row in team_lines}
    for metric_id, label in metric_labels.items():
        a = expected_by_team_metric.get((team_a, metric_id))
        b = expected_by_team_metric.get((team_b, metric_id))
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
    team_volume = _team_volume_context_from_profiles_cached(match_id, db_sig, engine_version)
    volume_predictions = {
        metric: sum(values.values())
        for metric, values in team_volume.team_volume_predictions.items()
        if metric in {"corners", "cards", "shots", "shots_on_target"}
    }

    return MatchVolumeBundle(
        volume_predictions=volume_predictions,
        team_volume_predictions=team_volume.team_volume_predictions,
        volume_market_rows=[],
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


@st.cache_resource(show_spinner=False)
def _player_match_context_cached(
    match_id: int,
    db_sig: tuple[int, int],
    engine_version: str,
    team_a: str,
    team_b: str,
    _current_players: list[dict],
    _auxiliary: MatchAuxiliaryBundle,
):
    return prepare_player_match_context(
        team_a,
        team_b,
        _current_players,
        _repo().list_imported_lineups(match_id),
        _auxiliary.team_volume_predictions,
        _auxiliary.goalkeeper_baselines,
    )


@st.cache_resource(show_spinner=False)
def _invalidate_match_analysis_caches() -> None:
    _match_analysis_bundle_cached.clear()
    _match_volume_context_cached.clear()
    _match_auxiliary_context_cached.clear()
    _player_match_context_cached.clear()


def _invalidate_player_caches() -> None:
    _match_analysis_bundle_cached.clear()
    _match_auxiliary_context_cached.clear()
    _player_match_context_cached.clear()
    _player_intelligence_rows_cached.clear()


def _render_team_statistics(auxiliary: MatchVolumeBundle | MatchAuxiliaryBundle) -> None:
    team_volume_stat_rows = getattr(auxiliary, "team_volume_stat_rows", [])
    if team_volume_stat_rows:
        st.subheader(_t("team_stats.title"))
        st.caption(_t("team_stats.note"))
        st.dataframe(
            pd.DataFrame(_localized_team_stat_rows(team_volume_stat_rows)),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(_t("team_stats.empty"))


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
        f'<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">{_t("audit.metric")}</th>'
        f'<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">{_t("audit.predicted")}</th>'
        f'<th style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">{_t("audit.actual")}</th>'
        '<th style="text-align:right;padding:6px 10px;color:#5b6b80;font-weight:600">Δ</th>'
        '</tr></thead><tbody>',
    ]
    for record in records:
        record = _localize_audit_row(record)
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
        f'<th rowspan="2" style="text-align:left;padding:6px 10px;color:#5b6b80;font-weight:600">{_b("Métrica", "Metric")}</th>'
        f'<th colspan="3" style="text-align:center;padding:6px 10px;color:#10233F;font-weight:700;background:#0b1f3a11">{localize_team_name(team_a, _lang())}</th>'
        f'<th colspan="3" style="text-align:center;padding:6px 10px;color:#10233F;font-weight:700;background:#0b1f3a11">{localize_team_name(team_b, _lang())}</th>'
        '</tr>'
        '<tr>'
        f'<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">{_b("Pred.", "Pred.")}</th>'
        f'<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">{_b("Real", "Actual")}</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Δ</th>'
        f'<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">{_b("Pred.", "Pred.")}</th>'
        f'<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">{_b("Real", "Actual")}</th>'
        '<th style="text-align:right;padding:4px 8px;color:#5b6b80;font-weight:600;font-size:0.82rem">Δ</th>'
        '</tr></thead><tbody>',
    ]
    for row in rows:
        row = _localize_per_team_audit_row(row)
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


def _current_model_knockout_payload(repo: Repository, match, bundle) -> dict | None:
    """Retro-prediction with the CURRENT model under strict pre-kickoff
    cutoffs (user policy 2026-07-04: audits always use the most refined
    model available, never the historical snapshot, but never post-match
    data either — every input below is *_before(kickoff))."""
    knockout_prediction = _knockout_prediction_for_match(match, bundle, repo)
    if knockout_prediction is None or not bundle.expected_xg:
        return None
    payload = _bundle_snapshot_payload(
        match.team_a.name,
        match.team_b.name,
        bundle.predictions,
        bundle.primary,
        bundle.expected_xg,
        bundle.deep_count,
        bundle.prior_deep_samples,
    )
    xa, xb = float(bundle.expected_xg[0]), float(bundle.expected_xg[1])
    adjustment = adjust_extra_time_xg(
        match.team_a.name,
        match.team_b.name,
        xa,
        xb,
        repo.list_extra_time_training_rows_before(match.kickoff_utc),
        match.kickoff_utc,
    )
    payload["knockout"] = build_knockout_snapshot_section(
        knockout_prediction,
        adjustment.adjusted_xg,
        _penalty_match_context(match),
    )
    return payload


def _render_knockout_phase_audit(repo: Repository, match, bundle=None) -> None:
    phase_result = repo.get_active_match_phase_result(match.id)
    if phase_result is None:
        st.caption(
            _b("Auditoría por fases no disponible: este cierre no tiene desglose de eliminatoria.", "Phase-by-phase audit unavailable: this completed match has no knockout-stage breakdown.")
        )
        return
    snapshot = None
    if bundle is not None:
        try:
            snapshot = _current_model_knockout_payload(repo, match, bundle)
        except Exception:
            snapshot = None
    if snapshot is None:
        snapshots = repo.list_prediction_snapshots(match.id)
        if not snapshots:
            st.caption(
                _b("Auditoría por fases no disponible: este cierre no tiene snapshot prepartido o desglose de eliminatoria.", "Phase-by-phase audit unavailable: this completed match has no pre-match snapshot or knockout-stage breakdown.")
            )
            return
        snapshot = json.loads(snapshots[0]["payload_json"])
    try:
        audit = evaluate_knockout_snapshot(
            snapshot,
            phase_result,
            repo.list_active_shootout_kicks(match.id),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        st.caption(_b("El snapshot anterior no contiene el formato de auditoría por fases.", "The earlier snapshot does not contain the phase-audit format."))
        return

    st.markdown(_b("#### Auditoría por fases", "#### Phase-by-phase audit"))
    sections = (
        (_b("90 minutos", "90 minutes"), audit.regulation),
        (_b("Prórroga", "Extra time"), audit.extra_time),
        (_b("Penaltis", "Penalty shootout"), audit.shootout),
    )
    for label, section in sections:
        with st.expander(label, expanded=section is audit.regulation):
            if section.status == "not_played":
                st.caption(_b("No se disputó.", "Not played."))
                continue
            cols = st.columns(3)
            cols[0].metric(_b("Real", "Actual"), section.actual_score or "—")
            cols[1].metric(
                _b("Resultado previsto", "Predicted outcome"),
                {"home": match.team_a.name, "draw": _b("Empate", "Draw"), "away": match.team_b.name}.get(
                    section.predicted_outcome, "—"
                ),
            )
            cols[2].metric(
                _b("Probabilidad de lo ocurrido", "Probability of the observed outcome"),
                f"{section.observed_probability:.1%}"
                if section.observed_probability is not None else "—",
            )
            if section is audit.extra_time and section.rows:
                details = section.rows[0]
                st.caption(
                    _b("xG esperado de prórroga: ", "Expected extra-time xG: ")
                    + "–".join(f"{float(value):.2f}" for value in details.get("expected_xg", []))
                    + _b(f" · marcador modal {details.get('mode_score') or '—'}", f" · modal score {details.get('mode_score') or '—'}")
                )
            if section is audit.shootout and section.rows:
                st.dataframe(
                    pd.DataFrame(section.rows).rename(columns={
                        "team_name": _b("Selección", "Team"),
                        "player_name": _b("Tirador", "Taker"),
                        "outcome": _b("Resultado", "Outcome"),
                        "predicted_conversion": _b("Prob. gol", "Scoring probability"),
                        "on_field_probability": _b("Prob. al 120'", "On-field probability at 120'"),
                        "first_five_probability": _b("Prob. primeros cinco", "First-five probability"),
                        "brier": "Brier",
                    }),
                    hide_index=True,
                    width="stretch",
                )


def _render_post_match_audit(
    bundle: MatchAnalysisBundle,
    auxiliary: MatchAuxiliaryBundle,
    team_a: str,
    team_b: str,
    is_knockout: bool = False,
    repo: Repository | None = None,
    match=None,
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
    st.subheader(_t("audit.title"))
    st.caption(_t("audit.legend", score=audit["actual_score"]))
    metric_cols = st.columns(3)
    metric_cols[0].metric(_t("audit.final_score"), audit["actual_score"])
    metric_cols[1].metric(
        _t("audit.mean_brier"),
        f"{brier_average:.3f}" if brier_average is not None else "—",
        help=_b(f"Promedio de {len(auxiliary.backtests)} predicciones evaluadas", f"Average across {len(auxiliary.backtests)} evaluated predictions"),
    )
    metric_cols[2].metric(
        _t("audit.observed_stats"),
        len(auxiliary.team_match_stats),
        help=_b("Filas de estadísticas por equipo: alimentan automáticamente las predicciones de partidos posteriores.", "Team-statistics rows automatically feed predictions for later matches."),
    )
    # The knockout header already renders these probabilities in Claude's
    # advance/funnel bars. Do not reintroduce the group-stage 1X2 table when
    # the match is closed; keep the score/deep-stat audit below the bars.
    if not is_knockout:
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
        st.markdown(_t("audit.team_comparison"))
        _render_per_team_audit_table(per_team_rows, team_a, team_b)

    _render_audit_table(audit["volume"])
    if not audit["volume"] and not per_team_rows:
        st.caption(
            _b("Sin estadísticas de equipo todavía. Cuando importes el JSON revisado o cierres el partido en Calibración con las estadísticas, esta tabla mostrará córners, tarjetas y tiros.", "No team statistics are available yet. After importing the reviewed JSON or completing the match with statistics in Calibration, this table will show corners, cards and shots.")
        )
    st.caption(
        _b("Lo registrado aquí ya alimenta el ajuste de xG y las proyecciones estadísticas para los próximos partidos de ambas selecciones: la evidencia de auditoría se utiliza, no solo se almacena.", "The evidence recorded here already feeds xG adjustments and statistical projections for both teams' later matches: audit data is used, not merely stored.")
    )
    if is_knockout and repo is not None and match is not None:
        _render_knockout_phase_audit(repo, match, bundle)


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
    with st.spinner(_t("dashboard.refreshing")):
        daily_result = _refresh_current_world_cup_banks(repo)
    _resolve_bracket_after_daily_refresh(repo, daily_result)
    summary = _database_summary()
    hero(
        _t("hero.eyebrow"),
        _t("hero.title"),
        _t("hero.supporting"),
    )
    cols = st.columns(4)
    metrics = [
        (_t("dashboard.matches"), summary["matches"]),
        (_t("dashboard.teams"), summary["teams"]),
        (_t("dashboard.imports"), summary["imports"]),
        (_t("dashboard.predictions"), summary["predictions"]),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, int(value))

    st.subheader(_t("dashboard.upcoming"))
    section_note(_t("dashboard.coverage_note"))
    daily_tone = (
        "green" if daily_result.status in {"current", "updated"}
        else "amber" if daily_result.status in {"partial", "stale"}
        else "red"
    )
    st.markdown(
        '<div class="status-row">'
        + status_pill(_t("dashboard.daily_data", status=localize_controlled_i18n("status", daily_result.status, language=_lang())), daily_tone)
        + status_pill(_t("dashboard.updated", count=len(daily_result.updated)), "green" if daily_result.updated else "neutral")
        + status_pill(_t("dashboard.failed", count=len(daily_result.failed)), "red" if daily_result.failed else "neutral")
        + "</div>",
        unsafe_allow_html=True,
    )
    failure_details = _daily_refresh_failure_details(repo, daily_result)
    if failure_details:
        with st.expander(_t("dashboard.error_details")):
            for detail in failure_details:
                st.write(detail)
    _render_external_dataset_review(repo)
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
                f'<span>{localize_team_name(match.team_a.name, _lang())}</span></span> '
                f'<span style="color:var(--muted);font-weight:500;margin:0 6px;">vs</span> '
                f'<span class="match-team">{crest_html(match.team_b.name, size=20)}'
                f'<span>{localize_team_name(match.team_b.name, _lang())}</span></span></a></td>'
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
            f'<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{_t("dashboard.local_time")}</th>'
            f'<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{_t("dashboard.match")}</th>'
            f'<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{_t("dashboard.venue")}</th>'
            f'<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{_t("dashboard.data")}</th>'
            f'<th style="text-align:left;padding:10px 12px;color:var(--muted);font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{_t("dashboard.last_capture")}</th>'
            '</tr></thead><tbody>' + "".join(rows_html) + "</tbody></table></div>",
            unsafe_allow_html=True,
        )
    else:
        empty_state(
            _t("dashboard.empty_title"),
            _t("dashboard.empty_body"),
            icon="📅",
        )

    _render_bracket_section(repo)


_BRACKET_STAGE_TO_ROUND = {
    "Round of 32":          "round_of_32",
    "Round of 16":          "round_of_16",
    "Quarter-final":        "quarter",
    "Semi-final":           "semi",
    "Final":                "final",
    "Third-place play-off": "third_place",
}

# Positional order of slots within each stage, matching the official FIFA 2026
# bracket tree. The CSV defines slot_ids by KICKOFF order, not by bracket
# position, so rendering by slot_id (M73, M74, M75…) makes the CSS connectors
# pair the wrong matches visually (e.g. Argentina M86 next to M85 instead of
# M88, even though data has M95 = W:M86 vs W:M88). Sorting by this list makes
# the visual connectors line up with the actual pairings:
#
#   R32  pair 0 → M89 (W74-W77) · pair 1 → M90 (W73-W75) · pair 2 → M93 …
#   R16  pair 0 → M97 (W89-W90) · pair 1 → M98 (W93-W94) · …
#   QF   pair 0 → SF1 (W97-W98) · pair 1 → SF2 (W99-W100)
_BRACKET_POSITION_ORDER = {
    "Round of 32": [
        "M74", "M77",  # → M89 → M97 (top half / top quadrant)
        "M73", "M75",  # → M90 ↗
        "M83", "M84",  # → M93 → M98 (bottom half / upper)
        "M81", "M82",  # → M94 ↗
        "M76", "M78",  # → M91 → M99 (top half / lower)
        "M79", "M80",  # → M92 ↗
        "M86", "M88",  # → M95 → M100 (bottom half / bottom)
        "M85", "M87",  # → M96 ↗
    ],
    "Round of 16":   ["M89", "M90", "M93", "M94", "M91", "M92", "M95", "M96"],
    "Quarter-final": ["M97", "M98", "M99", "M100"],
    "Semi-final":    ["SF1", "SF2"],
    "Final":         ["F"],
    "Third-place play-off": ["3RD"],
}

_MONTH_ES_SHORT = ("ene", "feb", "mar", "abr", "may", "jun",
                   "jul", "ago", "sep", "oct", "nov", "dic")


def _bracket_date_label(kickoff_utc: str | None) -> str:
    """Compact Spanish date label, e.g. '28 jun'."""
    if not kickoff_utc:
        return ""
    iso = _display_time(kickoff_utc, "%Y-%m-%d")
    try:
        _, month_str, day_str = iso.split("-")
        return f"{int(day_str)} {_MONTH_ES_SHORT[int(month_str) - 1]}"
    except (ValueError, IndexError):
        return iso


def _render_bracket_section(repo: Repository) -> None:
    """Tournament-style bracket: cards connected by CSS gutters, colour-coded
    per round, with crest + name + VS. Pending slots show the source token.

    Horizontal scroll is preserved on every viewport (the inner container has
    a 1260px minimum width and ``overflow-x: auto`` on the outer wrapper)."""
    try:
        resolve_knockout_bracket(repo)
    except Exception:
        pass
    slots = bracket_view(repo)
    if not slots:
        return
    st.subheader(_t("bracket.title"))

    # Bulk-fetch results for all knockout match_ids — used to label the cards
    # as "closed" (with score) or "live" instead of always "pending".
    match_ids = [int(s["match_id"]) for s in slots if s.get("match_id")]
    results_by_match: dict[int, dict] = {}
    if match_ids:
        with sqlite3.connect(repo.path, timeout=30) as con:
            placeholders = ",".join("?" * len(match_ids))
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"SELECT mr.match_id, mr.goals_a, mr.goals_b, "
                f"mr.extra_time_team_a_goals AS legacy_extra_time_goals_a, "
                f"mr.extra_time_team_b_goals AS legacy_extra_time_goals_b, "
                f"mr.penalty_team_a AS legacy_penalty_goals_a, "
                f"mr.penalty_team_b AS legacy_penalty_goals_b, "
                f"pr.regulation_goals_a, pr.regulation_goals_b, "
                f"pr.extra_time_goals_a AS phase_extra_time_goals_a, "
                f"pr.extra_time_goals_b AS phase_extra_time_goals_b, "
                f"pr.shootout_goals_a, pr.shootout_goals_b, pr.decided_in "
                f"FROM match_results mr "
                f"LEFT JOIN settlement_versions sv ON sv.match_id=mr.match_id AND sv.active=1 "
                f"LEFT JOIN match_phase_results pr ON pr.settlement_version_id=sv.id "
                f"WHERE mr.match_id IN ({placeholders})",
                match_ids,
            ).fetchall()
        for r in rows:
            results_by_match[int(r["match_id"])] = dict(r)

    today_utc = datetime.now(timezone.utc).date()

    def _slot_status_score_winner(slot: dict) -> tuple[str, dict | None]:
        """Return status plus normalized 120-minute and shootout display."""
        mid = slot.get("match_id")
        if mid is not None:
            res = results_by_match.get(int(mid))
            if res is not None:
                return "closed", bracket_result_display(res)
        kickoff = slot.get("kickoff_utc") or ""
        try:
            kickoff_date = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).date()
            if kickoff_date == today_utc:
                return "live", None
        except (TypeError, ValueError):
            pass
        return "pending", None

    # Sort by bracket position (not slot_id) so the CSS connectors visually
    # match the actual pairings — see _BRACKET_POSITION_ORDER for the why.
    def _position_key(slot: dict) -> tuple[int, int]:
        stage = slot.get("stage", "")
        order_list = _BRACKET_POSITION_ORDER.get(stage, [])
        slot_id = slot.get("slot_id", "")
        try:
            pos = order_list.index(slot_id)
        except ValueError:
            pos = len(order_list)
        # Stage order: R32 → R16 → QF → SF → Final, third-place stays last.
        stage_rank = list(_BRACKET_STAGE_TO_ROUND.keys()).index(stage) \
            if stage in _BRACKET_STAGE_TO_ROUND else 99
        return (stage_rank, pos)

    slots = sorted(slots, key=_position_key)

    rendered_slots: list[dict] = []
    for slot in slots:
        stage = slot.get("stage", "")
        round_key = _BRACKET_STAGE_TO_ROUND.get(stage)
        if round_key is None:
            continue

        def _team(name: str, pending: bool) -> dict:
            name = name or ""
            return {
                "name": localize_team_name(name, _lang()),
                "crest_html": "" if pending else crest_html(name, size=24),
                "is_placeholder": bool(pending),
            }

        match_id = slot.get("match_id")
        href = f"?page=lab&match_id={int(match_id)}" if match_id else None
        status, result_display = _slot_status_score_winner(slot)
        rendered_slots.append({
            "match_id": slot.get("slot_id", ""),
            "round": round_key,
            "date": _bracket_date_label(slot.get("kickoff_utc")),
            "stadium": slot.get("venue") or "",
            "home": _team(slot.get("home", ""), bool(slot.get("home_pending"))),
            "away": _team(slot.get("away", ""), bool(slot.get("away_pending"))),
            "status": status,
            "score": result_display["score"] if result_display else None,
            "penalty_score": result_display["penalty_score"] if result_display else None,
            "winner": result_display["winner"] if result_display else None,
            "decided_in": result_display["decided_in"] if result_display else None,
            "advances_to": None,
            "href": href,
        })

    st.markdown(render_bracket(rendered_slots, language=_lang()), unsafe_allow_html=True)


@st.fragment
def _render_prediction_workspace(
    match,
    bundle: MatchAnalysisBundle,
    cached,
    repo: Repository,
) -> None:
    team_a, team_b = match.team_a.name, match.team_b.name
    display_team_a, display_team_b = localize_team_name(team_a, _lang()), localize_team_name(team_b, _lang())
    current_players = bundle.current_players
    predictions = bundle.predictions
    score_only_predictions = bundle.score_only_predictions
    primary = bundle.primary
    ml_probabilities = bundle.ml_probabilities
    ml_features = bundle.ml_features
    ml_model_meta = bundle.ml_model_meta
    section = st.segmented_control(
        _t("analysis.view"),
        ["model", "scorelines", "team_stats", "players", "sources", "history"],
        default="model",
        format_func=lambda key: _t(f"analysis.sections.{key}"),
        label_visibility="collapsed",
    )
    deep_ml_probabilities = bundle.deep_ml_probabilities
    deep_weight = bundle.deep_outcome_weight
    if section == "model":
        if match.status == "finished":
            _render_post_match_audit(
                bundle,
                _match_auxiliary_context(match),
                team_a,
                team_b,
                is_knockout=_is_knockout_stage(getattr(match, "stage", None)),
                repo=repo,
                match=match,
            )
        with st.expander(_t("analysis.model_selection")):
            st.caption(_t("analysis.model_selection_help"))
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
            with st.expander(_t("analysis.signal_diagnostics")):
                col_cfg = {
                    "Modelo unificado (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
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
            callout(_t("analysis.ml_unavailable"))

        # Panel completo para partidos de eliminatoria (sustituye al 1X2
        # estándar con avance/vía/cruce + 90' + ET/penaltis). Para grupos
        # devuelve False y caemos al flujo de mercados normales abajo.
        _render_knockout_panel(
            match, bundle, team_a, team_b, repo,
            predictions=predictions,
            primary=primary,
            expected_xg=bundle.expected_xg,
        )

        st.subheader(_b("Resultados estimados", "Estimated outcomes"))
        neutral_predictions = [
            row for row in predictions
            if row.market_name in {
                "1X2", "Exact Score", "Exact Score (favorito)",
                "Exact Score (alt)", "Expected Score",
            }
        ]
        result_columns = (
            {"Market": "Resultado", "Selection": "Selección", "Probability": "Prob.", "Low": "Mín.", "High": "Máx.", "Confidence": "Confianza", "Sample": "Muestra", "Origin": "Origen", "Explanation": "Explicación"}
            if _lang() == "es" else
            {"Market": "Outcome", "Selection": "Selection", "Probability": "Prob.", "Low": "Low", "High": "High", "Confidence": "Confidence", "Sample": "Sample", "Origin": "Origin", "Explanation": "Explanation"}
        )
        frame = pd.DataFrame(prediction_rows(neutral_predictions)).rename(columns=result_columns)
        if "Line" in frame.columns:
            frame = frame.drop(columns=["Line"])
        outcome_column = result_columns["Market"]
        frame[outcome_column] = frame[outcome_column].replace({
            "1X2": _b("Resultado probable", "Likely outcome"),
            "Exact Score": _b("Marcador principal", "Primary scoreline"),
            "Exact Score (favorito)": _b("Marcador condicionado", "Conditional scoreline"),
            "Exact Score (alt)": _b("Marcador alternativo", "Alternative scoreline"),
            "Expected Score": _b("Goles esperados", "Expected goals"),
        })
        selection_column = result_columns["Selection"]
        explanation_column = result_columns["Explanation"]
        frame[selection_column] = frame[selection_column].replace({"Draw": _b("Empate", "Draw")})
        frame[explanation_column] = frame[explanation_column].str.replace(
            "Modelo unificado 1X2", _b("Modelo unificado de resultado", "Unified outcome model"), regex=False
        )
        if _lang() == "en":
            explanation_by_outcome = {
                "Likely outcome": "Unified match-outcome estimate derived from the scoreline distribution, team form, player availability and deep-stat adjustments.",
                "Primary scoreline": "The single most likely scoreline in the joint goal distribution.",
                "Conditional scoreline": "The most likely scoreline conditional on the model's favoured team winning.",
                "Alternative scoreline": "Another high-probability scoreline in the joint goal distribution.",
                "Expected goals": "Expected goals for each team according to the joint goal distribution.",
            }
            frame[explanation_column] = frame[outcome_column].map(explanation_by_outcome).fillna(
                "Model estimate derived from the available match, team and player evidence."
            )
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={
                "Prob.": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=1),
                result_columns["Low"]: st.column_config.NumberColumn(format="%.1f%%"),
                result_columns["High"]: st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        if st.button(_b("Guardar instantánea de predicciones", "Save prediction snapshot"), width="stretch"):
            now = datetime.now(timezone.utc)
            persisted_predictions = [
                row for row in predictions
                if row.market_name in {
                    "1X2", "Exact Score", "Exact Score (favorito)",
                    "Exact Score (alt)", "Expected Score",
                }
            ]
            for row in persisted_predictions:
                repo.add_prediction(match.id, row.market_family.value, row.market_name, row.selection_name, row.line, row.probability, row.confidence.value, now, row.explanation)
            st.success(_b(f"Instantánea guardada: {len(persisted_predictions)} proyecciones.", f"Snapshot saved: {len(persisted_predictions)} projections."))

    elif section == "scorelines":
        _render_exact_score_panel(team_a, team_b, predictions)

    elif section == "team_stats":
        _render_team_statistics(_match_volume_context(match))

    elif section == "players":
        auxiliary = _match_auxiliary_context(match)
        player_context = _player_match_context_cached(
            match.id,
            _db_signature(),
            PREDICTION_ENGINE_VERSION,
            team_a,
            team_b,
            current_players,
            auxiliary,
        )
        if player_context.lineups:
            st.success(_b("Alineación importada para este partido.", "A line-up has been imported for this match."))
            with st.expander(_b("Ver alineación", "View line-up")):
                st.dataframe(
                    _visible_frame(player_context.lineups),
                    width="stretch",
                    hide_index=True,
                )
        else:
            st.info(_b("Alineación no confirmada: las tasas observadas están disponibles, pero la confianza se mantiene baja.", "Line-up not confirmed: observed rates are available, but confidence remains low."))
        st.caption(_b("Elige un jugador, una métrica y un umbral estadístico. La tasa por 90, los minutos y la probabilidad de titularidad se calculan con los datos de jugadores.", "Choose a player, metric and statistical threshold. Rates per 90, expected minutes and starting probability are derived from player data."))
        selected_team = st.segmented_control(
            _b("Equipo", "Team"),
            [team_a, team_b],
            default=team_a,
            format_func=lambda value: localize_team_name(value, _lang()),
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
        for team_name in (selected_team,):
                team_context = player_context.by_team[team_name]
                team_players = team_context.players
                if not team_players:
                    st.warning(_b(f"No hay estadísticas observadas de jugadores de {localize_team_name(team_name, _lang())}.", f"No observed player statistics are available for {team_name}."))
                    continue
                goalkeepers = team_context.goalkeepers
                field_players = team_context.field_players
                # Player roster table: lets the user scan candidates and pick
                # interesting names before opening an individual projection.
                st.markdown(_b(f"**Plantilla disponible de {localize_team_name(team_name, _lang())}**", f"**Available {team_name} squad**"))

                position_filter = st.radio(
                    _b("Filtrar por posición", "Filter by position"),
                    ["all", "outfield", "goalkeepers"],
                    format_func=lambda key: {"all": _b("Todos", "All"), "outfield": _b("Campo", "Outfield"), "goalkeepers": _b("Porteros", "Goalkeepers")}[key],
                    horizontal=True,
                    key=f"roster_pos_{match.id}_{team_name}",
                )
                if position_filter == "outfield":
                    roster_source = field_players
                elif position_filter == "goalkeepers":
                    roster_source = goalkeepers
                else:
                    roster_source = team_players

                if not roster_source:
                    st.info(_b(
                        "No hay jugadores en este filtro." + ("" if goalkeepers else " Todavía no hay porteros con minutos publicados."),
                        "No players match this filter." + ("" if goalkeepers else " No goalkeepers have published minutes yet."),
                    ))
                    continue

                roster_names = {
                    str(row.get("player_name") or "") for row in roster_source
                }
                roster_rows = [
                    row
                    for row in team_context.roster_rows
                    if str(row.get("Jugador") or "") in roster_names
                ]
                roster_frame = pd.DataFrame(roster_rows)
                min_minutes = st.slider(
                    _b("Minutos mínimos para mostrar", "Minimum minutes to display"),
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
                display_roster = visible_roster.rename(columns={
                    "Jugador": _b("Jugador", "Player"),
                    "Posición": _b("Posición", "Position"),
                    "Tiros/90": _b("Tiros/90", "Shots/90"),
                })
                st.dataframe(
                    display_roster, width="stretch", hide_index=True,
                    column_config={k: v for k, v in column_config.items() if k in display_roster.columns},
                )
                st.caption(_b(
                    f"{len(visible_roster)}/{len(roster_frame)} jugadores visibles. Usa esta tabla para explorar las proyecciones individuales del modelo.",
                    f"{len(visible_roster)}/{len(roster_frame)} players shown. Use this table to explore individual model projections.",
                ))
                player_by_name = {str(row["player_name"]): row for row in roster_source}
                selected_player = st.selectbox(
                    _b(f"Jugador de {localize_team_name(team_name, _lang())}", f"{team_name} player"),
                    list(player_by_name),
                    key=f"player_select_{match.id}_{team_name}_{position_filter}",
                )
                player_row = player_by_name[selected_player]
                gk_mode = is_goalkeeper(player_row)
                if gk_mode:
                    # Goalkeeper metrics: saves, goals conceded and clean sheet.
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
                    st.warning(_b("Este jugador tiene minutos, pero no dispone de ninguna métrica predictiva publicada.", "This player has recorded minutes but no published predictive metric."))
                    continue
                family_picker = st.selectbox(
                    _b("Métrica", "Metric"),
                    available_families,
                    format_func=lambda value: localize_market_family(value.value),
                    key=f"player_market_{match.id}_{team_name}_{position_filter}",
                )
                family = family_picker
                # Clean sheet is binary; the threshold is fixed. For the rest
                # the user selects the analytical threshold.
                if family == MarketFamily.PLAYER_CLEAN_SHEET:
                    line = 0.5
                    st.caption(_b("Umbral estadístico fijo: portería a cero.", "Fixed statistical threshold: clean sheet."))
                else:
                    line = st.number_input(
                        _b("Umbral estadístico", "Statistical threshold"),
                        min_value=0.0,
                        value=float(default_lines[family]),
                        step=0.5,
                        key=f"player_line_{match.id}_{team_name}_{family.value}",
                    )
                if family in GOALKEEPER_MARKETS:
                    baseline = team_context.goalkeeper_baseline
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
                            _b(
                                f"Histórico deep de {team_name}: {baseline.sample_matches} partido(s) profundo(s) · save_rate observado {baseline.save_rate:.0%} · banco diario {bank_save_pct:.0%} → mezcla {weight:.0%} deep / {(1-weight):.0%} banco = save% efectivo {save_override:.0%}.",
                                f"Deep-stat history for {team_name}: {baseline.sample_matches} match(es) · observed save rate {baseline.save_rate:.0%} · daily dataset {bank_save_pct:.0%} → blend {weight:.0%} deep stats / {(1-weight):.0%} dataset = effective save rate {save_override:.0%}.",
                            )
                        )
                    elif baseline and baseline.sample_matches == 0:
                        st.caption(
                            _b(
                                f"Histórico deep de {team_name}: aún sin paradas registradas; se usa save% {bank_save_pct:.0%} del banco diario.",
                                f"Deep-stat history for {team_name}: no saves recorded yet; the {bank_save_pct:.0%} save rate from the daily dataset is used.",
                            )
                        )
                    derived = derive_player_assumption(
                        player_row, family,
                        opponent_sot_per90=team_context.opponent_sot_per90,
                        team_save_rate_override=save_override,
                    )
                else:
                    derived = derive_player_assumption(player_row, family)
                if derived is None:
                    st.warning(_b("La fuente no aporta los minutos o la métrica necesarios; no se puede estimar esta proyección.", "The source does not provide the required minutes or metric, so this projection cannot be estimated."))
                    continue
                rate_labels = {
                    MarketFamily.PLAYER_SAVES: _b("Paradas esperadas / 90", "Expected saves / 90"),
                    MarketFamily.PLAYER_GOALS_CONCEDED: _b("Goles concedidos / 90", "Goals conceded / 90"),
                    MarketFamily.PLAYER_CLEAN_SHEET: _b("Goles concedidos esperados / 90", "Expected goals conceded / 90"),
                }
                detail_cols = st.columns(3)
                detail_cols[0].metric(
                    rate_labels.get(family, _b("Tasa observada / 90", "Observed rate / 90")),
                    f"{derived.assumption.per90_rate:.2f}",
                )
                detail_cols[1].metric(_b("Minutos esperados", "Expected minutes"), derived.assumption.expected_minutes)
                detail_cols[2].metric(_b("Probabilidad de titularidad", "Starting probability"), f"{derived.assumption.starter_probability:.0%}")
                st.caption(derived.explanation if _lang() == "es" else "Projection baseline derived from the player's observed rate, expected minutes and starting probability.")
                estimate = estimate_player_projection(
                    derived.assumption, family, line, derived.sample_size
                )
                if estimate.probability is None:
                    st.warning(estimate.explanation if _lang() == "es" else "There is not enough information to estimate this projection.")
                else:
                    projection_cols = st.columns(3)
                    projection_cols[0].metric(_b("Proyección", "Projected total"), f"{estimate.expected_count:.2f}")
                    projection_cols[1].metric(_b("Probabilidad estimada", "Estimated probability"), f"{estimate.probability:.1%}")
                    projection_cols[2].metric(_b("Confianza", "Confidence"), localize_confidence(estimate.confidence.value))
                    st.caption(estimate.explanation if _lang() == "es" else "Model estimate based on the adjusted player baseline and the selected threshold.")

    elif section == "sources":
        st.subheader(_b("Importar estadísticas profundas revisadas", "Import reviewed deep statistics"))
        st.caption(_b("Admite el JSON estructurado obtenido de capturas y conserva su procedencia. No crea sanciones nominales sin identificar al jugador.", "Accepts structured JSON derived from screenshots and preserves its provenance. It never creates player suspensions without identifying the player."))
        deep_upload = st.file_uploader(_b("JSON de estadísticas de partidos", "Match-statistics JSON"), type=["json"], key=f"deep_json_{match.id}")
        reviewed_json = st.checkbox(_b("He comprobado que los equipos y valores corresponden a las capturas", "I have verified that teams and values match the screenshots"), key=f"deep_json_reviewed_{match.id}")
        if st.button(_b("Validar e importar JSON", "Validate and import JSON"), disabled=deep_upload is None or not reviewed_json, key=f"deep_json_import_{match.id}"):
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
                _invalidate_match_analysis_caches()
                st.success(
                    _b(
                        f"Importados {imported_deep.imported_matches}; sin cambios {imported_deep.unchanged_matches}; observaciones {imported_deep.observations}.",
                        f"Imported {imported_deep.imported_matches}; unchanged {imported_deep.unchanged_matches}; observations {imported_deep.observations}.",
                    )
                )
                if imported_deep.ambiguous_matches or imported_deep.unmatched_matches:
                    st.warning(
                        _b(f"Ambiguos: {imported_deep.ambiguous_matches}; sin partido: {imported_deep.unmatched_matches}.", f"Ambiguous: {imported_deep.ambiguous_matches}; unmatched: {imported_deep.unmatched_matches}.")
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
                            _b("Partido", "Match"): _localized_match_label(item),
                            _b("Inicio", "Kick-off"): _display_time(item.kickoff_utc, "%d/%m %H:%M"),
                            _b("Selecciones afectadas", "Teams affected"): ", ".join(
                                team for team in teams_with_new_evidence
                                if same_team(item.team_a.name, team) or same_team(item.team_b.name, team)
                            ),
                        }
                        for item in upcoming[:25]
                    ]
                    st.info(
                        _b(f"Esta evidencia alimenta el modelo (xG, tiros, posesión, córners y tarjetas) para {len(upcoming)} partidos futuros de selecciones cargadas.", f"This evidence feeds the model (xG, shots, possession, corners and cards) for {len(upcoming)} upcoming matches involving the imported teams.")
                    )
                    st.dataframe(pd.DataFrame(affected_rows), width="stretch", hide_index=True)
                else:
                    st.caption(
                        _b("Las estadísticas quedaron persistidas. Aún no hay partidos futuros de estas selecciones en el calendario; se aplicarán automáticamente cuando los haya.", "The statistics have been saved. There are no upcoming matches for these teams yet; they will be applied automatically when fixtures become available.")
                    )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                st.error(_b(f"JSON rechazado: {exc}", f"JSON rejected: {exc}"))
        st.divider()
        if cached:
            if cached.statistics:
                st.subheader(_b("Evidencia normalizada", "Normalised evidence"))
                st.dataframe(_visible_frame(cached.statistics), width="stretch", hide_index=True)
            if cached.lineups:
                st.subheader(_b("Jugadores", "Players"))
                st.dataframe(_visible_frame(cached.lineups), width="stretch", hide_index=True)

    elif section == "history":
        saved_predictions = repo.list_predictions(match.id)
        imports = repo.list_import_runs(match.id)
        if imports:
            st.subheader(_t("analysis.saved_data_history"))
            st.dataframe(_visible_frame(imports), width="stretch", hide_index=True)
        if saved_predictions:
            st.subheader(_t("analysis.saved_predictions"))
            st.dataframe(_visible_frame(saved_predictions), width="stretch", hide_index=True)
        if not imports and not saved_predictions:
            empty_state(_t("analysis.no_saved_predictions"), _t("analysis.no_saved_predictions_body"), icon="📭")



def render_prediction_lab() -> None:
    repo = _repo()
    with st.spinner(_t("analysis.refreshing")):
        daily_result = _refresh_current_world_cup_banks(repo)
    _resolve_bracket_after_daily_refresh(repo, daily_result)
    matches = _list_matches()
    if not matches:
        empty_state(_t("analysis.no_matches"), _t("analysis.no_matches_body"), icon="📅")
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
    selected_label = st.selectbox(_t("analysis.match"), labels, index=preselect_index, label_visibility="collapsed")
    if selected_label not in by_label:
        st.info(_t("analysis.choose_match"))
        return
    match = by_label[selected_label]
    team_a, team_b = match.team_a.name, match.team_b.name
    display_team_a, display_team_b = localize_team_name(team_a, _lang()), localize_team_name(team_b, _lang())
    crest_a = crest_html(team_a, size=44)
    crest_b = crest_html(team_b, size=44)
    title_html = (
        f'<span class="hero-team">{crest_a}<span>{display_team_a}</span></span>'
        f'<span class="hero-vs">vs</span>'
        f'<span class="hero-team">{crest_b}<span>{display_team_b}</span></span>'
    )
    hero(
        f"{match.stage} · {_display_time(match.kickoff_utc, '%d %b %Y · %H:%M')}",
        title_html,
        f"{match.venue or _t('analysis.venue_pending')} · {_t('analysis.local_time')}",
    )

    tone = "green" if daily_result.status in {"current", "updated"} else "amber" if daily_result.status in {"partial", "stale"} else "red"
    st.markdown(
        '<div class="status-row">'
        + status_pill(_t("analysis.world_cup_data", status=localize_status(daily_result.status)), tone)
        + status_pill(_t("analysis.updated", count=len(daily_result.updated)))
        + status_pill(_t("analysis.unchanged", count=len(daily_result.unchanged) + len(daily_result.skipped_recent)))
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
            refresh_clicked = st.button(_t("analysis.refresh"), type="primary", width="stretch")
        with note_col:
            st.caption(_t("analysis.refresh_note"))
    else:
        refresh_clicked = False
    if refresh_clicked:
        with st.spinner(_t("analysis.collecting")):
            result = refresh_match(team_a, team_b, match.kickoff_utc, SPORTS_DATA_DIR)
        st.session_state[cache_key] = result
        if result.bundle is not None:
            repo.import_collector_bundle(match.id, result.bundle)
            cached = result.bundle
            # New bundle data → invalidate cached analysis so the prediction
            # actually reflects the freshly imported evidence.
            _invalidate_match_analysis_caches()
        status_tone = {
            "complete": ("success", _b("Datos del partido completos.", "Match data is complete.")),
            "partial": ("warning", _b("Datos parciales: el modelo usa lo disponible y marca lo que falta.", "Partial data: the model uses what is available and identifies the missing fields.")),
            "cached": ("info", _b("No se pudieron añadir datos nuevos; se conserva la caché previa.", "No new data could be added; the previous cache has been retained.")),
            "failed": ("error", _b("La actualización falló y no había caché previa.", "The refresh failed and no previous cache was available.")),
            "unavailable": ("error", _b("El recolector local no está instalado o accesible.", "The local data collector is not installed or accessible.")),
        }
        tone, default_message = status_tone.get(result.status, ("warning", result.message))
        getattr(st, tone)(default_message)
        metric_cols = st.columns(3)
        metric_cols[0].metric(_b("Llamadas realizadas", "Requests made"), result.calls_made)
        metric_cols[1].metric(_b("Proveedores disponibles", "Providers available"), len(result.providers))
        metric_cols[2].metric(_b("Campos pendientes", "Missing fields"), len(result.missing_critical))
        if result.providers:
            st.caption(_b("Proveedores que respondieron: ", "Providers that responded: ") + ", ".join(result.providers))
        if result.missing_critical:
            st.warning(
                _b("Campos no obtenidos en este partido: ", "Fields unavailable for this match: ")
                + ", ".join(result.missing_critical)
                + _b(". La aplicación no los inventa: aparecerán vacíos en la cobertura.", ". The application does not infer them; they remain blank in the coverage report.")
            )
        if result.stderr_tail:
            with st.expander(_b("Salida técnica del recolector", "Collector technical output")):
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
            _b("No hay evidencia previa ni caché automática suficiente para modelar este partido con confianza.", "There is not enough prior evidence or cached data to model this match with confidence."),
            tone="red", title=_b("Sin datos", "No data"),
        )

    results = bundle.results
    predictions = bundle.predictions
    score_only_predictions = bundle.score_only_predictions
    primary = bundle.primary
    ml_probabilities = bundle.ml_probabilities
    ml_features = bundle.ml_features
    ml_model_meta = bundle.ml_model_meta
    if bundle.corrections is not None and corrections_active(bundle.corrections):
        callout(describe_corrections(bundle.corrections, _lang()), tone="blue", title=_b("Corrección automática activa", "Automatic correction active"))
    if bundle.form_adjustment_note:
        callout(_localized_form_adjustment_note(bundle.form_adjustment_note), tone="blue", title=_b("Ajuste por forma del torneo activo", "Active tournament-form adjustment"))
    knockout_prediction = _knockout_prediction_for_match(match, bundle, repo)
    try:
        _persist_pre_match_snapshot(match, bundle, repo, knockout_prediction)
    except Exception:
        # Snapshot persistence must never block prediction rendering.
        pass
    is_knockout = knockout_prediction is not None
    best = max(primary, key=lambda row: row.probability)
    exact_score = next(row for row in predictions if row.market_name == "Exact Score")
    alt_scores = [row for row in predictions if row.market_name == "Exact Score (alt)"]
    expected_row = next(
        (row for row in predictions if row.market_name == "Expected Score"),
        None,
    )
    home_p = next((row.probability for row in primary if row.selection_name == team_a), 0)
    draw_p = next((row.probability for row in primary if row.selection_name == "Draw"), 0)
    away_p = next((row.probability for row in primary if row.selection_name == team_b), 0)

    if is_knockout:
        # Knockout: dedicated panel (badge + advance card + conditional funnel)
        # replaces the standard "Probabilidad 1X2 / Lectura inmediata" header.
        stage_label = getattr(match, "stage", None) or _b("Eliminatoria", "Knockout")
        if _lang() == "en":
            stage_label = {
                "Eliminatoria": "Knockout",
                "Dieciseisavos": "Round of 32",
                "Octavos": "Round of 16",
                "Cuartos de final": "Quarter-final",
                "Semifinal": "Semi-final",
                "Tercer puesto": "Third-place play-off",
                "Final": "Final",
            }.get(stage_label, stage_label)
        st.markdown(knockout_badge_html(stage_label, _b("ELIMINATORIA", "KNOCKOUT")), unsafe_allow_html=True)
        next_fixture = _find_next_knockout_fixture(repo, match.id)
        next_caption = (
            _b(f"Cruce siguiente para el ganador: {next_fixture}", f"Winner's next fixture: {next_fixture}")
            if next_fixture else None
        )
        penalty_context = _penalty_match_context(match)
        st.markdown(
            knockout_advance_html(
                team_a=display_team_a,
                team_b=display_team_b,
                home_advances=knockout_prediction.home_advances,
                away_advances=knockout_prediction.away_advances,
                home_wins_90=knockout_prediction.home_wins_90,
                draw_90=knockout_prediction.p_draw_90,
                away_wins_90=knockout_prediction.away_wins_90,
                cond_home_et=knockout_prediction.cond_home_wins_et_given_draw_90,
                cond_draw_et=knockout_prediction.cond_draw_after_et_given_draw_90,
                cond_away_et=knockout_prediction.cond_away_wins_et_given_draw_90,
                cond_home_pen=knockout_prediction.cond_home_wins_penalties_given_draw_after_et,
                cond_away_pen=knockout_prediction.cond_away_wins_penalties_given_draw_after_et,
                crest_a=crest_html(team_a, size=28),
                crest_b=crest_html(team_b, size=28),
                next_fixture=next_caption,
                pen_pending=penalty_context.simulations == 0,
                language=_lang(),
            ),
            unsafe_allow_html=True,
        )
        # Compact "Lectura inmediata" inline below the advance card.
        meta_cols = st.columns(3)
        advancing_team = display_team_a if knockout_prediction.home_advances >= knockout_prediction.away_advances else display_team_b
        advancing_probability = max(knockout_prediction.home_advances, knockout_prediction.away_advances)
        meta_cols[0].metric(_t("knockout.qualifies"), advancing_team, f"{advancing_probability:.1%}")
        meta_cols[1].metric(_t("summary.most_likely_score"), exact_score.selection_name, f"{exact_score.probability:.1%}")
        if expected_row is not None:
            meta_cols[2].metric(
                _t("summary.expected_score"),
                expected_row.selection_name,
                help=_t("summary.expected_score_help"),
            )
        if alt_scores:
            alt_lines = " · ".join(
                f"{row.selection_name.split(' ')[0]} ({row.probability:.1%})"
                for row in alt_scores[:3]
            )
            st.caption(_t("summary.alternatives", scores=alt_lines))

        # xG por fase (90' + prórroga) y contexto de penaltis: van AQUÍ
        # integrados con el panel KO, no como sección separada abajo.
        if bundle.expected_xg and len(bundle.expected_xg) == 2:
            xa, xb = float(bundle.expected_xg[0]), float(bundle.expected_xg[1])
            et_a, et_b = xa * 0.30, xb * 0.30
            xg_cols = st.columns(4)
            xg_cols[0].metric(f"xG {display_team_a} (90')", f"{xa:.2f}")
            xg_cols[1].metric(f"xG {display_team_b} (90')", f"{xb:.2f}")
            xg_cols[2].metric(_b(f"xG {display_team_a} (prórroga)", f"xG {display_team_a} (extra time)"), f"{et_a:.2f}", help=_b("xG_90 × 0.30 (30 min de tiempo extra)", "xG_90 × 0.30 (30 minutes of extra time)"))
            xg_cols[3].metric(_b(f"xG {display_team_b} (prórroga)", f"xG {display_team_b} (extra time)"), f"{et_b:.2f}", help=_b("xG_90 × 0.30 (30 min de tiempo extra)", "xG_90 × 0.30 (30 minutes of extra time)"))
        if getattr(penalty_context, "explanation", ""):
            penalty_explanation = penalty_context.explanation
            if _lang() == "en":
                keeper_note = ""
                if len(penalty_context.goalkeeper_rows) >= 2:
                    keeper_note = f" with fixed starting goalkeepers ({penalty_context.goalkeeper_rows[0].player_name} and {penalty_context.goalkeeper_rows[1].player_name})"
                if penalty_context.simulations:
                    penalty_explanation = (
                        f"{penalty_context.simulations:,} pre-match scenarios{keeper_note}, with outfield substitutions based on role and match state; "
                        f"shootout win probability for {team_a}: {penalty_context.team_a_shootout_win_probability:.1%}. "
                        f"Penalty-history coverage: {penalty_context.coverage.players_with_history}/{penalty_context.coverage.squad_players} players."
                    )
                else:
                    penalty_explanation = (
                        f"Stored historical penalties: {team_a} {penalty_context.team_a.scored}/{penalty_context.team_a.attempts} "
                        f"({penalty_context.team_a.conversion:.0%} adjusted), {team_b} {penalty_context.team_b.scored}/{penalty_context.team_b.attempts} "
                        f"({penalty_context.team_b.conversion:.0%} adjusted). Shootout win probability for {team_a}: "
                        f"{penalty_context.team_a_shootout_win_probability:.1%}. Detailed calculation will be available after the group-stage precomputation."
                    )
            callout(_localize_team_mentions(penalty_explanation, team_a, team_b), tone="blue", title=_t("knockout.penalty_context"))
        if penalty_context.goalkeeper_rows:
            st.markdown(_b("**Porteros titulares usados**", "**Starting goalkeepers used**"))
            st.dataframe(
                pd.DataFrame([
                    {
                        _b("Selección", "Team"): localize_team_name(row.team_name, _lang()),
                        _b("Portero", "Goalkeeper"): row.player_name,
                        _b("Probabilidad de parada", "Save probability"): row.penalty_save_probability * 100,
                        _b("Penaltis afrontados", "Penalties faced"): row.faced_penalties,
                        _b("Paradas", "Saves"): row.saves,
                        _b("Goles", "Goals"): row.goals,
                        _b("Fuera/poste", "Off target/woodwork"): row.off_target_attempts,
                        _b("En partido", "In-match"): row.regular_attempts,
                        _b("En tanda", "Shootout"): row.shootout_attempts,
                        _b("Fuente", "Source"): row.source,
                    }
                    for row in penalty_context.goalkeeper_rows
                ]),
                width="stretch",
                hide_index=True,
                column_config={
                    _b("Probabilidad de parada", "Save probability"): st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0, max_value=100,
                    )
                },
            )
        if penalty_context.shootout_coverage_rows:
            st.markdown(_b("**Tandas recientes cubiertas**", "**Recent shootouts covered**"))
            st.dataframe(
                pd.DataFrame([
                    {
                        _b("Selección", "Team"): localize_team_name(row.team_name, _lang()),
                        _b("Competiciones revisadas", "Competitions reviewed"): (
                            " · ".join(row.competitions) if row.competitions
                            else _b("Sin cobertura verificada", "No verified coverage")
                        ),
                        _b("Lanzamientos de tanda", "Shootout attempts"): row.shootout_attempts,
                    }
                    for row in penalty_context.shootout_coverage_rows
                ]),
                width="stretch",
                hide_index=True,
            )
        if penalty_context.data_cutoff:
            st.caption(
                _b(f"Corte de datos: {penalty_context.data_cutoff} · Modelo: {penalty_context.model_version}.", f"Data cutoff: {penalty_context.data_cutoff} · Model: {penalty_context.model_version}.")
            )
        if penalty_context.player_rows:
            st.markdown(_b("**Probables al minuto 120**", "**Likely to be on the pitch at 120 minutes**"))
            penalty_rows = [
                {
                    _b("Jugador", "Player"): row.player_name,
                    _b("Selección", "Team"): localize_team_name(row.team_name, _lang()),
                    _b("Rol", "Role"): row.role,
                    _b("Probable al minuto 120", "On pitch at 120 minutes"): row.on_field_probability * 100,
                    _b("Prob. entre los 5 primeros", "First-five probability"): row.first_five_probability * 100,
                    _b("Conversión estimada", "Estimated conversion"): row.conversion * 100,
                    _b("Penaltis registrados", "Recorded penalties"): row.attempts,
                    _b("Confianza", "Confidence"): localize_confidence(row.confidence),
                }
                for row in penalty_context.player_rows
                if row.on_field_probability >= 0.02
            ]
            st.dataframe(
                pd.DataFrame(penalty_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    label: st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0, max_value=100,
                    )
                    for label in (
                        _b("Probable al minuto 120", "On pitch at 120 minutes"),
                        _b("Prob. entre los 5 primeros", "First-five probability"),
                        _b("Conversión estimada", "Estimated conversion"),
                    )
                },
            )
            coverage = penalty_context.coverage
            st.caption(
                _b("Cobertura del historial de penaltis: ", "Penalty-history coverage: ")
                + f"{coverage.players_with_history}/{coverage.squad_players} "
                + _b("jugadores", "players")
                + f" · {coverage.attempts} " + _b("penaltis", "penalties")
                + f" · " + _b("error Monte Carlo", "Monte Carlo error") + f" ±{1.96 * penalty_context.standard_error:.2%}."
            )
            with st.expander(_b("Supuestos de sustituciones y tanda", "Substitution and shootout assumptions")):
                st.write(
                    _b(
                        "Simulación prepartido condicionada a empate tras 120 minutos: cinco cambios reglamentarios, uno adicional en prórroga, cambios preferentemente por roles próximos y ajuste ofensivo/defensivo según marcador.",
                        "Pre-match simulation conditional on a draw after 120 minutes: five standard substitutions, one additional change in extra time, role-compatible replacements and attacking or defensive adjustments based on the match state.",
                    )
                )
                st.write(
                    _b(
                        "Solo lanzan los futbolistas que siguen en el campo. Los cinco primeros se eligen sin reemplazo; en muerte súbita nadie repite hasta completar los once.",
                        "Only players still on the pitch may take a penalty. The first five are selected without replacement; in sudden death nobody repeats until all eleven have taken one.",
                    )
                )

        with st.expander(_t("summary.calculation")):
            st.caption(_localize_team_mentions(best.explanation, team_a, team_b))
        if best.confidence.value == "low":
            st.warning(_t("summary.low_confidence"))
    else:
        top_left, top_right = st.columns([1.55, 1])
        with top_left:
            st.subheader(_t("summary.outcome_probabilities"))
            section_note(_t("summary.model_note"))
            bars_html = (
                probability_bar(team_with_crest_html(team_a, size=18), home_p, "win")
                + probability_bar(_t("summary.draw"), draw_p, "draw")
                + probability_bar(team_with_crest_html(team_b, size=18), away_p, "loss")
            )
            st.markdown(bars_html, unsafe_allow_html=True)
        with top_right:
            st.subheader(_t("summary.quick_read"))
            st.metric(_t("summary.most_likely_outcome"), localize_selection(best.selection_name), f"{best.probability:.1%}")
            st.metric(_t("summary.most_likely_score"), exact_score.selection_name, f"{exact_score.probability:.1%}")
            if expected_row is not None:
                st.metric(
                    _t("summary.expected_score"),
                    expected_row.selection_name,
                    help=_t("summary.expected_score_help"),
                )
            if alt_scores:
                alt_lines = " · ".join(
                    f"{row.selection_name.split(' ')[0]} ({row.probability:.1%})"
                    for row in alt_scores[:3]
                )
                st.caption(_t("summary.alternatives", scores=alt_lines))
            short_explanation = best.explanation.split("Ajuste de jugadores:", 1)[0].strip()
            st.caption(short_explanation)
            with st.expander(_t("summary.calculation")):
                st.caption(best.explanation)
            if best.confidence.value == "low":
                st.warning(_t("summary.low_confidence"))

    _render_prediction_workspace(match, bundle, cached, repo)

def _render_global_bias_panel() -> None:
    """Show the model's systematic biases across all matches with deep stats,
    independent of which teams are involved.

    Heavy: each refresh reconstructs N predictions. Therefore it sits inside a
    collapsed expander and only runs when the user opts in via the button (or
    the toggle for auto-corrections is already on).
    """
    st.subheader(_b("Sesgo global del modelo", "Overall model bias"))
    auto_corrections_on = bool(st.session_state.get("apply_corrections", False))
    with st.expander(
        _b("Recalcular informe de calibración (proceso intensivo)", "Recalculate calibration report (resource-intensive)"),
        expanded=auto_corrections_on,
    ):
        st.caption(
            _b("Reconstruye la predicción de cada partido cerrado con estadísticas profundas "
            "usando SOLO datos anteriores a su kickoff y compara con lo real. "
            "Tarda unos segundos por la cantidad de partidos. Sólo es necesario "
            "cuando quieras revisar el sesgo o activar la corrección automática.",
            "Rebuilds each completed match prediction from deep statistics using only data available before kick-off, then compares it with the observed result. It may take several seconds and is only needed to review bias or enable automatic correction.")
        )
        run = st.button(
            _b("Calcular informe ahora", "Calculate report now"), key="run_bias_report", type="primary"
        )
        if not run and not auto_corrections_on:
            st.info(
                _b("Informe no calculado. Pulsa el botón para generarlo o activa la corrección automática para mantenerlo al día.", "The report has not been calculated. Use the button to generate it, or enable automatic correction to keep it current.")
            )
            return
        samples, report = _calibration_bias_report()
    if report.sample_size == 0:
        st.info(report.notes[0] if report.notes else _b("Sin datos.", "No data."))
        return
    cols = st.columns(4)
    cols[0].metric(_b("Partidos auditados", "Matches audited"), report.sample_size)
    cols[1].metric(_b("Precisión del resultado (máxima probabilidad)", "Outcome accuracy (highest probability)"), f"{report.outcome_accuracy:.0%}")
    if report.xg_bias_per_team is not None:
        cols[2].metric(
            _b("Sesgo xG /equipo", "xG bias / team"),
            f"{report.xg_bias_per_team:+.2f}",
            help=_b(f"MAE {report.xg_mean_absolute_error:.2f}. Positivo = el modelo sobreestima.", f"MAE {report.xg_mean_absolute_error:.2f}. Positive values mean the model overestimates."),
        )
    if report.total_goals_bias is not None:
        cols[3].metric(
            _b("Sesgo total de goles", "Total-goals bias"),
            f"{report.total_goals_bias:+.2f}",
            help=_b(f"MAE {report.total_goals_mae:.2f}. Positivo = el modelo sobreestima.", f"MAE {report.total_goals_mae:.2f}. Positive values mean the model overestimates."),
        )
    # 1X2 calibration table.
    st.markdown(_b("**Calibración de resultados (frecuencia real frente a media predicha)**", "**Outcome calibration (observed frequency vs mean prediction)**"))
    cal_rows = [
        {_b("Resultado", "Outcome"): _b("Local", "Home"), _b("Media predicha", "Mean prediction"): f"{report.home_predicted_avg:.1%}",
         _b("Frecuencia real", "Observed frequency"): f"{report.home_actual_frequency:.1%}",
         _b("Diferencia (pp)", "Gap (pp)"): f"{(report.home_predicted_avg - report.home_actual_frequency)*100:+.1f}"},
        {_b("Resultado", "Outcome"): _b("Empate", "Draw"), _b("Media predicha", "Mean prediction"): f"{report.draw_predicted_avg:.1%}",
         _b("Frecuencia real", "Observed frequency"): f"{report.draw_actual_frequency:.1%}",
         _b("Diferencia (pp)", "Gap (pp)"): f"{(report.draw_predicted_avg - report.draw_actual_frequency)*100:+.1f}"},
        {_b("Resultado", "Outcome"): _b("Visitante", "Away"), _b("Media predicha", "Mean prediction"): f"{report.away_predicted_avg:.1%}",
         _b("Frecuencia real", "Observed frequency"): f"{report.away_actual_frequency:.1%}",
         _b("Diferencia (pp)", "Gap (pp)"): f"{(report.away_predicted_avg - report.away_actual_frequency)*100:+.1f}"},
    ]
    st.dataframe(pd.DataFrame(cal_rows), width="stretch", hide_index=True)
    # Favourites calibration.
    if report.favourites_calibration:
        fav_rows = [
            {_b("Confianza del modelo", "Model confidence"): label, "N": data["n"],
             _b("Media predicha", "Mean prediction"): f"{data['predicted']:.0%}",
             _b("Acertados realmente", "Observed accuracy"): f"{data['actual']:.0%}",
             _b("Diferencia (pp)", "Gap (pp)"): f"{(data['predicted'] - data['actual'])*100:+.1f}"}
            for label, data in report.favourites_calibration.items()
        ]
        st.markdown(_b("**Cuando el modelo identifica un favorito, ¿acierta?**", "**How often is the model's favourite correct?**"))
        st.dataframe(pd.DataFrame(fav_rows), width="stretch", hide_index=True)
    if report.notes:
        callout(
            _b("Sesgos significativos detectados:<br>• ", "Significant biases detected:<br>• ") + "<br>• ".join(report.notes),
            tone="amber", title=_b("Sesgos detectados", "Biases detected"),
        )
    else:
        callout(_b("Sin sesgos significativos detectados con la muestra actual.", "No significant bias was detected in the current sample."), tone="green")

    # Bayesian-shrunk auto-correction toggle.
    st.markdown(_b("**Corrección automática del modelo**", "**Automatic model correction**"))
    preview = derive_corrections(report)
    st.caption(describe_corrections(preview, _lang()))
    apply_toggle = st.toggle(
        _b("Aplicar la corrección a todas las predicciones", "Apply correction to all predictions"),
        key="apply_corrections",
        help=_b(
            "Aplica un shrinkage bayesiano: con muestra pequeña sólo aplica una fracción del sesgo medido, y se acerca al sesgo completo conforme se acumulan más partidos. El cambio invalida la caché y se refleja en el análisis predictivo.",
            "Uses Bayesian shrinkage: a small sample applies only part of the measured bias, moving towards the full correction as more matches accumulate. Changing this setting invalidates the cache and updates the predictive analysis.",
        ),
    )
    if apply_toggle and not corrections_active(preview):
        st.info(
            _b("El ajuste está activado, pero ningún parámetro supera los umbrales mínimos; no se está aplicando ninguna corrección.", "The setting is enabled, but no parameter exceeds the minimum thresholds, so no correction is being applied.")
        )
    with st.expander(_b("Ver muestras individuales auditadas", "View individual audited samples")):
        rows = [{
            _b("Partido", "Match"): f"{s.team_a} vs {s.team_b}",
            _b("Inicio", "Kick-off"): _display_time(s.kickoff_utc, "%d/%m %H:%M"),
            _b("Pred. local", "Home pred."): f"{s.predicted_1x2['home']:.0%}",
            _b("Pred. empate", "Draw pred."): f"{s.predicted_1x2['draw']:.0%}",
            _b("Pred. visitante", "Away pred."): f"{s.predicted_1x2['away']:.0%}",
            _b("Real", "Actual"): s.actual_outcome,
            _b("xG pred. A", "Pred. xG A"): f"{s.predicted_xg_a:.2f}" if s.predicted_xg_a else "—",
            _b("xG real A", "Actual xG A"): f"{s.actual_xg_a:.2f}" if s.actual_xg_a else "—",
            _b("xG pred. B", "Pred. xG B"): f"{s.predicted_xg_b:.2f}" if s.predicted_xg_b else "—",
            _b("xG real B", "Actual xG B"): f"{s.actual_xg_b:.2f}" if s.actual_xg_b else "—",
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
    hero(_t("calibration.eyebrow"), _t("calibration.title"), _t("calibration.supporting"))
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
            tone="amber", title=_t("calibration.pending"),
        )
    now_utc = datetime.now(timezone.utc)
    status_by_id: dict[int, dict] = {}
    for item in matches:
        info = all_statuses.get(item.id, {})
        has_result = bool(info.get("has_result"))
        has_stats = bool(info.get("has_team_statistics") or info.get("deep_observations"))
        future = item.kickoff_utc > now_utc
        if future and not has_result and not has_stats:
            tag, label_word = "⏭️", _b("Sin jugar", "Not played")
        elif has_result and has_stats:
            tag, label_word = "✅", _b("Completo", "Complete")
        elif has_stats and not has_result:
            tag, label_word = "🟡", _b("Falta marcador", "Score missing")
        elif has_result and not has_stats:
            tag, label_word = "📊", _b("Falta estadísticas", "Statistics missing")
        else:
            tag, label_word = "🔴", _b("Faltan stats y marcador", "Score and statistics missing")
        status_by_id[item.id] = {"tag": tag, "label": label_word, "has_result": has_result, "has_stats": has_stats}
    annotated_labels = [
        f"{status_by_id[item.id]['tag']} {_display_time(item.kickoff_utc, '%d %b · %H:%M')} — {_localized_match_label(item)}  ·  {status_by_id[item.id]['label']}"
        for item in matches
    ]
    by_annotated = dict(zip(annotated_labels, matches))
    st.caption(_t("calibration.legend"))
    label = st.selectbox(_t("calibration.match"), annotated_labels)
    match = by_annotated[label]
    st.subheader(_t("calibration.close"))
    existing_result = repo.get_match_result(match.id)
    evidence_status = all_statuses.get(match.id, repo.get_match_evidence_status(match.id))
    has_result = bool(evidence_status.get("has_result"))
    has_stats = bool(evidence_status.get("has_team_statistics") or evidence_status.get("deep_observations"))
    status_cols = st.columns(3)
    status_cols[0].metric(_t("calibration.deep_stats"), evidence_status["deep_observations"])
    status_cols[1].metric(_t("calibration.teams_with_stats"), evidence_status["team_stat_rows"])
    status_cols[2].metric(_t("calibration.final_score"), _t("calibration.saved") if has_result else _t("calibration.pending_value"))
    if has_stats and has_result:
        callout(
            _b("Partido completo: estadísticas importadas y marcador guardado. Las predicciones compatibles ya se han evaluado y todo alimenta la forma de partidos posteriores.", "Match complete: statistics imported and final score saved. Compatible predictions have been evaluated and the data now informs subsequent matches."),
            tone="green", title=_b("Partido completo", "Match complete"),
        )
    elif has_stats and not has_result:
        callout(
            _b("Estadísticas importadas, pero falta el marcador final. Guárdalo abajo para evaluar las predicciones y calcular Brier.", "Statistics have been imported, but the final score is missing. Save it below to evaluate predictions and calculate the Brier score."),
            tone="amber", title=_b("Falta marcador", "Score missing"),
        )
    elif has_result and not has_stats:
        callout(
            _b("Marcador guardado, pero faltan estadísticas profundas. Importa el JSON revisado o complétalas manualmente para enriquecer la forma de los próximos partidos.", "The score has been saved, but deep statistics are missing. Import the reviewed JSON or complete them manually to improve form estimates for upcoming matches."),
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
            "resumen_del_partido": _b("Resumen del partido", "Match summary"),
            "ataque": _b("Ataque", "Attacking"),
            "defensa": _b("Defensa", "Defending"),
            "duelos": _b("Duelos", "Duels"),
            "pases": _b("Pases", "Passing"),
            "tiros": _b("Tiros", "Shooting"),
            "porteria": _b("Portería", "Goalkeeping"),
            "otros": _b("Otros", "Other"),
        }
        unique_metric_count = len({row["metric"] for row in team_observations})
        with st.expander(
            _b(f"Ver todas las estadísticas profundas ({unique_metric_count} métricas por equipo)", f"View all deep statistics ({unique_metric_count} metrics per team)")
        ):
            st.caption(
                _b("Todas las métricas presentes en el JSON de estadísticas profundas importado, una columna por equipo. "
                "Las métricas estructuradas se usan ya en el modelo; el resto "
                "alimenta la auditoría y futuras extensiones.",
                "Every metric in the imported deep-statistics JSON, with one column per team. Structured metrics already feed the model; the remainder supports auditing and future extensions.")
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
                        _b("Métrica", "Metric"): metric_short.replace("_", " "),
                        team_a_name: by_team.get(team_a_name),
                        team_b_name: by_team.get(team_b_name),
                    })
                st.markdown(_b(f"**{category_labels.get(category, category)}** ({len(pivot)} métricas)", f"**{category_labels.get(category, category)}** ({len(pivot)} metrics)"))
                st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
    st.caption(_b("La tabla siguiente contiene solo campos de equipo ausentes. Puedes añadir filas de jugador u otras métricas; las vacías no se guardan.", "The table below contains only missing team fields. You may add player rows or other metrics; blank rows are not saved."))
    stats_by_team = {row["team_name"]: row for row in existing_stats}
    team_names = (match.team_a.name, match.team_b.name)
    settlement_rows = []
    for team_name in team_names:
        for metric in ("shots", "shots_on_target", "corners", "yellow_cards", "possession"):
            if stats_by_team.get(team_name, {}).get(metric) is None:
                settlement_rows.append({"subject_type": "team", "subject_name": team_name, "metric": metric, "value_number": None, "value_text": "", "unit": "match", "sample_size": 1})
    settlement_columns = ["subject_type", "subject_name", "metric", "value_number", "value_text", "unit", "sample_size"]
    if _is_knockout_stage(getattr(match, "stage", None)):
        knockout_settlement_id = render_knockout_settlement(
            repo,
            match,
            DATA_DIR / "evidence" / "reviewed-json",
            reviewed_batch_id,
        )
        submit_settlement = knockout_settlement_id is not None
        rows = []
        recorded_at = datetime.now(timezone.utc)
    else:
        with st.form(key=f"settlement_form_{match.id}", clear_on_submit=False):
            score_a, score_b = st.columns(2)
            goals_a = score_a.number_input(
                _b(f"Goles · {match.team_a.name}", f"Goals · {match.team_a.name}"), 0, 20,
                int(existing_result["goals_a"]) if existing_result else 0,
            )
            goals_b = score_b.number_input(
                _b(f"Goles · {match.team_b.name}", f"Goals · {match.team_b.name}"), 0, 20,
                int(existing_result["goals_b"]) if existing_result else 0,
            )
            settlement_stats = st.data_editor(
                pd.DataFrame(settlement_rows, columns=settlement_columns),
                hide_index=True, width="stretch", num_rows="dynamic",
                key=f"settlement_{match.id}",
            )
            st.caption(
                _b("Las tarjetas individuales se importan automáticamente desde el banco diario de jugadores (Actualizar datos); ya no hace falta asignarlas a mano aquí. Si un jugador acumula dos amarillas o ve una roja, la sanción para el siguiente partido se genera al guardar.", "Individual cards are imported automatically from the daily player dataset (Refresh data), so they no longer need to be assigned manually here. If a player accumulates two yellow cards or receives a red card, the next-match suspension is generated when you save.")
            )
            submit_settlement = st.form_submit_button(
                _b("Guardar resultado, estadísticas y recalibrar", "Save result, statistics and recalibrate"), type="primary", width="stretch"
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
    if submit_settlement:
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
            st.info(_b(f"Sanciones automáticas generadas: {created_suspensions}.", f"Automatic suspensions generated: {created_suspensions}."))
        st.success(_b("Partido cerrado. Predicciones compatibles evaluadas y forma disponible para partidos posteriores.", "Match completed. Compatible predictions have been evaluated and the updated form is available for subsequent matches."))
    predictions = repo.list_predictions(match.id)
    backtests = repo.list_backtests(match.id)
    c1, c2, c3 = st.columns(3)
    c1.metric(_t("calibration.snapshots"), len(predictions))
    c2.metric(_t("calibration.evaluations"), len(backtests))
    c3.metric(_t("calibration.mean_brier"), f"{sum(row['brier_score'] for row in backtests if row['brier_score'] is not None) / len(backtests):.3f}" if backtests else "—")
    if not predictions:
        empty_state(_t("calibration.no_snapshots"), _t("calibration.no_snapshots_body"), icon="📊")
        return
    options = {f"#{row['id']} · {row['market_name']} · {row['selection_name']} · p={row['probability']:.1%}": row for row in predictions}
    chosen_label = st.selectbox(_t("calibration.saved_prediction"), list(options))
    chosen = options[chosen_label]
    occurred = st.checkbox(_t("calibration.occurred"))
    if st.button(_t("calibration.save_evaluation"), type="primary"):
        score = brier_score(float(chosen["probability"]), occurred)
        repo.add_backtest(int(chosen["id"]), 1.0 if occurred else 0.0, score, occurred, datetime.now(timezone.utc))
        st.success(_b(f"Evaluación guardada · Brier {score:.4f}", f"Evaluation saved · Brier {score:.4f}"))
        backtests = repo.list_backtests(match.id)
    if backtests:
        st.dataframe(_visible_frame(backtests), width="stretch", hide_index=True)
        bands = calibration_bands([(float(row["probability"]), bool(row["hit"])) for row in backtests], band_size=0.2)
        chart_rows = [{"Banda": key, "Predicha": value["avg_probability"], "Observada": value["hit_rate"], "N": value["count"]} for key, value in bands.items()]
        st.subheader(_t("calibration.by_band"))
        st.dataframe(pd.DataFrame(chart_rows), width="stretch", hide_index=True)
        family = summarize_by_market_family(backtests)
        st.subheader(_t("calibration.reliability"))
        family_rows = [{"Familia": name, **values} for name, values in family.items()]
        st.dataframe(pd.DataFrame(family_rows), width="stretch", hide_index=True)
        st.caption(_b("Menos de 20 evaluaciones por familia se etiqueta como provisional y no aumenta la confianza del modelo.", "Fewer than 20 evaluations in a family are labelled provisional and do not increase model confidence."))
        drift = calibration_drift(backtests)
        if drift:
            st.subheader(_t("calibration.drift"))
            st.line_chart(pd.DataFrame(drift).set_index("evaluated_at_utc")["cumulative_brier"])


def render_data_quality() -> None:
    hero(_t("quality.eyebrow"), _t("quality.title"), _t("quality.supporting"))
    repo = _repo()
    repo.sync_source_catalog(default_source_catalog(), datetime.now(timezone.utc))
    st.subheader(_t("quality.freshness"))
    section_note(_t("quality.freshness_note"))
    freshness_rows = _freshness_rows_now()
    if freshness_rows:
        st.dataframe(pd.DataFrame(freshness_rows), width="stretch", hide_index=True)
    else:
        empty_state(_t("quality.no_sources"), _t("quality.no_sources_body"), icon="🗄️")
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
                _b("Partido", "Match"): _localized_match_label(match),
                _b("Fecha", "Date"): _display_time(match.kickoff_utc, "%d/%m %H:%M"),
                _b("Cobertura", "Coverage"): _coverage_status(bundle)[0],
                _b("Estadísticas", "Statistics"): (len(bundle.statistics) if bundle else 0) + deep_count,
                _b("Jugadores disponibles", "Players available"): match_player_count,
                _b("Alineación", "Line-up"): _b("Confirmada", "Confirmed") if bundle and bundle.lineups else _b("No confirmada", "Not confirmed"),
                _b("Última captura", "Latest capture"): _display_time(bundle.updated_at_utc, "%d/%m %H:%M") if bundle else "—",
                _b("Importado", "Imported"): _b("Sí", "Yes") if import_flags.get(match.id) else _b("No", "No"),
                _b("Faltantes", "Missing"): ", ".join(missing) if missing else _b("Ninguno crítico", "Nothing critical"),
            }
        )
    frame = pd.DataFrame(rows)
    coverage_filter = st.multiselect(
        _t("quality.coverage_filter"),
        sorted(frame[_b("Cobertura", "Coverage")].unique()),
        default=[],
        placeholder=_t("quality.coverage_placeholder"),
    )
    if coverage_filter:
        frame = frame[frame[_b("Cobertura", "Coverage")].isin(coverage_filter)]
    st.dataframe(frame, width="stretch", hide_index=True)
    st.subheader(_t("quality.manual"))
    labels, by_label = _match_labels(matches)
    selected_label = st.selectbox(_t("quality.match_to_correct"), labels)
    if selected_label not in by_label:
        st.info(_b("Selecciona un partido de la lista (no un separador).", "Select a match from the list, not a section divider."))
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
    if st.button(_t("quality.save_manual"), width="stretch"):
        repo.save_manual_observations(
            selected.id,
            edited.to_dict("records"),
            datetime.now(timezone.utc),
        )
        st.success(_b("Correcciones guardadas con fuente manual y marca temporal.", "Corrections saved with a manual source and timestamp."))
    st.subheader(_t("quality.context"))
    st.caption(_b("Solo una incidencia nominal revisada afecta a la disponibilidad. Las tarjetas agregadas no identifican por sí solas al jugador.", "Only a reviewed, player-specific incident affects availability. Aggregate card totals do not identify a player on their own."))
    c1, c2 = st.columns(2)
    context_team = c1.selectbox(_b("Selección afectada", "Affected team"), [selected.team_a.name, selected.team_b.name], format_func=lambda value: localize_team_name(value, _lang()), key="context_team")
    event_label = c2.selectbox(
        _b("Tipo de incidencia", "Incident type"),
        ["suspension_red", "suspension_yellows", "injury", "illness", "coach_change"],
        format_func=lambda value: {
            "suspension_red": _b("Sanción por roja", "Red-card suspension"),
            "suspension_yellows": _b("Sanción por amarillas", "Yellow-card suspension"),
            "injury": _b("Lesión", "Injury"),
            "illness": _b("Enfermedad", "Illness"),
            "coach_change": _b("Cambio de entrenador", "Manager change"),
        }[value],
        key="context_event_type",
    )
    player_name = st.text_input(_b("Jugador (obligatorio salvo cambio de entrenador)", "Player (required unless this is a manager change)"), key="context_player_name")
    source_reference = st.text_input(_b("Fuente o referencia revisada", "Reviewed source or reference"), key="context_source")
    incident_notes = st.text_area(_b("Notas de la incidencia", "Incident notes"), key="context_notes")
    if st.button(_b("Guardar incidencia de plantilla", "Save squad incident"), key="save_context_event"):
        event_type = event_label
        if event_type != "coach_change" and not player_name.strip():
            st.error(_b("Debes identificar al jugador para aplicar una ausencia.", "Identify the player before recording an absence."))
        elif not source_reference.strip():
            st.error(_b("Debes indicar una fuente o referencia revisada.", "Provide a reviewed source or reference."))
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
            st.success(_b("Incidencia guardada y aplicable a la predicción de este partido.", "Incident saved and applied to this match prediction."))
    st.subheader(_t("quality.provider"))
    provider_rows = [
        {_b("Componente", "Component"): "sports-data SQLite", _b("Estado", "Status"): _b("Disponible", "Available") if SPORTS_DB_PATH.exists() else _b("No disponible", "Unavailable"), _b("Ruta", "Path"): str(SPORTS_DB_PATH)},
        {_b("Componente", "Component"): "Collector analisis-de-datos", _b("Estado", "Status"): _b("Se comprueba al actualizar", "Checked during refresh"), _b("Ruta", "Path"): "CODEX_HOME/skills/analisis-de-datos"},
        {_b("Componente", "Component"): "SofaScore URL", _b("Estado", "Status"): _b("Experimental", "Experimental"), _b("Ruta", "Path"): _b("Sin cookies ni sesión", "No cookies or session")},
    ]
    st.dataframe(pd.DataFrame(provider_rows), width="stretch", hide_index=True)
    st.caption(_b("Fuentes: SQLite local del recolector, importaciones manuales y SofaScore experimental cuando el usuario lo solicita.", "Sources: the collector's local SQLite database, manual imports and experimental SofaScore retrieval when requested by the user."))
    st.subheader(_t("quality.sources"))
    bank_labels = {0: _b("Prioritario / autoridad", "Priority / authoritative"), 1: _b("Primario abierto", "Open primary"), 2: _b("Secundario API", "Secondary API"), 3: _b("Terciario / experimental", "Tertiary / experimental")}
    catalog_rows = [
        {
            _b("Banco", "Tier"): bank_labels.get(int(row["bank"]), str(row["bank"])),
            _b("Fuente", "Source"): row["label"],
            _b("Fiabilidad", "Reliability"): f'{float(row["reliability"]):.0%}',
            _b("Coste", "Cost"): localize_cost_tier(row["cost_tier"]),
            _b("Consumo", "Resource use"): localize_resource_tier(row["resource_tier"]),
            _b("Credencial", "Credentials"): _b("Sí", "Yes") if row["requires_credentials"] else _b("No", "No"),
            _b("Dominios", "Domains"): ", ".join(json.loads(row["domains_json"])),
            _b("Notas", "Notes"): row["notes"],
        }
        for row in repo.list_source_catalog()
    ]
    st.dataframe(pd.DataFrame(catalog_rows), width="stretch", hide_index=True)
    st.caption(_b("El enrutador elige por dominio el banco disponible más alto; los desacuerdos del mismo banco se marcan como conflicto.", "The router selects the highest-priority available source tier for each domain; disagreements within a tier are flagged as conflicts."))


def render_player_intelligence() -> None:
    hero(
        _t("players.eyebrow"),
        _t("players.title"),
        _t("players.supporting"),
    )
    repo = _repo()
    # Manual refresh of the daily player bank. Useful right after a fixture
    # ends because the provider may publish updated minutes/goals minutes later.
    refresh_col, info_col = st.columns([1, 2.5])
    with refresh_col:
        refresh_players = st.button(
            _t("players.refresh"),
            key="refresh_players_intelligence",
            type="primary", width="stretch",
            help=_t("players.refresh_help"),
        )
    with info_col:
        st.caption(_t("players.refresh_note"))
    if refresh_players:
        with st.spinner(_t("players.refreshing")):
            try:
                refresh_result = _force_refresh_players(repo)
            except Exception as exc:
                st.error(_b(f"No se pudo recargar el banco: {type(exc).__name__}: {exc}", f"The player dataset could not be refreshed: {type(exc).__name__}: {exc}"))
                refresh_result = None
        if refresh_result is not None:
            if refresh_result.updated:
                st.success(
                    _b(f"Banco actualizado: {len(refresh_result.updated)} fuente(s) recibida(s).", f"Player dataset updated: {len(refresh_result.updated)} source(s) received.")
                )
            elif refresh_result.unchanged:
                st.info(_b("Sin cambios: el proveedor no ha publicado nuevas estadísticas desde la última recarga.", "No changes: the provider has not published new statistics since the last refresh."))
            if refresh_result.failed:
                st.warning(
                    _b("Proveedor con error: ", "Provider error: ") + ", ".join(refresh_result.failed)
                    + _b(". Se conservan los datos en caché.", ". Cached data has been retained.")
                )
            _invalidate_player_caches()
            st.rerun()
    minimum_minutes = st.slider(
        _t("players.minimum_minutes"), 0, 900, 60, 30,
        help=_t("players.minimum_help"),
    )
    rows = _player_intelligence_rows_cached(_db_signature(), 0)
    if not rows:
        empty_state(_t("players.empty"), _t("players.empty_body"), icon="👤")
    else:
        frame = pd.DataFrame(rows)
        selected_ranking = st.segmented_control(
            _t("players.ranking"),
            ["impact", "goals", "assists", "shots"],
            default="impact",
            format_func=lambda key: _t(f"players.{key}"),
            label_visibility="collapsed",
        )
        ranking_specs = {
            "impact": ("impact", _t("players.impact"), "impact", _t("players.impact"), None, None),
            "goals": ("goals_per90", f'{_t("players.goals")} / 90', "goals", _t("players.goals"), "goals_per90", f'{_t("players.goals")}/90'),
            "assists": ("assists_per90", f'{_t("players.assists")} / 90', "assists", _t("players.assists"), "assists_per90", f'{_t("players.assists")}/90'),
            "shots": ("shots_per90", f'{_t("players.shots")} / 90', "shots", _t("players.shots"), "shots_per90", f'{_t("players.shots")}/90'),
        }
        metric, title, total_col, total_label, rate_col, rate_label = ranking_specs[selected_ranking]
        _render_player_panel(
            frame, metric, title, total_col, total_label, rate_col, rate_label,
            minimum_minutes=int(minimum_minutes),
        )
        if "passes_per90" not in frame:
            callout(_b("Pases: sin cobertura en el banco diario actual. Se conserva como dato desconocido y no como 0; aparecerá cuando una fuente revisada lo aporte.", "Passes: not covered by the current daily dataset. The value remains unknown rather than being treated as zero, and will appear when a reviewed source provides it."))
        st.caption(_b("Impacto estandariza solo métricas realmente disponibles. Todas las clasificaciones muestran minutos y partidos para contextualizar la muestra.", "Impact standardises only metrics that are genuinely available. Every ranking includes minutes and matches to provide sample context."))
