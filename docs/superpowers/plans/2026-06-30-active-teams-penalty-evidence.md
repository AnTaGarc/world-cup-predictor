# Active-Team Penalty Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build auditable recent-shootout and goalkeeper-penalty evidence for World Cup teams still alive, integrate it into the fixed-starting-goalkeeper simulation, and finish the optional 120-minute aggregate validation.

**Architecture:** Keep taker attempts, goalkeeper-faced attempts, and historical international shootout kicks in separate persistent structures. A dynamic bracket selector controls collection and precomputation. Repository inputs are cut off at kickoff, converted into recency-weighted Bayesian profiles, fingerprinted, and served only through versioned precomputed artifacts.

**Tech Stack:** Python 3.12, SQLite, `unittest`, Streamlit, `requests`, existing Transfermarkt parser/cache, FIFA/UEFA reviewed source fixtures.

## Global Constraints

- Only teams still alive in the resolved World Cup 2026 bracket may be refreshed or precomputed.
- Germany, South Africa, Netherlands, and Japan are excluded in the current bracket state; this is asserted from data, not hard-coded as a permanent blacklist.
- Use each live team's three most recent senior official international competitions; exclude friendlies, youth football, and club shootouts from the team-experience signal.
- Goalkeeper history includes senior club and international penalties, both in play and in shootouts.
- Never mix goalkeeper-only evidence into taker or team conversion samples.
- Never use evidence dated on or after match kickoff.
- Keep the starting goalkeeper fixed unless a confirmed pre-match goalkeeper substitution exists.
- Do not mutate, delete, or stage the user's existing SQLite, model artifact, reviewed JSON, cache, or output changes.
- Use TDD and commit each independently green task.

---

### Task 1: Dynamic live-team selector

**Files:**
- Modify: `src/wcpredict/transfermarkt_penalties.py`
- Modify: `scripts/fetch_transfermarkt_penalties.py`
- Modify: `scripts/precompute_penalty_contexts.py`
- Test: `tests/test_transfermarkt_penalties.py`
- Test: `tests/test_penalty_context_cache.py`

**Interfaces:**
- Produces: `active_knockout_teams(repo: Repository) -> list[str]`
- Consumes: resolved `knockout_bracket`, active `settlement_versions`, and `_decide_winner` results already propagated into later bracket slots.

- [ ] **Step 1: Write failing selector tests**

```python
def test_active_knockout_teams_excludes_closed_losers(self):
    # Seed R32, settle Germany, South Africa, Netherlands, and Japan as losers,
    # resolve the bracket, then assert none appears while each winner does.
    active = active_knockout_teams(self.repo)
    self.assertNotIn("Germany", active)
    self.assertNotIn("South Africa", active)
    self.assertNotIn("Netherlands", active)
    self.assertNotIn("Japan", active)

def test_active_knockout_teams_is_not_a_permanent_blacklist(self):
    # An unsettled test bracket containing Germany must still return Germany.
    self.assertIn("Germany", active_knockout_teams(self.repo))
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_transfermarkt_penalties -v`

Expected: import failure for `active_knockout_teams`.

- [ ] **Step 3: Implement the selector and route both scripts through it**

```python
def active_knockout_teams(repo: Repository) -> list[str]:
    with closing(sqlite3.connect(repo.path, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT DISTINCT t.name FROM knockout_bracket kb "
            "JOIN teams t ON t.id IN (kb.home_team_id, kb.away_team_id) "
            "LEFT JOIN matches m ON m.id=kb.match_id "
            "LEFT JOIN settlement_versions sv ON sv.match_id=m.id AND sv.active=1 "
            "WHERE kb.competition=? AND (kb.match_id IS NULL OR sv.id IS NULL)",
            (COMPETITION,),
        ).fetchall()
    return sorted({canonical_team_name(str(row["name"])) for row in rows})
```

Use this function as the default team list in `fetch_transfermarkt_penalties.py`. In `precompute_penalty_contexts.py`, skip any candidate whose two teams are not both active.

- [ ] **Step 4: Run focal tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_transfermarkt_penalties tests.test_penalty_context_cache -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/wcpredict/transfermarkt_penalties.py scripts/fetch_transfermarkt_penalties.py scripts/precompute_penalty_contexts.py tests/test_transfermarkt_penalties.py tests/test_penalty_context_cache.py
git commit -m "feat(penalties): target only active knockout teams"
```

### Task 2: Separate goalkeeper penalty persistence

**Files:**
- Modify: `src/wcpredict/database.py`
- Modify: `src/wcpredict/repository.py`
- Test: `tests/test_database_repository.py`
- Test: `tests/test_penalty_tournament_evidence.py`

**Interfaces:**
- Produces: `Repository.save_goalkeeper_penalty_attempts(rows: list[dict]) -> int`
- Produces: `Repository.list_goalkeeper_penalty_attempts(goalkeeper_name: str, before_utc: datetime) -> list[dict]`

- [ ] **Step 1: Write failing migration and repository tests**

```python
def test_goalkeeper_attempts_are_idempotent_and_cut_off_at_kickoff(self):
    rows = [
        keeper_row("before", "2026-06-27", "saved"),
        keeper_row("after", "2026-06-29", "scored"),
    ]
    self.assertEqual(2, repo.save_goalkeeper_penalty_attempts(rows))
    self.assertEqual(2, repo.save_goalkeeper_penalty_attempts(rows))
    evidence = repo.list_goalkeeper_penalty_attempts("Bounou", kickoff)
    self.assertEqual(["before"], [row["source_row_key"] for row in evidence])
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository tests.test_penalty_tournament_evidence -v`

Expected: missing table/repository methods.

- [ ] **Step 3: Add the table and repository methods**

```sql
CREATE TABLE IF NOT EXISTS goalkeeper_penalty_attempts (
    id INTEGER PRIMARY KEY,
    goalkeeper_name TEXT NOT NULL,
    transfermarkt_player_id TEXT,
    attempted_on TEXT,
    competition TEXT,
    phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    taker_name TEXT,
    opponent_team TEXT,
    match_label TEXT,
    source_provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_provider, source_row_key)
);
```

Normalize dates through `penalty_attempt_date`; accepted outcomes are `scored`, `saved`, `off_target`, `woodwork`, and the auditable but unscored `unknown_miss`. Do not expose these rows through `list_penalty_attempts()`.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_database_repository tests.test_penalty_tournament_evidence -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/wcpredict/database.py src/wcpredict/repository.py tests/test_database_repository.py tests/test_penalty_tournament_evidence.py
git commit -m "feat(data): store goalkeeper penalty evidence separately"
```

### Task 3: Transfermarkt goalkeeper collector

**Files:**
- Modify: `src/wcpredict/transfermarkt_penalties.py`
- Modify: `scripts/fetch_transfermarkt_penalties.py`
- Test: `tests/test_transfermarkt_penalties.py`

**Interfaces:**
- Produces: `goalkeeper_penalty_url(player_name: str, player_id: str) -> str`
- Produces: `parse_goalkeeper_penalty_attempts(html: str, **identity) -> list[dict]`
- Consumes: live-team targets from Task 1 and persistence from Task 2.

- [ ] **Step 1: Write failing parser tests for saved, scored, and missed-by-taker rows**

```python
def test_goalkeeper_parser_distinguishes_save_from_off_target(self):
    rows = parse_goalkeeper_penalty_attempts(
        goalkeeper_html(saved=1, scored=1, off_target=1),
        goalkeeper_name="Yassine Bounou",
        transfermarkt_player_id="207834",
        source_url="https://example.test/keeper",
        fetched_at_utc=NOW,
    )
    self.assertEqual(["saved", "scored", "off_target"], [row["outcome"] for row in rows])
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_transfermarkt_penalties -v`

Expected: parser and URL imports fail.

- [ ] **Step 3: Implement keeper-page parsing and collection**

```python
def goalkeeper_penalty_url(player_name: str, player_id: str) -> str:
    return f"{TRANSFERMARKT_BASE}/{slugify_player_name(player_name)}/elfmeterstatistik/spieler/{player_id}"
```

Extend `TableParser` with goalkeeper-section defaults. Parse each detailed row into the separate goalkeeper schema. If a generic missed section does not distinguish save from off-target, persist `outcome="unknown_miss"` for audit but exclude it from save-rate denominators until reviewed.

Only goalkeeper targets from active teams are fetched. Reuse cached HTML and existing Transfermarkt identities.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_transfermarkt_penalties -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/wcpredict/transfermarkt_penalties.py scripts/fetch_transfermarkt_penalties.py tests/test_transfermarkt_penalties.py
git commit -m "feat(penalties): collect goalkeeper penalty records"
```

### Task 4: Reviewed international shootout dataset

**Files:**
- Create: `data/fixtures/active_team_shootout_kicks.csv`
- Create: `scripts/import_historical_shootouts.py`
- Modify: `src/wcpredict/database.py`
- Modify: `src/wcpredict/repository.py`
- Create: `tests/test_historical_shootouts.py`

**Interfaces:**
- Produces: `Repository.save_historical_shootouts(shootouts: list[dict], kicks: list[dict]) -> tuple[int, int]`
- Produces: `Repository.list_historical_shootout_kicks(team_names: tuple[str, ...], before_utc: datetime) -> list[dict]`

- [ ] **Step 1: Write failing import, idempotency, scope, and cutoff tests**

```python
def test_import_keeps_only_three_latest_senior_official_competitions(self):
    imported = import_rows(FIXTURE, active_teams={"Morocco"})
    self.assertLessEqual(len({row.competition_edition for row in imported.shootouts}), 3)
    self.assertTrue(all(row.senior and row.official for row in imported.shootouts))

def test_eliminated_team_rows_are_preserved_but_not_refreshed(self):
    before = repo.count_historical_shootouts("Netherlands")
    import_fixture(repo, active_teams={"Morocco"})
    self.assertEqual(before, repo.count_historical_shootouts("Netherlands"))
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_historical_shootouts -v`

Expected: missing importer and tables.

- [ ] **Step 3: Add normalized tables and importer**

```sql
CREATE TABLE IF NOT EXISTS historical_shootouts (
    id INTEGER PRIMARY KEY,
    played_on TEXT NOT NULL,
    competition TEXT NOT NULL,
    competition_edition TEXT NOT NULL,
    round_name TEXT,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    winner_team TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL,
    UNIQUE(source_provider, source_row_key)
);
CREATE TABLE IF NOT EXISTS historical_shootout_kicks (
    id INTEGER PRIMARY KEY,
    shootout_id INTEGER NOT NULL REFERENCES historical_shootouts(id),
    sequence_number INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    player_name TEXT,
    goalkeeper_name TEXT,
    outcome TEXT NOT NULL,
    source_row_key TEXT NOT NULL UNIQUE
);
```

The CSV columns are `played_on,competition,competition_edition,round_name,team_a,team_b,winner_team,sequence_number,team_name,player_name,goalkeeper_name,outcome,source_provider,source_url,source_row_key,retrieved_at_utc`. Populate it only from cited FIFA/UEFA/confederation or official match-report evidence for active teams.

- [ ] **Step 4: Validate fixture coverage and import GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_historical_shootouts -v`

Run: `python scripts/import_historical_shootouts.py --db .tmp/penalty-evidence-audit.sqlite --dry-run`

Expected: tests pass; dry run reports active teams, competitions, shootouts, kicks, missing fields, and zero writes.

- [ ] **Step 5: Commit**

```powershell
git add data/fixtures/active_team_shootout_kicks.csv scripts/import_historical_shootouts.py src/wcpredict/database.py src/wcpredict/repository.py tests/test_historical_shootouts.py
git commit -m "feat(data): add reviewed international shootouts"
```

### Task 5: Bayesian goalkeeper and team-experience profiles

**Files:**
- Modify: `src/wcpredict/penalty_profiles.py`
- Modify: `src/wcpredict/penalty_history_model.py`
- Modify: `src/wcpredict/penalty_context_cache.py`
- Test: `tests/test_penalty_profiles.py`
- Test: `tests/test_penalty_history_model.py`
- Test: `tests/test_penalty_context_cache.py`

**Interfaces:**
- Produces: `build_goalkeeper_profile(player, goalkeeper_attempts, deep_save_rate, as_of) -> GoalkeeperPenaltyProfile`
- Produces: `build_team_shootout_experience(team_name, kicks, as_of) -> TeamShootoutExperience`

- [ ] **Step 1: Write failing weighting and separation tests**

```python
def test_recent_shootout_save_outweighs_old_in_play_save(self):
    recent = keeper_profile([keeper_attempt("2026-01-01", "shootout", "saved")])
    old = keeper_profile([keeper_attempt("2016-01-01", "regular", "saved")])
    self.assertGreater(recent.effective_saves, old.effective_saves)

def test_goalkeeper_rows_do_not_change_team_taker_conversion(self):
    baseline = build_penalty_team_profile("Morocco", taker_attempts)
    enriched = build_penalty_team_profile("Morocco", taker_attempts + goalkeeper_only_rows)
    self.assertEqual(baseline, enriched)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_penalty_profiles tests.test_penalty_history_model tests.test_penalty_context_cache -v`

Expected: signature/weighting assertions fail.

- [ ] **Step 3: Implement recency-weighted posteriors and repository wiring**

```python
weight = (SHOOTOUT_WEIGHT if phase == "shootout" else REGULAR_WEIGHT)
weight *= 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
alpha = GLOBAL_PENALTY_SAVE * PRIOR_ATTEMPTS + weighted_saves
beta = (1.0 - GLOBAL_PENALTY_SAVE) * PRIOR_ATTEMPTS + weighted_goals
penalty_save_rate = alpha / (alpha + beta)
```

Keep off-target/woodwork in `faced_total` diagnostics but outside saves and goals. Build fixed starter profiles from `list_goalkeeper_penalty_attempts`. Add international kicks to taker histories with `phase="shootout"`; apply only a small capped team-experience shift and never a second win/loss bonus.

Bump `PENALTY_MODEL_VERSION`, include both new datasets and active-team state in the fingerprint, and reject old artifacts.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_penalty_profiles tests.test_penalty_history_model tests.test_penalty_context_cache -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/wcpredict/penalty_profiles.py src/wcpredict/penalty_history_model.py src/wcpredict/penalty_context_cache.py tests/test_penalty_profiles.py tests/test_penalty_history_model.py tests/test_penalty_context_cache.py
git commit -m "feat(penalties): model keeper and recent shootout evidence"
```

### Task 6: Coverage UI and honest fallbacks

**Files:**
- Modify: `src/wcpredict/penalty_history_model.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/theme.py`
- Test: `tests/test_app_contract.py`
- Test: `tests/test_penalty_history_model.py`

**Interfaces:**
- Extends: `PenaltyMatchContext` with starter goalkeeper and source coverage diagnostics.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_penalty_context_discloses_keeper_and_shootout_coverage(self):
    self.assertIn("Porteros titulares usados", source)
    self.assertIn("Penaltis afrontados", source)
    self.assertIn("Tandas recientes cubiertas", source)
    self.assertIn("Corte de datos", source)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract tests.test_penalty_history_model -v`

Expected: missing labels/context fields.

- [ ] **Step 3: Add diagnostics without changing the advancement headline**

Show starter name, penalty-specific rate, `saved/goals/off_target`, split by regular/shootout, source, cutoff, three-competition coverage, and prior-only warnings. If an artifact is absent or stale, label the displayed fallback as incomplete and do not run 25,000 simulations in Streamlit.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract tests.test_penalty_history_model -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/wcpredict/penalty_history_model.py src/wcpredict/ui/pages.py src/wcpredict/ui/theme.py tests/test_app_contract.py tests/test_penalty_history_model.py
git commit -m "feat(ui): explain penalty evidence coverage"
```

### Task 7: Finish 120-minute validation, audit, and integration

**Files:**
- Verify: `src/wcpredict/ui/knockout_settlement.py`
- Verify: `src/wcpredict/match_phases.py`
- Verify: `tests/test_knockout_settlement_ui.py`
- Verify: `tests/test_phase_stats_persistence.py`
- Modify only if tests expose a defect.

**Interfaces:**
- Consumes the already implemented four-atomic-period plus optional-120-total workflow.

- [ ] **Step 1: Run the complete period/settlement integration set**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_phase_integration tests.test_knockout_settlement tests.test_knockout_settlement_ui tests.test_phase_stats_persistence tests.test_match_phases -v`

Expected: all tests pass and the optional 120 total detects additive mismatches.

- [ ] **Step 2: Re-audit Germany-Paraguay read-only**

Confirm the validator reports Germany `shots_on_target` parts `7` vs total `6`, and Paraguay `saves` parts `7` vs total `6`. Do not alter reviewed data without user approval.

- [ ] **Step 3: Run all penalty and importer tests**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_transfermarkt_penalties tests.test_historical_shootouts tests.test_penalty_profiles tests.test_penalty_tournament_evidence tests.test_penalty_history_model tests.test_penalty_substitution_model tests.test_penalty_context_cache tests.test_app_contract -v`

Expected: all tests pass.

- [ ] **Step 4: Run the full suite**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

- [ ] **Step 5: Build reviewed artifacts without touching user data first**

Run importers and precomputation against a copied temporary SQLite database and temporary artifact directory. Verify active-team scope, source coverage, fingerprints, deterministic results, and UI deserialization.

- [ ] **Step 6: Present data mutations for approval**

List exact source rows and artifacts proposed for the real `data/worldcup.sqlite` and `data/precomputed/penalties`. Apply them only after explicit user approval; code integration does not require staging the database.

- [ ] **Step 7: Commit any verification-only corrections**

If verification required corrections, stage only the bounded settlement files:

```powershell
git add src/wcpredict/ui/knockout_settlement.py src/wcpredict/match_phases.py tests/test_knockout_settlement_ui.py tests/test_phase_stats_persistence.py
git commit -m "fix: close penalty evidence verification gaps"
```

If no correction was required, record the passing commands in the handoff and create no empty commit.

- [ ] **Step 8: Merge locally and rerun the full suite on `main`**

Fast-forward the feature branch only after confirming the user's dirty data files are preserved and no overlapping paths will be overwritten. Remove the owned worktree only after the merged full suite passes.
