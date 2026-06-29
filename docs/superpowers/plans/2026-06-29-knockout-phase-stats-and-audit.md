# Knockout Phase Statistics and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registrar y auditar por separado 90 minutos, prórroga y tanda, mejorar únicamente el submodelo de prórroga y mostrar el resultado correcto en el bracket.

**Architecture:** Mantener `team_match_stats` como agregado canónico de 90 minutos y añadir almacenamiento versionado para resultados por fase, estadísticas de equipo por periodo y lanzamientos. La predicción de prórroga recibe un ajuste aislado y regularizado; snapshots y auditorías consumen datos congelados anteriores al partido.

**Tech Stack:** Python 3.12, SQLite, Streamlit, `unittest`, HTML/CSS existente del bracket, modelos Poisson/Negative Binomial y Monte Carlo de penaltis existentes.

## Global Constraints

- Las estadísticas por partes de los 90 minutos no pueden cambiar ninguna predicción actual.
- `team_match_stats` continúa representando exclusivamente el acumulado de 90 minutos.
- La evidencia de prórroga solo puede cambiar sus xG y las probabilidades condicionales victoria/empate/derrota en prórroga.
- Las estadísticas de jugador siguen siendo de partido completo y proceden del repositorio actual.
- Los acumulados son opcionales y sirven para validación, nunca para duplicar muestras.
- Solo `saved` acredita una parada al portero; `off_target_or_woodwork` no.
- La historia de Transfermarkt no se reescribe ni se reinterpreta.
- Los snapshots prepartido son inmutables y la evidencia del partido solo influye en encuentros posteriores.
- Los tantos de la tanda no se suman al marcador oficial.
- No se añaden dependencias Python nuevas.

---

## Mapa de archivos

- Crear `src/wcpredict/match_phases.py`: tipos, periodos canónicos, agregación y validaciones puras.
- Crear `src/wcpredict/extra_time_model.py`: ajuste regularizado y aislado de prórroga.
- Crear `src/wcpredict/knockout_audit.py`: evaluación pura de snapshots por fase.
- Crear `src/wcpredict/ui/knockout_settlement.py`: flujo guiado, editor de tanda y estados de cobertura.
- Modificar `src/wcpredict/database.py`: nuevas tablas e índices.
- Modificar `src/wcpredict/deep_match_import.py`: periodo explícito en la carga revisada.
- Modificar `src/wcpredict/repository.py`: persistencia por periodo, cierre versionado y consultas de entrenamiento.
- Modificar `src/wcpredict/knockout_model.py`: aceptar xG de prórroga explícitos sin cambiar el fallback actual.
- Modificar `src/wcpredict/knockout_bracket.py`: resolver ganadores desde el resultado de fase activo.
- Modificar `src/wcpredict/penalty_profiles.py`: unir lanzamientos activos del torneo con `penalty_attempts`.
- Modificar `src/wcpredict/ui/pages.py`: snapshots knockout, auditoría y entrada al nuevo flujo.
- Modificar `src/wcpredict/ui/bracket.py` y `src/wcpredict/ui/theme.py`: marcador `2 (5)` y etiqueta de prórroga.
- Añadir pruebas unitarias específicas y ampliar contratos/Smoke existentes.

---

### Task 1: Dominio de fases y migración SQLite

**Files:**
- Create: `src/wcpredict/match_phases.py`
- Modify: `src/wcpredict/database.py`
- Test: `tests/test_match_phases.py`
- Test: `tests/test_database_repository.py`

**Interfaces:**
- Produces: `MatchPhaseResultInput`, `PeriodStatInput`, `ShootoutKickInput`, `validate_phase_result(value: MatchPhaseResultInput) -> tuple[str, ...]`, `validate_shootout_sequence(kicks: tuple[ShootoutKickInput, ...]) -> ShootoutSummary`, `aggregate_additive_periods(rows: list[dict], periods: tuple[str, ...]) -> dict[tuple[int, str], float]`, `validate_period_totals(rows: list[dict]) -> list[PhaseValidationIssue]`, `regulation_projection(rows: list[dict]) -> dict[int, dict[str, float | int | None]]`.
- Produces tables: `match_phase_results`, `team_match_period_stats`, `shootout_kicks`.

- [ ] **Step 1: Write failing domain and migration tests**

```python
def test_shootout_requires_level_score_after_extra_time(self):
    value = MatchPhaseResultInput(1, 1, 1, 0, 5, 4, "shootout")
    self.assertIn("empate al 120", " ".join(validate_phase_result(value)))

def test_only_saved_credits_the_goalkeeper(self):
    kicks = (
        ShootoutKickInput(1, 10, 20, "saved"),
        ShootoutKickInput(2, 11, 20, "off_target_or_woodwork"),
    )
    summary = summarize_shootout(kicks)
    self.assertEqual(1, summary.goalkeeper_saves[20])
    self.assertEqual(2, summary.goalkeeper_faced[20])

def test_initialize_adds_phase_tables_without_touching_existing_results(self):
    # Insert an existing match_result, call initialize again, then assert the
    # old score remains and all three new tables exist.
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_match_phases tests.test_database_repository -v
```

Expected: import/table failures because the new module and tables do not exist.

- [ ] **Step 3: Implement canonical types and pure validators**

```python
ATOMIC_PERIODS = ("first_half", "second_half", "extra_time_first", "extra_time_second")
CUMULATIVE_PERIODS = ("regulation_total", "extra_time_total", "full_match_total")
ALL_PERIODS = ATOMIC_PERIODS + CUMULATIVE_PERIODS
ADDITIVE_METRICS = frozenset({
    "goals", "xg", "shots", "shots_on_target", "corners",
    "yellow_cards", "red_cards", "saves", "goals_conceded",
})
SHOOTOUT_OUTCOMES = frozenset({"scored", "saved", "off_target_or_woodwork"})

@dataclass(frozen=True)
class MatchPhaseResultInput:
    regulation_goals_a: int
    regulation_goals_b: int
    extra_time_goals_a: int | None
    extra_time_goals_b: int | None
    shootout_goals_a: int | None
    shootout_goals_b: int | None
    decided_in: str

@dataclass(frozen=True)
class ShootoutKickInput:
    sequence_number: int
    team_id: int
    taker_player_id: int
    goalkeeper_player_id: int
    outcome: str

@dataclass(frozen=True)
class PeriodStatInput:
    team_id: int
    period: str
    metrics: dict[str, float | int | None]
    source_id: str
    content_sha256: str

@dataclass(frozen=True)
class PhaseValidationIssue:
    severity: str
    team_name: str
    metric: str
    calculated: float | None
    imported: float | None
    message: str

@dataclass(frozen=True)
class ShootoutSummary:
    goals_by_team: dict[int, int]
    goalkeeper_saves: dict[int, int]
    goalkeeper_faced: dict[int, int]
    winner_team_id: int | None
    errors: tuple[str, ...]
```

`validate_phase_result` must enforce the invariants from the design. `validate_shootout_sequence` must replay alternating attempts, early termination and paired sudden death; it returns errors and the computed tally rather than trusting manually entered totals.

- [ ] **Step 4: Add tables and indexes to `SCHEMA`**

```sql
CREATE TABLE IF NOT EXISTS match_phase_results (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    settlement_version_id INTEGER NOT NULL UNIQUE REFERENCES settlement_versions(id),
    regulation_goals_a INTEGER NOT NULL,
    regulation_goals_b INTEGER NOT NULL,
    extra_time_goals_a INTEGER,
    extra_time_goals_b INTEGER,
    shootout_goals_a INTEGER,
    shootout_goals_b INTEGER,
    decided_in TEXT NOT NULL CHECK(decided_in IN ('regulation','extra_time','shootout')),
    source_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_match_period_stats (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    period TEXT NOT NULL,
    goals INTEGER, xg REAL, shots INTEGER, shots_on_target INTEGER,
    possession REAL, corners INTEGER, yellow_cards INTEGER, red_cards INTEGER,
    saves INTEGER, goals_conceded INTEGER,
    source_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
    manual_edit INTEGER NOT NULL DEFAULT 0,
    observed_at_utc TEXT NOT NULL,
    PRIMARY KEY(match_id, team_id, period, source_id)
);

CREATE TABLE IF NOT EXISTS shootout_kicks (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    settlement_version_id INTEGER NOT NULL REFERENCES settlement_versions(id),
    sequence_number INTEGER NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    taker_player_id INTEGER NOT NULL REFERENCES players(id),
    goalkeeper_player_id INTEGER NOT NULL REFERENCES players(id),
    outcome TEXT NOT NULL CHECK(outcome IN ('scored','saved','off_target_or_woodwork')),
    source_provider TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(settlement_version_id, sequence_number)
);
```

Add indexes for `(match_id, period)`, `settlement_version_id`, `taker_player_id` and `goalkeeper_player_id`. Add `observations.period TEXT NOT NULL DEFAULT 'full_match'` through `_OPTIONAL_COLUMNS`; existing importers inherit `full_match`, while the guided importer writes a canonical period explicitly. New databases and existing databases receive the change without destructive migration.

- [ ] **Step 5: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_match_phases tests.test_database_repository -v
git add src/wcpredict/match_phases.py src/wcpredict/database.py tests/test_match_phases.py tests/test_database_repository.py
git commit -m "feat(phases): add knockout phase domain and schema"
```

Expected: PASS.

---

### Task 2: Importación guiada por periodo y validación de acumulados

**Files:**
- Modify: `src/wcpredict/deep_match_import.py`
- Modify: `src/wcpredict/repository.py`
- Test: `tests/test_deep_match_import.py`
- Test: `tests/test_deep_match_persistence.py`
- Create: `tests/test_phase_stats_persistence.py`

**Interfaces:**
- Consumes: period constants and aggregation helpers from Task 1.
- Produces: `Repository.import_deep_match_period(collection, intended_match_id, period, imported_at_utc)`.
- Produces: `Repository.list_team_match_period_stats(match_id: int, include_history: bool = False) -> list[dict]` and `Repository.validate_match_period_stats(match_id: int) -> list[PhaseValidationIssue]`.

- [ ] **Step 1: Write failing persistence and isolation tests**

```python
def test_first_and_second_half_project_once_to_regulation_stats(self):
    repo.import_deep_match_period(first_half, match_id, "first_half", now)
    repo.import_deep_match_period(second_half, match_id, "second_half", now)
    repo.project_regulation_stats(match_id, now)
    stats = {row["team_name"]: row for row in repo.list_team_match_stats(match_id)}
    self.assertEqual(18, stats["Spain"]["shots"])
    self.assertAlmostEqual(1.72, stats["Spain"]["xg"])

def test_extra_time_stats_never_project_to_team_match_stats(self):
    before = repo.list_team_match_stats(match_id)
    repo.import_deep_match_period(et, match_id, "extra_time_first", now)
    self.assertEqual(before, repo.list_team_match_stats(match_id))

def test_regulation_total_mismatch_reports_metric_and_team(self):
    issues = repo.validate_match_period_stats(match_id)
    self.assertEqual(("Spain", "shots", 18.0, 19.0), issues[0].comparison)
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_deep_match_import tests.test_phase_stats_persistence -v
```

Expected: missing repository methods.

- [ ] **Step 3: Add an explicit canonical period to import results**

Keep `load_deep_match_file(path)` unchanged for existing callers. The guided UI supplies the period separately. `import_deep_match_period` must:

1. validate `period in ALL_PERIODS`;
2. reuse `flatten_team_metrics`;
3. upsert core fields into `team_match_period_stats`;
4. persist all metrics in `observations.period` and `context_json.period` with the same canonical value;
5. include period in `source_event_id` so the same JSON cannot collide across periods;
6. keep the content hash and source file list;
7. avoid writing `team_match_stats` until projection.

`list_team_match_period_stats(..., include_history=False)` returns the most recent source row for each `(match_id, team_id, period)` using `ROW_NUMBER() OVER (PARTITION BY match_id, team_id, period ORDER BY observed_at_utc DESC, source_id DESC)`. `include_history=True` exposes every reviewed version for provenance.

- [ ] **Step 4: Implement validation and regulation projection**

Implement these exact methods:

```python
def validate_match_period_stats(self, match_id: int) -> list[PhaseValidationIssue]:
    rows = self.list_team_match_period_stats(match_id)
    return validate_period_totals(rows)

def project_regulation_stats(self, match_id: int, observed_at_utc: datetime) -> None:
    rows = self.list_team_match_period_stats(match_id)
    issues = validate_period_totals(rows)
    blocking = [issue for issue in issues if issue.severity == "blocking"]
    if blocking:
        raise ValueError("; ".join(issue.message for issue in blocking))
    projected = regulation_projection(rows)
    self._upsert_projected_team_match_stats(match_id, projected, observed_at_utc)
```

For additive metrics use `first_half + second_half`; prefer a matching `regulation_total` when supplied. Exact integer mismatches and xG differences above `0.02` are blocking. Possession and other rates are non-blocking unless numerator/denominator metrics permit exact reconstruction. Never project `extra_time_*` or `full_match_total` into `team_match_stats`.

- [ ] **Step 5: Preserve the old full-match importer**

`Repository.import_deep_match_collection` remains the compatibility path for group-stage/current JSON. Its behavior and tests must remain unchanged. The new method is used only by the guided knockout workflow.

- [ ] **Step 6: Run focused tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_deep_match_import tests.test_deep_match_persistence tests.test_phase_stats_persistence -v
git add src/wcpredict/deep_match_import.py src/wcpredict/repository.py tests/test_deep_match_import.py tests/test_deep_match_persistence.py tests/test_phase_stats_persistence.py
git commit -m "feat(phases): import and validate deep stats by period"
```

Expected: PASS, including existing deep import behavior.

---

### Task 3: Cierre versionado por fases y resolución del bracket

**Files:**
- Modify: `src/wcpredict/repository.py`
- Modify: `src/wcpredict/knockout_bracket.py`
- Test: `tests/test_prediction_persistence.py`
- Test: `tests/test_knockout_bracket.py`
- Create: `tests/test_knockout_settlement.py`

**Interfaces:**
- Produces: `Repository.settle_knockout_match_versioned(match_id, phase_result, kicks, batch_id, evaluated_at_utc)`.
- Produces: `Repository.get_active_match_phase_result(match_id)`.
- Existing `settle_match_versioned(match_id: int, goals_a: int, goals_b: int, batch_id: int | None, evaluated_at_utc: datetime) -> int` remains unchanged for group-stage callers.

- [ ] **Step 1: Write failing settlement tests for all three paths**

```python
def test_extra_time_settlement_keeps_official_120_score_but_trains_on_90(self):
    phase = MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time")
    repo.settle_knockout_match_versioned(mid, phase, (), None, now)
    self.assertEqual((2, 1), official_score(repo, mid))
    self.assertEqual((1, 1), reviewed_historical_score(repo, mid))

def test_shootout_winner_resolves_next_slot_from_active_phase_result(self):
    phase = MatchPhaseResultInput(1, 1, 0, 0, 5, 4, "shootout")
    repo.settle_knockout_match_versioned(mid, phase, kicks, None, now)
    self.assertEqual(team_a_id, resolved_winner(repo, slot))

def test_correction_deactivates_old_kicks_and_evaluations(self):
    first = repo.settle_knockout_match_versioned(mid, phase_a, kicks_a, None, now)
    second = repo.settle_knockout_match_versioned(mid, phase_b, kicks_b, None, later)
    self.assertNotEqual(first, second)
    self.assertEqual(kicks_b, active_kicks(repo, mid))
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement tests.test_knockout_bracket tests.test_prediction_persistence -v
```

- [ ] **Step 3: Implement the transactional knockout settlement**

The method must:

1. validate phase result and shootout sequence before opening writes;
2. validate period totals and refuse blocking issues;
3. create/reuse a `settlement_versions` version;
4. save `match_phase_results` linked to that version;
5. save kicks linked to that version;
6. write `match_results` with `regulation + extra_time`, excluding penalties;
7. write `historical_matches` with regulation goals only;
8. project the validated regulation stats;
9. evaluate existing 1X2 predictions with regulation goals;
10. retrain outcome/EMA using regulation goals;
11. leave previous phase rows/kicks intact but inactive through `settlement_versions.active=0`.

An identical phase payload, kick sequence and batch returns the current settlement ID without new rows.

- [ ] **Step 4: Make bracket resolution phase-aware with legacy fallback**

Change `_resolve_source`/`_decide_winner` to query the active phase result first. If none exists, retain the current optional-column behavior (`extra_time_team_*`, `penalty_team_*`) for old data. A shootout winner is determined by `shootout_goals_*`; penalties never alter the displayed official goals.

- [ ] **Step 5: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement tests.test_knockout_bracket tests.test_prediction_persistence tests.test_live_residuals_hook -v
git add src/wcpredict/repository.py src/wcpredict/knockout_bracket.py tests/test_knockout_settlement.py tests/test_knockout_bracket.py tests/test_prediction_persistence.py
git commit -m "feat(knockout): settle and resolve matches by phase"
```

Expected: PASS.

---

### Task 4: Lanzamientos observados y perfiles futuros

**Files:**
- Modify: `src/wcpredict/repository.py`
- Modify: `src/wcpredict/penalty_profiles.py`
- Modify: `src/wcpredict/penalty_context_cache.py`
- Test: `tests/test_penalty_profiles.py`
- Test: `tests/test_penalty_context_cache.py`
- Extend: `tests/test_knockout_settlement.py`

**Interfaces:**
- Produces: `Repository.list_penalty_evidence(team_names, before_utc)` returning normalized Transfermarkt and active World Cup rows.
- Consumed by existing `build_player_profiles` and `build_goalkeeper_profile` without changing their public signatures.

- [ ] **Step 1: Write failing normalization and temporal-isolation tests**

```python
def test_world_cup_off_target_counts_for_taker_but_not_keeper_save(self):
    evidence = repo.list_penalty_evidence(("Spain", "Germany"), future_kickoff)
    taker = build_player_profile("Taker", "FW", evidence, future_kickoff.date())
    keeper = build_goalkeeper_profile({"player_name": "Keeper"}, evidence)
    self.assertEqual(1, taker.attempts)
    self.assertEqual(1, keeper.faced_penalties)
    self.assertEqual(0, sum(row["outcome"] == "saved" for row in evidence))

def test_match_cannot_consume_its_own_shootout(self):
    self.assertEqual([], repo.list_penalty_evidence(("Spain",), match_kickoff))
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_penalty_profiles tests.test_penalty_context_cache tests.test_knockout_settlement -v
```

- [ ] **Step 3: Normalize active shootout kicks at read time**

Map World Cup rows to the existing evidence contract:

```python
{
    "player_name": taker_name,
    "team_name": team_name,
    "attempted_on": kickoff_date,
    "phase": "shootout",
    "outcome": "scored" | "saved" | "off_target",
    "goalkeeper_name": goalkeeper_name,
    "source_provider": "world_cup_2026_manual",
}
```

Join only active settlement versions and only matches with kickoff strictly before `before_utc`. Do not insert these rows into `penalty_attempts`.

- [ ] **Step 4: Count every World Cup kick as faced while crediting only saves**

Update goalkeeper profiling so explicit World Cup `scored`, `saved` and `off_target` rows all count as faced. Preserve the current conservative Transfermarkt rule: one-sided scored history without an explicit save remains excluded. Use `source_provider` to distinguish the two sources.

- [ ] **Step 5: Include active kick fingerprint in penalty precomputation**

`penalty_context_cache` input fingerprints must include active prior kicks. Regenerating a future fixture after a new shootout therefore marks only affected artifacts stale.

- [ ] **Step 6: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_penalty_profiles tests.test_penalty_context_cache tests.test_penalty_history_model tests.test_knockout_settlement -v
git add src/wcpredict/repository.py src/wcpredict/penalty_profiles.py src/wcpredict/penalty_context_cache.py tests/test_penalty_profiles.py tests/test_penalty_context_cache.py tests/test_knockout_settlement.py
git commit -m "feat(penalties): learn from reviewed tournament shootouts"
```

Expected: PASS.

---

### Task 5: Submodelo regularizado y aislado de prórroga

**Files:**
- Create: `src/wcpredict/extra_time_model.py`
- Modify: `src/wcpredict/repository.py`
- Modify: `src/wcpredict/knockout_model.py`
- Modify: `src/wcpredict/ui/pages.py`
- Create: `tests/test_extra_time_model.py`
- Modify: `tests/test_knockout_bracket.py`

**Interfaces:**
- Produces: `ExtraTimeAdjustment(adjusted_xg, factor_a, factor_b, sample_a, sample_b, explanation)` and `adjust_extra_time_xg(team_a: str, team_b: str, regulation_xg_a: float, regulation_xg_b: float, rows: list[dict], as_of: datetime) -> ExtraTimeAdjustment`.
- Produces: `Repository.list_extra_time_training_rows_before(kickoff_utc)`.
- Extends: `predict_knockout_match(team_a_xg: float, team_b_xg: float, *, dispersion: float = 0.0, rho: float = 0.0, home_gk_rating: float | None = None, away_gk_rating: float | None = None, home_penalty_win_probability: float | None = None, extra_time_xg: tuple[float, float] | None = None) -> KnockoutPrediction`.

- [ ] **Step 1: Write failing isolation, shrinkage and normalization tests**

```python
def test_no_extra_time_history_keeps_current_fraction(self):
    result = adjust_extra_time_xg("A", "B", 1.5, 1.2, [], as_of)
    self.assertAlmostEqual(0.45, result.adjusted_xg[0])
    self.assertAlmostEqual(0.36, result.adjusted_xg[1])

def test_one_extreme_sample_is_strongly_shrunk(self):
    result = adjust_extra_time_xg("A", "B", 1.5, 1.2, [extreme_row], as_of)
    self.assertLess(result.factor_a, 1.10)

def test_extra_time_rows_do_not_change_regulation_matrix(self):
    base = predict_knockout_match(1.5, 1.2)
    adjusted = predict_knockout_match(1.5, 1.2, extra_time_xg=(0.55, 0.30))
    self.assertEqual(base.home_wins_90, adjusted.home_wins_90)
    self.assertNotEqual(base.cond_home_wins_et_given_draw_90, adjusted.cond_home_wins_et_given_draw_90)
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_extra_time_model tests.test_knockout_bracket -v
```

- [ ] **Step 3: Implement the regularized adjustment**

Use the current base `regulation_xg * 0.30`. For each prior extra-time row:

- weight recency with a 365-day half-life;
- combine observed extra-time xG and goals as `0.70 * xg + 0.30 * goals` when xG exists, otherwise goals;
- estimate team attack and opponent defence ratios against the base intensity;
- shrink each ratio with eight prior-equivalent matches at factor `1.0`;
- combine attack and opponent defence with a geometric mean;
- clamp each final factor to `[0.75, 1.25]`.

Return sample counts, factors and an explanation. Missing evidence returns factor `1.0` exactly.

- [ ] **Step 4: Query only prior active extra-time evidence**

`list_extra_time_training_rows_before` joins active `match_phase_results`, the two extra-time atomic periods and teams. It excludes the target/future match and ignores full-match totals.

- [ ] **Step 5: Inject explicit extra-time xG into knockout prediction**

When `extra_time_xg` is absent, `predict_knockout_match` must produce byte-for-byte equivalent probabilities to the current implementation. When present, only `matrix_et` uses it. Increment `PREDICTION_ENGINE_VERSION` because frozen knockout snapshots change.

- [ ] **Step 6: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_extra_time_model tests.test_knockout_bracket tests.test_services -v
git add src/wcpredict/extra_time_model.py src/wcpredict/repository.py src/wcpredict/knockout_model.py src/wcpredict/ui/pages.py tests/test_extra_time_model.py tests/test_knockout_bracket.py
git commit -m "feat(extra-time): learn isolated regularized phase rates"
```

Expected: PASS.

---

### Task 6: Snapshot knockout y auditoría por fases

**Files:**
- Create: `src/wcpredict/knockout_audit.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/repository.py`
- Create: `tests/test_knockout_audit.py`
- Modify: `tests/test_audit.py`
- Modify: `tests/test_live_residuals_hook.py`

**Interfaces:**
- Produces: `build_knockout_snapshot_section(prediction, extra_time_xg, penalty_context) -> dict`.
- Produces: `evaluate_knockout_snapshot(snapshot, phase_result, kicks) -> KnockoutPhaseAudit`.
- Produces: `Repository.save_knockout_phase_backtest_rows(match_id: int, settlement_version_id: int, audit: KnockoutPhaseAudit, recorded_at_utc: datetime) -> None` using `backtest_runs` and `extra_json`.

Define the audit contract explicitly:

```python
@dataclass(frozen=True)
class PhaseAuditSection:
    status: str
    actual_score: str | None
    actual_outcome: str | None
    predicted_outcome: str | None
    observed_probability: float | None
    brier: float | None
    rows: tuple[dict, ...]

@dataclass(frozen=True)
class KnockoutPhaseAudit:
    regulation: PhaseAuditSection
    extra_time: PhaseAuditSection
    shootout: PhaseAuditSection
```

- [ ] **Step 1: Write failing audit-path tests**

```python
def test_regulation_only_marks_later_phases_not_played(self):
    audit = evaluate_knockout_snapshot(snapshot, regulation_result, ())
    self.assertEqual("not_played", audit.extra_time.status)
    self.assertEqual("not_played", audit.shootout.status)

def test_extra_time_audit_uses_goals_scored_only_in_extra_time(self):
    audit = evaluate_knockout_snapshot(snapshot, extra_time_result, ())
    self.assertEqual("1-0", audit.extra_time.actual_score)
    self.assertEqual("home", audit.extra_time.actual_outcome)

def test_shootout_audit_scores_winner_and_each_kick(self):
    audit = evaluate_knockout_snapshot(snapshot, shootout_result, kicks)
    self.assertAlmostEqual((snapshot_penalty_home - 1.0) ** 2, audit.shootout.winner_brier)
    self.assertEqual(len(kicks), len(audit.shootout.kick_rows))
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_audit tests.test_audit tests.test_live_residuals_hook -v
```

- [ ] **Step 3: Add knockout data to the frozen snapshot**

Remove snapshot persistence from `_match_analysis_bundle_cached` and add `_persist_pre_match_snapshot(match, bundle, knockout_prediction, penalty_context, repo)`. Call it in `render_prediction_lab` immediately after computing the knockout prediction and penalty context; for group-stage matches pass both optional values as `None`. This keeps one immutable payload and makes the knockout section available before insertion:

```python
"knockout": {
    "extra_time": {
        "expected_xg": [home_et_xg, away_et_xg],
        "mode_score": "0-0",
        "conditional": {"home": p_home_et, "draw": p_draw_et, "away": p_away_et},
        "reach_shootout": p_draw_90 * p_draw_et,
    },
    "shootout": {
        "conditional": {"home": p_home_pen, "away": p_away_pen},
        "players": penalty_context.player_rows,
        "coverage": penalty_context.coverage,
    },
}
```

The saved payload must be pre-kickoff and idempotent under the existing snapshot key.

- [ ] **Step 4: Implement pure phase evaluation**

Compute regulation against `regulation_goals_*`; extra-time outcome against `extra_time_goals_*`; shootout winner against `shootout_goals_*`. For each kick, look up the frozen taker conversion, use the global prior if the actual taker was not predicted, and calculate binary Brier. Report likely-on-field and first-five probabilities without treating absence as a failed kick prediction.

- [ ] **Step 5: Persist versioned audit rows without contaminating EMA**

Use a run label `live-wc2026-knockout-phase-v1` and markets `ET_OUTCOME`, `REACH_SHOOTOUT`, `SHOOTOUT_WINNER`, `SHOOTOUT_KICK`. Keep existing `live-wc2026-v1` 1X2 rows regulation-only. Correction overwrites/deactivates rows tied to the old settlement version.

- [ ] **Step 6: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_audit tests.test_audit tests.test_live_residuals_hook tests.test_prediction_persistence -v
git add src/wcpredict/knockout_audit.py src/wcpredict/ui/pages.py src/wcpredict/repository.py tests/test_knockout_audit.py tests/test_audit.py tests/test_live_residuals_hook.py
git commit -m "feat(audit): compare knockout predictions by phase"
```

Expected: PASS.

---

### Task 7: Flujo guiado de cierre en Streamlit

**Files:**
- Create: `src/wcpredict/ui/knockout_settlement.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/theme.py`
- Create: `tests/test_knockout_settlement_ui.py`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_streamlit_smoke.py`

**Interfaces:**
- Produces: `render_knockout_settlement(repo, match, existing_result, evidence_dir) -> int | None`.
- Consumes repository/import/validation/settlement interfaces from Tasks 1–6.

Define the pure UI-state contracts in the new module:

```python
@dataclass(frozen=True)
class SettlementSections:
    visible_periods: tuple[str, ...]
    show_extra_time_score: bool
    show_shootout: bool

@dataclass(frozen=True)
class KnockoutSettlementDraft:
    phase_result: MatchPhaseResultInput
    kicks: tuple[ShootoutKickInput, ...]
    imported_periods: frozenset[str]
    goalkeeper_a_id: int | None
    goalkeeper_b_id: int | None
```

- [ ] **Step 1: Write failing UI-state and contract tests**

```python
def test_regulation_path_hides_extra_time_and_shootout(self):
    state = build_settlement_sections("regulation")
    self.assertEqual(("first_half", "second_half", "regulation_total"), state.visible_periods)
    self.assertFalse(state.show_shootout)

def test_shootout_path_requires_two_goalkeepers_and_valid_sequence(self):
    errors = validate_settlement_draft(shootout_draft_without_keepers)
    self.assertIn("portero", " ".join(errors).lower())

def test_pages_uses_dedicated_knockout_settlement_component(self):
    source = Path("src/wcpredict/ui/pages.py").read_text(encoding="utf-8")
    self.assertIn("render_knockout_settlement(", source)
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement_ui tests.test_app_contract tests.test_streamlit_smoke -v
```

- [ ] **Step 3: Build the guided state model before Streamlit widgets**

Create pure helpers `build_settlement_sections(decided_in: str) -> SettlementSections`, `period_statuses(decided_in: str, imported_periods: set[str], issues: list[PhaseValidationIssue]) -> dict[str, str]` and `validate_settlement_draft(draft: KnockoutSettlementDraft) -> tuple[str, ...]`. Period statuses are exactly `imported`, `pending`, `not_played`, `mismatch`. Cumulative periods are optional and never leave a played match in `pending` by themselves.

- [ ] **Step 4: Render phase cards and imports**

For each visible period, render one card containing its label, status, JSON uploader, reviewed checkbox and import button. Store evidence under `data/evidence/reviewed-json/` by SHA-256 as the current importer does. Call `import_deep_match_period` with the selected canonical period and invalidate only match-analysis/database-signature caches.

- [ ] **Step 5: Render scores and shootout editor**

Use explicit inputs for regulation and extra-time goals. Populate player selectors from the selected teams' registered squads. Choose one defending goalkeeper per team; assign it to every rival kick. Use a dynamic editor for sequence/team/taker/outcome, calculate the tally from rows and display validation errors before enabling final closure.

- [ ] **Step 6: Separate draft from definitive close**

Draft imports/stat rows remain available without finishing `matches.status`. The primary close button calls `settle_knockout_match_versioned` only when all blocking checks pass. Group-stage matches continue using the existing settlement form unchanged.

- [ ] **Step 7: Render phase audit sections**

Keep Claude's knockout advance/funnel card. Under the closed-match audit, render collapsible sections `90 minutos`, `Prórroga` and `Penaltis`. Do not reintroduce the old group-stage 1X2 table. A non-played phase displays `No se disputó`.

- [ ] **Step 8: Run UI tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement_ui tests.test_app_contract tests.test_streamlit_smoke tests.test_postmatch_capture_ui -v
git add src/wcpredict/ui/knockout_settlement.py src/wcpredict/ui/pages.py src/wcpredict/ui/theme.py tests/test_knockout_settlement_ui.py tests/test_app_contract.py tests/test_streamlit_smoke.py
git commit -m "feat(ui): guide knockout phase capture and audit"
```

Expected: PASS.

---

### Task 8: Marcador por fase en el bracket

**Files:**
- Modify: `src/wcpredict/ui/bracket.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/theme.py`
- Modify: `tests/test_app_contract.py`
- Create: `tests/test_bracket_scores.py`

**Interfaces:**
- Extends slot view with `penalty_score: [int, int] | None` and `decided_in: str | None`.
- Extends `_team_row(team: dict, score_val: int | None, penalty_score_val: int | None, is_winner: bool) -> str`.

- [ ] **Step 1: Write failing HTML rendering tests**

```python
def test_shootout_score_is_small_and_attached_to_each_team(self):
    html = render_bracket([closed_slot(score=[2, 2], penalty_score=[5, 4], winner="home")])
    self.assertIn('class="bracket-team-penalty-score">(5)</span>', html)
    self.assertIn('class="bracket-team-penalty-score">(4)</span>', html)

def test_extra_time_match_has_discreet_label(self):
    html = render_bracket([closed_slot(score=[2, 1], decided_in="extra_time")])
    self.assertIn('class="bracket-decision-label">Prórroga</span>', html)
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_bracket_scores tests.test_app_contract -v
```

- [ ] **Step 3: Populate slots from active phase results**

Bulk-fetch active `match_phase_results`. Use official regulation-plus-extra-time score as `score`; use shootout tallies separately. Retain legacy optional-column fallback for older closed matches.

- [ ] **Step 4: Render and style the small tally**

Render `2 <span class="bracket-team-penalty-score">(5)</span>` inside the existing score area. Use smaller font, muted color and no extra column width. Add `Prórroga` only for `decided_in=extra_time`; shootout cards are self-explanatory through the parenthesized score. Winner styling continues to come from the explicit `winner` field.

- [ ] **Step 5: Run tests and commit**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_bracket_scores tests.test_app_contract tests.test_knockout_bracket -v
git add src/wcpredict/ui/bracket.py src/wcpredict/ui/pages.py src/wcpredict/ui/theme.py tests/test_bracket_scores.py tests/test_app_contract.py
git commit -m "feat(bracket): show extra time and shootout scores"
```

Expected: PASS.

---

### Task 9: Integración, rendimiento, documentación y publicación

**Files:**
- Modify: `README.md` only if the operational closing workflow is documented there.
- Modify: `docs/superpowers/specs/2026-06-29-knockout-phase-stats-and-audit-design.md` only to record implementation-confirmed deviations.
- Test: all files under `tests/`.

**Interfaces:**
- Verifies every interface and constraint from Tasks 1–8.

- [ ] **Step 1: Add an end-to-end regression test**

Create `tests/test_knockout_phase_integration.py` with a temporary tournament containing one regulation, one extra-time and one shootout result. Import atomic stats, close each match, resolve the bracket, build the next prediction and assert:

```python
with repo.session() as con:
    training = con.execute(
        "SELECT goals_a, goals_b FROM historical_matches "
        "WHERE source_id='reviewed_settlement' AND source_row_key=?",
        (str(extra_time_match_id),),
    ).fetchone()
self.assertEqual((1, 1), tuple(training))
self.assertNotEqual(base_ko.cond_home_wins_et_given_draw_90, next_ko.cond_home_wins_et_given_draw_90)
self.assertEqual(base_ko.home_wins_90, next_ko.home_wins_90)
evidence = repo.list_penalty_evidence(("Spain",), next_kickoff)
self.assertEqual(1, sum(row["source_provider"] == "world_cup_2026_manual" for row in evidence))
html = render_bracket([shootout_slot])
self.assertIn('class="bracket-team-score">2', html)
self.assertIn('class="bracket-team-penalty-score">(5)</span>', html)
```

- [ ] **Step 2: Verify query count and caching**

Opening an eliminatoria must not run training or Monte Carlo. Extra-time training rows and phase results are cached by database signature and model version. Penalty contexts continue loading precomputed JSON; a newly observed shootout only marks future relevant artifacts stale for explicit offline regeneration.

- [ ] **Step 3: Run the complete verification suite**

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
python -m compileall -q src scripts
git diff --check
```

Expected: all tests PASS, compile exit code `0`, diff check empty.

- [ ] **Step 4: Run local browser QA**

Verify at desktop and 390×844:

- regulation closure hides unplayed phases;
- extra-time imports show atomic and optional cumulative cards;
- mismatches block definitive close with a readable explanation;
- shootout selectors contain only the correct squads and goalkeepers;
- corrected closure replaces active learning rows;
- closed audit keeps the knockout bars and displays phase sections;
- bracket displays `2 (5)` / `2 (4)` without horizontal overflow.

Expected: no application errors, no unexpected horizontal overflow, no old closed-match 1X2 table.

- [ ] **Step 5: Regenerate only affected precomputed penalty artifacts**

After real tournament shootouts exist, run `scripts/precompute_penalty_contexts.py` for future unresolved fixtures. Review the changed JSONs and confirm model version, team pair, 25,000 simulations and updated fingerprint. Do not regenerate completed fixtures or run Monte Carlo in Streamlit.

- [ ] **Step 6: Commit final integration**

Always stage the integration test explicitly:

```powershell
git add -- tests/test_knockout_phase_integration.py
```

If browser QA or implementation-confirmed behavior changed documentation, stage only those changed files:

```powershell
git add -- README.md docs/superpowers/specs/2026-06-29-knockout-phase-stats-and-audit-design.md
```

If and only if reviewed future-fixture penalty artifacts changed in Step 5, stage their exact filenames individually. Then inspect and commit:

```powershell
git status --short
git diff --cached --stat
git commit -m "test(knockout): verify phase capture end to end"
```

Never stage `.codex-remote-attachments/`, `data/cache/`, `output/` or local screenshots.

- [ ] **Step 7: Push only after fresh verification**

```powershell
git push origin main
```

Expected: `main -> main`; the existing GitHub warning for the SQLite file may appear but must not be treated as a failed push.
