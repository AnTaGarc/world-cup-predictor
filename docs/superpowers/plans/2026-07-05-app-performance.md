# App Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reducir la sobrelectura de SQLite y el trabajo repetido de inicialización en los flujos principales de Streamlit sin cambiar resultados funcionales.

**Architecture:** Los métodos del repositorio aceptarán filtros opcionales que se aplican dentro de SQL, conservando el comportamiento completo cuando no se pasan. La UI pedirá únicamente la competición y los dos equipos activos, y reutilizará el repositorio cacheado para el refresco diario.

**Tech Stack:** Python 3.12, SQLite, Streamlit, `unittest`.

## Global Constraints

- Preservar todos los datos locales y no alterar las fórmulas del modelo.
- No añadir dependencias.
- Seguir TDD: cada comportamiento nuevo debe fallar antes de implementar.
- Mantener compatibilidad con scripts que llaman a los métodos sin filtros.

---

### Task 1: Filtrar partidos y cobertura en SQLite

**Files:**
- Modify: `src/wcpredict/repository.py`
- Modify: `src/wcpredict/ui/pages.py`
- Test: `tests/test_database_repository.py`
- Test: `tests/test_app_contract.py`

**Interfaces:**
- Produces: `Repository.list_matches(competition: str | None = None) -> list[Match]`
- Produces: `Repository.get_all_match_evidence_statuses(competition: str | None = None) -> dict[int, dict]`

- [ ] **Step 1: Write failing repository tests**

Add tests which seed matches in two competitions, call both methods with `competition="FIFA World Cup 2026"`, and assert only the requested match is returned.

- [ ] **Step 2: Run tests to verify RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository -v`

Expected: failure because both methods reject the `competition` keyword.

- [ ] **Step 3: Implement SQL-level optional filters**

Build `WHERE m.competition = ?` and its parameter tuple only when `competition` is supplied. Place it before `ORDER BY`/`GROUP BY`, so correlated evidence subqueries only execute for selected fixtures.

- [ ] **Step 4: Wire the UI to the filtered APIs**

Change `_matches_cached` to call `list_matches(competition="FIFA World Cup 2026")`, make `_list_matches()` return that cached list directly, and make `_all_evidence_statuses_cached` request the same competition.

- [ ] **Step 5: Run focused tests to verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository tests.test_app_contract -v`

Expected: all focused tests pass.

### Task 2: Filtrar perfiles profundos por los equipos del partido

**Files:**
- Modify: `src/wcpredict/repository.py`
- Modify: `src/wcpredict/database.py`
- Modify: `src/wcpredict/ui/pages.py`
- Test: `tests/test_database_repository.py`
- Test: `tests/test_app_contract.py`

**Interfaces:**
- Produces: `Repository.list_deep_team_metric_observations_before(as_of_utc: datetime, team_names: tuple[str, ...] | None = None) -> list[dict]`

- [ ] **Step 1: Write failing filtered-profile test**

Seed verified team observations for three teams and assert `team_names=("Morocco", "France")` excludes the third team while a call without `team_names` still returns all rows.

- [ ] **Step 2: Run test to verify RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository.DatabaseRepositoryTests.test_deep_team_metric_observations_can_filter_teams -v`

Expected: failure because the method rejects `team_names`.

- [ ] **Step 3: Add the SQL filter and hot-path index**

Apply `o2.subject_name IN (?, ...)` inside the deduplication subquery. Add `idx_observations_team_profile` on `(subject_type, subject_name, evidence_status, match_id, metric, id)`.

- [ ] **Step 4: Request only the active teams from prediction paths**

Pass `(team_a, team_b)` from `_match_analysis_bundle_cached` and `_team_volume_context_from_profiles_cached`. Leave offline training/backtest scripts unchanged so they retain full-history behavior.

- [ ] **Step 5: Run focused tests to verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository tests.test_app_contract -v`

Expected: all focused tests pass.

### Task 3: Eliminar la reinicialización redundante del refresco diario

**Files:**
- Modify: `src/wcpredict/ui/pages.py`
- Test: `tests/test_app_contract.py`

**Interfaces:**
- Consumes: `_repo() -> Repository`
- Produces: `_refresh_current_world_cup_banks_cached(...)` reutiliza el recurso inicializado.

- [ ] **Step 1: Write failing contract test**

Assert that the body of `_refresh_current_world_cup_banks_cached` contains `repo = _repo()` and does not contain `repo.initialize()`.

- [ ] **Step 2: Run test to verify RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract.AppContractTests.test_daily_refresh_reuses_initialized_repository -v`

Expected: failure because the function constructs and initializes another repository.

- [ ] **Step 3: Reuse `_repo()`**

Replace the local constructor and initialization with `repo = _repo()`.

- [ ] **Step 4: Run contract tests to verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract -v`

Expected: 55 or more tests pass.

### Task 4: Medición y verificación integral

**Files:**
- Modify: `docs/superpowers/2026-07-05-app-performance-design.md`

**Interfaces:**
- Consumes: base local copiada en el worktree.
- Produces: cifras antes/después registradas en el diseño.

- [ ] **Step 1: Repetir benchmarks**

Measure `list_matches(competition=...)`, `list_deep_team_metric_observations_before(..., team_names=...)`, and `get_all_match_evidence_statuses(competition=...)` on the copied 66.6 MB database. Record row counts and elapsed seconds.

- [ ] **Step 2: Run static validation**

Run: `git diff --check`

Expected: exit 0 without whitespace errors.

- [ ] **Step 3: Run the full suite**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v`

Expected: all tests pass with a timeout of at least five minutes.

- [ ] **Step 4: Inspect scope**

Run: `git status --short` and `git diff --stat`.

Expected: only source, tests and documentation are modified; `data/worldcup.sqlite` remains unstaged.
