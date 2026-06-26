from pathlib import Path
from contextlib import closing
import sqlite3


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    fifa_code TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    position TEXT,
    UNIQUE(name, team_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    competition TEXT NOT NULL,
    stage TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    team_a_id INTEGER NOT NULL REFERENCES teams(id),
    team_b_id INTEGER NOT NULL REFERENCES teams(id),
    status TEXT NOT NULL,
    venue TEXT,
    neutral_site INTEGER NOT NULL DEFAULT 1,
    UNIQUE(competition, kickoff_utc, team_a_id, team_b_id)
);

CREATE TABLE IF NOT EXISTS team_match_stats (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    goals INTEGER,
    xg REAL,
    shots INTEGER,
    shots_on_target INTEGER,
    possession REAL,
    corners INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    saves INTEGER,
    goals_conceded INTEGER,
    source_id TEXT,
    manual_edit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(match_id, team_id)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    minutes INTEGER,
    goals INTEGER,
    assists INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    passes INTEGER,
    source_id TEXT,
    manual_edit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(match_id, player_id)
);

CREATE TABLE IF NOT EXISTS manual_odds (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    market_family TEXT NOT NULL,
    market_name TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    line REAL,
    decimal_odds REAL NOT NULL,
    bookmaker TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    considered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    market_family TEXT NOT NULL,
    market_name TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    line REAL,
    probability REAL NOT NULL,
    confidence TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    explanation TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    result_value REAL NOT NULL,
    brier_score REAL,
    hit INTEGER NOT NULL,
    evaluated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT,
    retrieved_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    source_event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    missing_critical_json TEXT NOT NULL DEFAULT '[]',
    missing_optional_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(match_id, source_event_id)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    subject_type TEXT NOT NULL,
    subject_name TEXT,
    metric TEXT NOT NULL,
    value_number REAL,
    value_text TEXT,
    unit TEXT,
    context_json TEXT NOT NULL DEFAULT '{}',
    source_id TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    sample_size INTEGER,
    observed_at_utc TEXT NOT NULL,
    UNIQUE(match_id, subject_type, subject_name, metric, context_json, source_id)
);

CREATE TABLE IF NOT EXISTS imported_lineups (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    team_name TEXT NOT NULL,
    player_name TEXT NOT NULL,
    lineup_status TEXT NOT NULL,
    position TEXT,
    shirt_number TEXT,
    source_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    UNIQUE(match_id, team_name, player_name, source_id)
);

CREATE TABLE IF NOT EXISTS match_results (
    match_id INTEGER PRIMARY KEY REFERENCES matches(id),
    goals_a INTEGER NOT NULL,
    goals_b INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_health (
    provider TEXT PRIMARY KEY,
    credential_name TEXT,
    configured INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    checked_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_entities (
    provider TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    canonical_type TEXT NOT NULL,
    canonical_id INTEGER NOT NULL,
    original_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(provider, entity_type, provider_id)
);

CREATE TABLE IF NOT EXISTS penalty_attempts (
    id INTEGER PRIMARY KEY,
    player_name TEXT NOT NULL,
    team_name TEXT,
    transfermarkt_player_id TEXT,
    attempted_on TEXT,
    competition TEXT,
    phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    goalkeeper_name TEXT,
    opponent_team TEXT,
    minute TEXT,
    match_label TEXT,
    source_provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_provider, source_row_key)
);

CREATE TABLE IF NOT EXISTS historical_matches (
    id INTEGER PRIMARY KEY,
    played_at_utc TEXT NOT NULL,
    team_a_name TEXT NOT NULL,
    team_b_name TEXT NOT NULL,
    goals_a INTEGER NOT NULL,
    goals_b INTEGER NOT NULL,
    tournament TEXT NOT NULL,
    city TEXT,
    country TEXT,
    neutral_site INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    UNIQUE(source_id, source_row_key)
);

CREATE TABLE IF NOT EXISTS screenshot_batches (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    status TEXT NOT NULL,
    source_url TEXT,
    created_at_utc TEXT NOT NULL,
    finalized_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS screenshot_assets (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES screenshot_batches(id),
    original_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    uploaded_at_utc TEXT NOT NULL,
    UNIQUE(batch_id, sha256)
);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES screenshot_batches(id),
    asset_id INTEGER NOT NULL REFERENCES screenshot_assets(id),
    subject_type TEXT NOT NULL,
    subject_name TEXT,
    metric TEXT NOT NULL,
    value_number REAL,
    value_text TEXT,
    unit TEXT,
    period TEXT NOT NULL,
    raw_label TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    confidence REAL NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'pending_review'
);

CREATE TABLE IF NOT EXISTS review_decisions (
    candidate_id INTEGER PRIMARY KEY REFERENCES extraction_candidates(id),
    decision TEXT NOT NULL,
    corrected_subject_name TEXT,
    corrected_metric TEXT,
    corrected_value_number REAL,
    corrected_value_text TEXT,
    corrected_unit TEXT,
    corrected_period TEXT,
    rejection_reason TEXT,
    reviewed_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlement_versions (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    version INTEGER NOT NULL,
    batch_id INTEGER REFERENCES screenshot_batches(id),
    goals_a INTEGER NOT NULL,
    goals_b INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    UNIQUE(match_id, version)
);

CREATE TABLE IF NOT EXISTS prediction_evaluations (
    id INTEGER PRIMARY KEY,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    settlement_version_id INTEGER NOT NULL REFERENCES settlement_versions(id),
    result_value REAL NOT NULL,
    brier_score REAL,
    hit INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    evaluated_at_utc TEXT NOT NULL,
    UNIQUE(prediction_id, settlement_version_id)
);

CREATE TABLE IF NOT EXISTS source_catalog (
    provider_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    bank INTEGER NOT NULL,
    reliability REAL NOT NULL,
    cost_tier TEXT NOT NULL,
    resource_tier TEXT NOT NULL,
    domains_json TEXT NOT NULL,
    freshness_hours INTEGER NOT NULL,
    requires_credentials INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    synced_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    provider_id TEXT NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    query TEXT NOT NULL,
    language TEXT NOT NULL,
    positive INTEGER NOT NULL,
    neutral INTEGER NOT NULL,
    negative INTEGER NOT NULL,
    sample_size INTEGER NOT NULL,
    sentiment_score REAL NOT NULL,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    eligible_for_model INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(match_id, provider_id, window_start_utc, window_end_utc, query, language)
);

CREATE TABLE IF NOT EXISTS outcome_model_runs (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    training_cutoff_utc TEXT,
    validation_cutoff_utc TEXT,
    temperature REAL,
    reason TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_snapshots (
    id INTEGER PRIMARY KEY,
    provider_id TEXT NOT NULL,
    provider_version TEXT,
    content_sha256 TEXT NOT NULL,
    checked_at_utc TEXT NOT NULL,
    data_updated_at_utc TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    UNIQUE(provider_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS dataset_refresh_checks (
    id INTEGER PRIMARY KEY,
    provider_id TEXT NOT NULL,
    checked_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS current_wc_player_stats (
    provider_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    team_name TEXT NOT NULL,
    position TEXT,
    games INTEGER,
    starts INTEGER,
    minutes INTEGER,
    goals INTEGER,
    assists INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    passes INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    tackles_won INTEGER,
    interceptions INTEGER,
    save_percentage REAL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, player_name, team_name)
);

CREATE TABLE IF NOT EXISTS current_wc_team_stats (
    provider_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    data_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, team_name)
);

CREATE TABLE IF NOT EXISTS current_wc_match_stats (
    provider_id TEXT NOT NULL,
    match_key TEXT NOT NULL,
    data_json TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, match_key)
);

CREATE TABLE IF NOT EXISTS squad_context_events (
    id INTEGER PRIMARY KEY,
    team_name TEXT NOT NULL,
    player_name TEXT,
    event_type TEXT NOT NULL,
    starts_at_utc TEXT NOT NULL,
    ends_at_utc TEXT,
    affected_match_id INTEGER REFERENCES matches(id),
    source_id TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    UNIQUE(team_name, player_name, event_type, starts_at_utc, source_id)
);

CREATE INDEX IF NOT EXISTS idx_historical_matches_date
ON historical_matches(played_at_utc);

CREATE INDEX IF NOT EXISTS idx_candidates_batch_status
ON extraction_candidates(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_dataset_snapshots_provider_checked
ON dataset_snapshots(provider_id, checked_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_dataset_refresh_checks_provider_checked
ON dataset_refresh_checks(provider_id, checked_at_utc DESC);

CREATE TABLE IF NOT EXISTS knockout_bracket (
    id INTEGER PRIMARY KEY,
    competition TEXT NOT NULL,
    stage TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    venue TEXT,
    home_source TEXT NOT NULL,
    away_source TEXT NOT NULL,
    home_team_id INTEGER REFERENCES teams(id),
    away_team_id INTEGER REFERENCES teams(id),
    match_id INTEGER REFERENCES matches(id),
    resolved_at_utc TEXT,
    UNIQUE(competition, slot_id)
);

CREATE INDEX IF NOT EXISTS idx_knockout_bracket_stage
ON knockout_bracket(competition, stage);

-- Frozen pre-kickoff payload of model predictions. Persisted automatically
-- when a fresh bundle is computed for a match whose kickoff is still in
-- the future. Required by the backtest / calibration framework: without an
-- immutable record of "what the model said before the match" we cannot
-- measure improvement honestly when the deep-stats pipeline evolves.
CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    generated_at_utc TEXT NOT NULL,
    data_as_of_utc TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(match_id, model_version, data_as_of_utc)
);
CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_match
ON prediction_snapshots(match_id, generated_at_utc DESC);

-- Hot-path indexes (Streamlit dashboard re-runs these on every interaction)
CREATE INDEX IF NOT EXISTS idx_matches_competition_kickoff
ON matches(competition, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_observations_match
ON observations(match_id);
CREATE INDEX IF NOT EXISTS idx_observations_subject
ON observations(subject_type, subject_name);
CREATE INDEX IF NOT EXISTS idx_observations_deep_latest
ON observations(subject_type, evidence_status, match_id, subject_name, metric, id);
CREATE INDEX IF NOT EXISTS idx_team_match_stats_match
ON team_match_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_player_match_stats_player
ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_player_match_stats_match
ON player_match_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_current_wc_player_stats_team_cards
ON current_wc_player_stats(team_name, yellow_cards, red_cards);
CREATE INDEX IF NOT EXISTS idx_squad_context_active
ON squad_context_events(evidence_status, affected_match_id, starts_at_utc, ends_at_utc);
CREATE INDEX IF NOT EXISTS idx_squad_context_source
ON squad_context_events(source_id);
CREATE INDEX IF NOT EXISTS idx_penalty_attempts_player
ON penalty_attempts(player_name, transfermarkt_player_id);
CREATE INDEX IF NOT EXISTS idx_penalty_attempts_team
ON penalty_attempts(team_name);
"""


# Additional columns added after the original schema was defined. They are
# applied with ALTER TABLE so existing DBs upgrade in place without losing data.
_OPTIONAL_COLUMNS = {
    "team_match_stats": [
        ("saves", "INTEGER"),
        ("goals_conceded", "INTEGER"),
    ],
    "player_match_stats": [
        ("saves", "INTEGER"),
        ("goals_conceded", "INTEGER"),
        ("save_percentage", "REAL"),
        ("red_cards", "INTEGER"),
    ],
    # Knockout extras: ET aggregate goals + penalty shoot-out tally. NULL
    # means the match either ended in regulation or hasn't been played yet.
    "match_results": [
        ("extra_time_team_a_goals", "INTEGER"),
        ("extra_time_team_b_goals", "INTEGER"),
        ("penalty_team_a", "INTEGER"),
        ("penalty_team_b", "INTEGER"),
    ],
}


def _ensure_optional_columns(con: sqlite3.Connection) -> None:
    for table, columns in _OPTIONAL_COLUMNS.items():
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as con:
        con.execute("PRAGMA busy_timeout = 30000")
        try:
            con.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        con.executescript(SCHEMA)
        _ensure_optional_columns(con)
        con.commit()
