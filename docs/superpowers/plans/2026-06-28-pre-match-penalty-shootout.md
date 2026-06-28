# Pre-Match Penalty Shootout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Predict each knockout team's conditional probability of winning a penalty shootout by simulating logical pre-match lineups, role-compatible substitutions, extra time, taker selection, goalkeeper effects, and sudden death across the full registered squads.

**Architecture:** A canonical 32-team snapshot drives complete history collection while the bracket catches up. Pure modules build Bayesian player/goalkeeper profiles, simulate match-state-aware substitution paths to minute 120, and simulate shootouts; `penalty_history_model.py` orchestrates them behind its existing public API. An external script runs the expensive model after both teams complete the group stage, stores a versioned JSON artifact, and both desktop and online Streamlit only deserialize that artifact.

**Tech Stack:** Python 3.12, standard-library `random`/`math`/`csv`, SQLite repository, existing Streamlit cache/fragment boundaries, `unittest` and Streamlit `AppTest`.

## Global Constraints

- Pre-match only; no live substitution or event ingestion.
- Include every registered squad member, including players with zero tournament minutes.
- Only players remaining on the field at minute 120 may take kicks.
- Default substitution configuration: five changes plus one additional change in extra time; keep it configurable.
- Preserve the existing regulation and extra-time probability formulas.
- Missing history uses a global prior, never a zero conversion rate.
- General goalkeeper save percentage is only a weak fallback, not penalty save percentage.
- Simulations are deterministic for the same match/model seed, run outside Streamlit, and persist as deployable JSON.
- Normal desktop/web rendering never launches the 25,000-simulation Monte Carlo; missing artifacts use the fast legacy fallback and report the detailed model as pending.
- Ambiguous Transfermarkt identities require review and are never auto-accepted.
- Do not commit the user's changing `data/worldcup.sqlite` or unrelated cache/output artifacts.

---

## File Structure

- Create `data/fixtures/world_cup_2026_penalty_teams.csv`: canonical snapshot of the 32 qualified teams.
- Modify `src/wcpredict/transfermarkt_penalties.py`: snapshot reconciliation, full-squad targets, and identity reuse.
- Modify `scripts/fetch_transfermarkt_penalties.py`: snapshot-aware, resumable collection.
- Create `src/wcpredict/penalty_profiles.py`: Bayesian taker and goalkeeper profiles.
- Create `src/wcpredict/penalty_substitution_model.py`: starting XI, score path, and logical substitution trajectories.
- Create `src/wcpredict/penalty_shootout_simulator.py`: taker order, early termination, and sudden death.
- Rewrite `src/wcpredict/penalty_history_model.py`: orchestration and compatibility API.
- Modify `src/wcpredict/ui/pages.py`: cached context and explanatory knockout UI.
- Add focused tests beside the existing penalty, knockout, repository, and Streamlit contracts.

### Task 1: Canonical qualified teams and complete identity targets

**Files:**
- Create: `data/fixtures/world_cup_2026_penalty_teams.csv`
- Modify: `src/wcpredict/transfermarkt_penalties.py`
- Modify: `src/wcpredict/repository.py`
- Modify: `scripts/fetch_transfermarkt_penalties.py`
- Modify: `tests/test_transfermarkt_penalties.py`

**Interfaces:**
- Produces `load_penalty_team_snapshot(path: Path) -> list[str]`.
- Produces `reconcile_penalty_teams(snapshot: list[str], dynamic: list[str]) -> dict[str, list[str]]`.
- Extends `Repository.list_transfermarkt_player_ids()` to reuse IDs from `penalty_attempts`.
- Changes `player_targets_for_teams()` to include zero-minute registered players.

- [ ] **Step 1: Write failing snapshot, alias, zero-minute, and ID-reuse tests**

```python
def test_penalty_snapshot_contains_exactly_the_user_confirmed_32(self):
    teams = load_penalty_team_snapshot(
        Path(__file__).parents[1] / "data/fixtures/world_cup_2026_penalty_teams.csv"
    )
    self.assertEqual(32, len(teams))
    self.assertEqual(32, len(set(teams)))
    for team in ("USA", "Bosnia and Herzegovina", "Cote d'Ivoire", "Congo DR", "Cape Verde"):
        self.assertIn(team, teams)

def test_full_squad_targets_include_zero_minute_players_and_reuse_attempt_ids(self):
    repo.replace_current_world_cup_players("test", [
        {"player_name": "Starter", "team_name": "United States", "position": "FW", "minutes": 180},
        {"player_name": "Unused", "team_name": "United States", "position": "MF", "minutes": 0},
    ], now)
    repo.save_penalty_attempts([{
        "player_name": "Unused", "team_name": "USA",
        "transfermarkt_player_id": "999", "attempted_on": "2026-01-01",
        "competition": "Test", "phase": "regular", "outcome": "scored",
        "goalkeeper_name": "Keeper", "opponent_team": "Test",
        "minute": "70'", "match_label": "Test",
        "source_provider": "transfermarkt", "source_url": "https://example.test/999",
        "source_row_key": "transfermarkt:999:test", "fetched_at_utc": now.isoformat(),
        "raw": {},
    }])
    targets = player_targets_for_teams(repo, ["USA"])
    self.assertEqual({"Starter", "Unused"}, {row.player_name for row in targets})
    self.assertEqual("999", next(row.transfermarkt_player_id for row in targets if row.player_name == "Unused"))
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_transfermarkt_penalties -v
```

Expected: missing snapshot functions and zero-minute target omission.

- [ ] **Step 3: Add the canonical CSV**

```csv
team_name
Germany
Paraguay
France
Sweden
South Africa
Canada
Netherlands
Morocco
Portugal
Croatia
Spain
Austria
USA
Bosnia and Herzegovina
Belgium
Senegal
Brazil
Japan
Cote d'Ivoire
Norway
Mexico
Ecuador
England
Congo DR
Argentina
Cape Verde
Australia
Egypt
Switzerland
Algeria
Colombia
Ghana
```

- [ ] **Step 4: Implement snapshot loading, reconciliation, and identity reuse**

```python
def load_penalty_team_snapshot(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        teams = [canonical_team_name(row["team_name"]) for row in csv.DictReader(handle)]
    return list(dict.fromkeys(team for team in teams if team))


def reconcile_penalty_teams(snapshot: list[str], dynamic: list[str]) -> dict[str, list[str]]:
    wanted = set(snapshot)
    actual = {canonical_team_name(team) for team in dynamic}
    return {
        "missing_from_bracket": sorted(wanted - actual),
        "unexpected_in_bracket": sorted(actual - wanted),
    }
```

Extend `list_transfermarkt_player_ids()` with distinct non-null IDs from `penalty_attempts`, canonicalizing team names and using `setdefault` so reviewed provider-entity mappings retain priority. Remove the `minutes <= 0` exclusion from `player_targets_for_teams` and sort targets by minutes descending for collection priority.

- [ ] **Step 5: Make the fetch script snapshot-aware**

Add `--team-snapshot` defaulting to the new CSV. Use the snapshot as the collection set and print reconciliation differences against `eligible_penalty_teams(repo)` before fetching. Retain `--teams` as the highest-priority explicit override.

- [ ] **Step 6: Verify and commit Task 1**

Run `tests.test_transfermarkt_penalties` and `tests.test_names`, then:

```powershell
git add data/fixtures/world_cup_2026_penalty_teams.csv src/wcpredict/transfermarkt_penalties.py src/wcpredict/repository.py scripts/fetch_transfermarkt_penalties.py tests/test_transfermarkt_penalties.py
git commit -m "feat(penalties): target all qualified squads"
```

### Task 2: Bayesian taker and goalkeeper profiles

**Files:**
- Create: `src/wcpredict/penalty_profiles.py`
- Create: `tests/test_penalty_profiles.py`

**Interfaces:**
- Produces `PenaltyPlayerProfile`, `GoalkeeperPenaltyProfile`.
- Produces `build_player_profile(player_name, position, attempts, as_of) -> PenaltyPlayerProfile`.
- Produces `build_player_profiles(squad, attempts, as_of) -> dict[str, PenaltyPlayerProfile]`.
- Produces `build_goalkeeper_profile(player, attempts, deep_save_rate) -> GoalkeeperPenaltyProfile`.

- [ ] **Step 1: Write failing profile tests**

```python
def test_missing_history_uses_global_prior_not_zero(self):
    profiles = build_player_profiles(
        [{"player_name": "Unknown", "position": "FW"}], [], date(2026, 6, 28)
    )
    self.assertAlmostEqual(0.76, profiles["Unknown"].conversion, places=2)
    self.assertEqual(0, profiles["Unknown"].attempts)

def test_recent_shootout_attempts_outweigh_old_regular_attempts(self):
    recent = [{"player_name": "Taker", "phase": "shootout", "outcome": "scored", "attempted_on": "2026-06-01"}]
    old = [{"player_name": "Taker", "phase": "regular", "outcome": "scored", "attempted_on": "2016-06-01"}]
    strong = build_player_profile("Taker", "FW", recent, date(2026, 6, 28))
    weak = build_player_profile("Taker", "FW", old, date(2026, 6, 28))
    self.assertGreater(strong.effective_attempts, weak.effective_attempts)

def test_goalkeeper_penalty_history_dominates_general_save_rate_only_with_sample(self):
    keeper = {"player_name": "Keeper", "save_percentage": 80.0}
    one = [{"goalkeeper_name": "Keeper", "outcome": "missed"}]
    ten = [{"goalkeeper_name": "Keeper", "outcome": "missed"} for _ in range(10)]
    sparse = build_goalkeeper_profile(keeper, one, deep_save_rate=0.80)
    sampled = build_goalkeeper_profile(keeper, ten, deep_save_rate=0.80)
    self.assertLess(abs(sparse.penalty_save_rate - GLOBAL_PENALTY_SAVE), 0.10)
    self.assertGreater(sampled.penalty_history_weight, sparse.penalty_history_weight)
```

- [ ] **Step 2: Run and verify RED**

Run `python -m unittest tests.test_penalty_profiles -v`; expect module import failure.

- [ ] **Step 3: Implement profile dataclasses and weighted Beta estimates**

```python
GLOBAL_CONVERSION = 0.76
PRIOR_ATTEMPTS = 12.0
SHOOTOUT_WEIGHT = 1.50
REGULAR_WEIGHT = 1.00
RECENCY_HALF_LIFE_DAYS = 1095.0

@dataclass(frozen=True)
class PenaltyPlayerProfile:
    player_name: str
    position: str | None
    attempts: int
    shootout_attempts: int
    conversion: float
    low: float
    high: float
    effective_attempts: float
    taker_propensity: float
    confidence: str

@dataclass(frozen=True)
class GoalkeeperPenaltyProfile:
    player_name: str
    penalty_save_rate: float
    faced_penalties: int
    penalty_history_weight: float
    source: str
```

For each attempt, multiply phase weight by `0.5 ** (age_days / 1095)`. Add weighted successes/failures to the Beta prior. Approximate the 90% interval using posterior variance and clamp to `[0.05, 0.98]`. Taker propensity is `log1p(effective_attempts) + 0.75 * log1p(shootout_attempts)` plus a weak position prior only when history is absent.

Goalkeeper profiles aggregate rows whose normalized `goalkeeper_name` matches the player. Shrink penalty-specific saves toward the global penalty-save prior; blend deep/general save evidence with a maximum 15% influence when penalty history is sparse.

- [ ] **Step 4: Run tests and commit Task 2**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_penalty_profiles -v
git add src/wcpredict/penalty_profiles.py tests/test_penalty_profiles.py
git commit -m "feat(penalties): build Bayesian player and keeper profiles"
```

### Task 3: Logical substitution-path simulator

**Files:**
- Create: `src/wcpredict/penalty_substitution_model.py`
- Create: `tests/test_penalty_substitution_model.py`

**Interfaces:**
- Produces `SubstitutionConfig`, `MatchWindowState`, `SubstitutionEvent`, `EndOfExtraTimeState`.
- Produces `build_on_field_profiles(...)` and `simulate_substitution_path(...)`.

- [ ] **Step 1: Write failing role, limit, and path tests**

```python
def test_neutral_changes_are_role_preserving_and_substituted_players_cannot_return(self):
    state = simulate_substitution_path(squad, confirmed_lineup, neutral_states, Random(7), config)
    self.assertEqual(11, len(state.players))
    self.assertEqual(1, sum(player.role == "GK" for player in state.players))
    self.assertTrue(all(event.role_distance <= 1 for event in state.events))
    self.assertTrue(set(event.out_player for event in state.events).isdisjoint(state.player_names))

def test_trailing_state_increases_attacking_changes(self):
    trailing = repeated_paths(score_delta=-1, seed=20)
    leading = repeated_paths(score_delta=1, seed=20)
    self.assertGreater(trailing.attacking_changes, leading.attacking_changes)

def test_substitution_limits_and_extra_time_change_are_enforced(self):
    state = simulate_substitution_path(squad, lineup, all_windows, Random(11), config)
    self.assertLessEqual(state.regulation_substitutions, 5)
    self.assertLessEqual(state.extra_time_substitutions, 1)
```

- [ ] **Step 2: Run and verify RED**

Run `python -m unittest tests.test_penalty_substitution_model -v`; expect module import failure.

- [ ] **Step 3: Implement roles, on-field weights, and scenario types**

```python
@dataclass(frozen=True)
class SubstitutionConfig:
    regulation_limit: int = 5
    extra_time_additional: int = 1
    windows: tuple[tuple[int, int], ...] = ((55, 65), (65, 75), (75, 90), (90, 105), (105, 120))

@dataclass(frozen=True)
class MatchWindowState:
    minute: int
    score_delta: int

@dataclass(frozen=True)
class EndOfExtraTimeState:
    players: tuple[ScenarioPlayer, ...]
    events: tuple[SubstitutionEvent, ...]
    regulation_substitutions: int
    extra_time_substitutions: int
```

Normalize detailed/coarse positions to `GK/CB/FB/DM/CM/AM/W/ST`. Build start, appearance, survival, and bench-entry weights from starts, games, minutes, lineup status, and availability. Sample one goalkeeper and a plausible ten-player outfield starting state.

- [ ] **Step 4: Implement substitution decisions**

At every configured window, compute outgoing weights from fatigue, expected minutes, card exposure, and forced-sub risk. Compute incoming weights from bench status, freshness, role distance, and match state. Use a role-distance penalty so neutral changes are normally like-for-like; trailing states boost attacking adjacent roles and leading states boost defensive adjacent roles. Remove outgoing players permanently and never exceed configured limits.

- [ ] **Step 5: Run deterministic convergence tests and commit Task 3**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_penalty_substitution_model -v
git add src/wcpredict/penalty_substitution_model.py tests/test_penalty_substitution_model.py
git commit -m "feat(penalties): simulate logical substitution paths"
```

### Task 4: Shootout simulator with first five and sudden death

**Files:**
- Create: `src/wcpredict/penalty_shootout_simulator.py`
- Create: `tests/test_penalty_shootout_simulator.py`

**Interfaces:**
- Consumes end-of-extra-time states and penalty profiles.
- Produces `ShootoutResult` and `simulate_shootout(...)`.

- [ ] **Step 1: Write failing shootout-rule tests**

```python
def test_shootout_stops_when_remaining_kicks_cannot_change_winner(self):
    result = simulate_scripted_shootout([1, 1, 1], [0, 0, 0])
    self.assertEqual("A", result.winner)
    self.assertLess(result.total_kicks, 10)

def test_sudden_death_uses_each_eligible_player_before_repeating(self):
    result = simulate_scripted_shootout([1] * 7, [1] * 6 + [0])
    self.assertEqual(len(result.team_a_unique_takers), result.team_a_kicks)

def test_same_seed_produces_identical_taker_order_and_result(self):
    self.assertEqual(run_once(123), run_once(123))
```

- [ ] **Step 2: Run and verify RED**

Run the new module tests; expect import failure.

- [ ] **Step 3: Implement weighted taker order and kick probabilities**

Select takers without replacement from the eleven using `taker_propensity`; reserve remaining players for sudden death. Compute kick conversion by centering the taker's posterior around the global rate and subtracting the opposing keeper's penalty-save delta, clamped to `[0.35, 0.95]`.

- [ ] **Step 4: Implement early termination and sudden death**

After each first-five kick, compare goals and remaining kicks; stop when the trailing team cannot catch up. If tied after five each, execute paired sudden-death kicks until one pair differs. Do not repeat a taker until all eligible players, including the goalkeeper, have taken.

- [ ] **Step 5: Verify and commit Task 4**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_penalty_shootout_simulator -v
git add src/wcpredict/penalty_shootout_simulator.py tests/test_penalty_shootout_simulator.py
git commit -m "feat(penalties): simulate full penalty shootouts"
```

### Task 5: Orchestrate 25,000 pre-match paths behind the existing API

**Files:**
- Rewrite: `src/wcpredict/penalty_history_model.py`
- Modify: `tests/test_penalty_history_model.py`

**Interfaces:**
- Preserves `build_penalty_match_context(team_a, team_b, attempts, ...) -> PenaltyMatchContext`.
- Extends `PenaltyMatchContext` with player contribution, coverage, and convergence fields.

- [ ] **Step 1: Add failing orchestration tests**

```python
def test_full_context_is_deterministic_and_probabilities_sum_to_one(self):
    first = build_penalty_match_context("A", "B", attempts, squads=squads, seed=77, simulations=500)
    second = build_penalty_match_context("A", "B", attempts, squads=squads, seed=77, simulations=500)
    self.assertEqual(first, second)
    self.assertAlmostEqual(1.0, first.team_a_shootout_win_probability + first.team_b_shootout_win_probability)

def test_empty_taker_history_still_uses_goalkeeper_signal(self):
    context = build_penalty_match_context(
        "A", "B", [], squads=squads, goalkeeper_profiles={"A": weak, "B": strong}, simulations=500
    )
    self.assertLess(context.team_a_shootout_win_probability, 0.5)
```

- [ ] **Step 2: Run tests and verify RED**

Expected: old context lacks extended inputs and the empty-history goalkeeper test fails.

- [ ] **Step 3: Implement the expanded context and Monte Carlo loop**

```python
@dataclass(frozen=True)
class PenaltyMatchContext:
    team_a: PenaltyTeamProfile
    team_b: PenaltyTeamProfile
    team_a_shootout_win_probability: float
    team_b_shootout_win_probability: float
    player_rows: tuple[PenaltyPlayerContribution, ...]
    coverage: PenaltyCoverage
    simulations: int
    standard_error: float
    explanation: str
```

Use a deterministic `Random(seed)`. For every scenario, sample a score path conditioned on a draw after 120, run both substitution paths, build taker orders, simulate the shootout, and accumulate wins/on-field/first-five/any-kick counts. Default to 25,000 simulations; tests pass smaller counts.

- [ ] **Step 4: Preserve legacy callers and verify normalization**

When squads are omitted, construct prior-only synthetic team profiles and preserve a fast compatibility path for unit callers. Remove the old behavior that always passes a neutral 0.5 override when history is empty.

- [ ] **Step 5: Verify and commit Task 5**

Run `tests.test_penalty_history_model`, `tests.test_knockout_bracket`, and convergence tests, then commit:

```powershell
git add src/wcpredict/penalty_history_model.py tests/test_penalty_history_model.py
git commit -m "feat(penalties): orchestrate pre-match path simulation"
```

### Task 6: Cache and integrate the full context into knockout predictions

**Files:**
- Create: `src/wcpredict/penalty_context_cache.py`
- Create: `scripts/precompute_penalty_contexts.py`
- Create: `tests/test_penalty_context_cache.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_streamlit_smoke.py`

**Interfaces:**
- Produces `save_precomputed_context(...)`, `load_precomputed_context(...)`, `group_stage_complete(...)`, and `build_repository_penalty_context(...)`.
- Produces `_penalty_match_context_cached(match_id, db_sig, model_version) -> PenaltyMatchContext`.
- Feeds its conditional probability to `predict_knockout_match`.

- [ ] **Step 1: Add failing cache and UI contracts**

```python
def test_knockout_penalty_context_is_cached_separately(self):
    source = pages_source()
    self.assertIn("def _penalty_match_context_cached", source)
    self.assertIn("PENALTY_MODEL_VERSION", source)
    self.assertNotIn("_penalty_attempts_for_match(repo", source)

def test_knockout_panel_explains_minute_120_player_pool(self):
    source = pages_source()
    self.assertIn("Probables al minuto 120", source)
    self.assertIn("Prob. entre los 5 primeros", source)
    self.assertIn("Cobertura penalty_history", source)
```

- [ ] **Step 2: Run and verify RED**

Run the two app contracts; expect missing cache/UI labels.

- [ ] **Step 3: Add failing persistence and completeness-gate tests**

Verify that a context round-trips through JSON, a version/team mismatch returns `None`, writes are atomic, and `group_stage_complete()` is false until three group results exist.

- [ ] **Step 4: Build the repository context and external precompute script**

Load both full squad banks using canonical-name matching, imported lineups, active squad events, penalty attempts, and deep goalkeeper baselines before kickoff. Use a seed derived from `sha256(f"{match_id}:{model_version}")`. The script refuses incomplete teams by default, accepts an explicit match selector, writes via a temporary sibling file plus `Path.replace()`, and records an input fingerprint.

- [ ] **Step 5: Make Streamlit read-only for the expensive model**

`_penalty_match_context_cached` loads the versioned artifact. On a cache miss it builds only the fast team-level compatibility context and adds a pending explanation; it must not pass squads to `build_penalty_match_context`.

- [ ] **Step 6: Wire conditional shootout probability into advancement**

Call `predict_knockout_match(..., home_penalty_win_probability=context.team_a_shootout_win_probability)`. Ensure the same cached context is reused by the knockout summary and details panel.

- [ ] **Step 7: Render player and coverage explanations**

Under `Si se resuelve en penaltis`, render a compact table with player, team, role, probability on field at 120, probability among first five, posterior conversion, attempts, and confidence. Add likely goalkeeper/source and coverage counts. Collapse detailed scenario assumptions in an expander.

- [ ] **Step 8: Verify and commit Task 6**

Run app contracts, Streamlit smoke, knockout, and penalty tests, then:

```powershell
git add src/wcpredict/penalty_context_cache.py scripts/precompute_penalty_contexts.py tests/test_penalty_context_cache.py src/wcpredict/ui/pages.py tests/test_app_contract.py tests/test_streamlit_smoke.py docs/superpowers/specs/2026-06-28-pre-match-penalty-shootout-design.md docs/superpowers/plans/2026-06-28-pre-match-penalty-shootout.md
git commit -m "feat(ui): explain simulated penalty shootout paths"
```

### Task 7: Populate and audit the 32-team history cache

**Files:**
- Generated but not committed: `output/penalty_identity_review.csv`
- Generated but not committed: `data/cache/transfermarkt_penalties/`
- Modified local DB, not committed without explicit review: `data/worldcup.sqlite`

**Interfaces:**
- Uses the completed snapshot-aware fetch script.

- [ ] **Step 1: Run a dry coverage pass**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' scripts/fetch_transfermarkt_penalties.py --dry-run
```

Record team count, squad targets, reused IDs, missing IDs, and current attempt coverage.

- [ ] **Step 2: Resolve missing identities using cached, rate-limited searches**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' scripts/fetch_transfermarkt_penalties.py --resolve-ids --dry-run
```

Review `output/penalty_identity_review.csv`. Do not lower `--auto-confidence` below `0.95`; ambiguous rows remain unresolved.

- [ ] **Step 3: Fetch histories for accepted IDs**

Run without `--dry-run` only after identity output is reviewed. Existing HTML cache and penalty rows make the run resumable and idempotent. Report exact team/player/attempt coverage and leave unresolved players on priors.

- [ ] **Step 4: Do not commit the live DB automatically**

Present the DB diff/coverage to the user separately. Commit `data/worldcup.sqlite` only with explicit approval because the user is simultaneously closing match results.

### Task 8: Full verification and performance convergence

**Files:**
- Modify only files from Tasks 1-6 if verification finds a regression.

- [ ] **Step 1: Compile**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m compileall -q src tests
```

- [ ] **Step 2: Run all penalty and knockout tests**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_transfermarkt_penalties tests.test_penalty_profiles tests.test_penalty_substitution_model tests.test_penalty_shootout_simulator tests.test_penalty_history_model tests.test_knockout_bracket tests.test_app_contract tests.test_streamlit_smoke -v
```

- [ ] **Step 3: Measure deterministic convergence**

Run identical representative matches at 5k, 10k, 25k, and 50k simulations. Require the 25k and 50k team-win estimates to differ by no more than 0.5 percentage points and record cold runtime. Keep 25k as default only if the cached cold calculation remains acceptable.

- [ ] **Step 4: Run the full suite**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest discover -s tests -v
```

- [ ] **Step 5: Audit repository state**

```powershell
git diff --check
git status --short
git log --oneline --decorate -10
```

Verify that source/tests/fixture changes are committed, generated caches/reviews remain untracked or ignored, and the user's SQLite changes are not accidentally staged.
