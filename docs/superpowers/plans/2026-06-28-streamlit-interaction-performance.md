# Streamlit Interaction Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make view switching, odds editing, and per-match player calculations rerun only their immediate UI workspace while preserving every current numerical result.

**Architecture:** Keep the existing cached match-analysis bundle as the prediction source of truth. Extract pure odds/player preparation into `interaction_models.py`, add narrow Streamlit caches for persisted UI inputs, and move the analysis selector plus selected section into one `st.fragment` so widget interactions never rerun the page shell. Replace global cache clearing with mutation-specific invalidation.

**Tech Stack:** Python 3.12, Streamlit fragments/cache APIs, pandas, SQLite repository, `unittest`, Streamlit `AppTest` smoke coverage.

## Global Constraints

- Preserve all current model outputs bit-for-bit for identical inputs.
- Preserve immediate calculation when a user changes an odds or player input; do not add a mandatory Calculate button.
- Preserve the existing Streamlit information architecture and visible sections.
- Preserve local and Streamlit Cloud operation.
- Do not introduce background workers, new services, or new runtime dependencies.
- Do not include or overwrite the current uncommitted `data/worldcup.sqlite` change or unrelated untracked files.
- Use test-first implementation for every behavioral boundary or refactor.
- Do not modify `penalty_history_model.py` in this plan.

---

## File Structure

- Create `src/wcpredict/ui/interaction_models.py`: pure, Streamlit-free preparation and evaluation for odds and player rosters.
- Create `tests/test_ui_interaction_models.py`: behavioral parity and isolation tests for the pure interaction paths.
- Modify `src/wcpredict/ui/pages.py`: cached persisted contexts, scoped invalidation, and one fragment-backed analysis workspace.
- Modify `tests/test_app_contract.py`: structural regression tests for fragment scope and removal of global cache clearing.
- Modify `tests/test_streamlit_smoke.py`: confirm the prediction page and all analysis sections still render.

### Task 1: Extract the odds interaction path into a pure module

**Files:**
- Create: `src/wcpredict/ui/interaction_models.py`
- Create: `tests/test_ui_interaction_models.py`
- Modify: `src/wcpredict/ui/pages.py:773-774, 2711-2759`

**Interfaces:**
- Consumes: `list[MarketPrediction]` and edited rows from `st.data_editor`.
- Produces: `localized_default_odds_rows(team_a, team_b) -> list[dict]` and `evaluate_odds_rows(predictions, edited_rows) -> OddsEvaluation`.

- [ ] **Step 1: Write failing parity tests for localized rows and EV**

```python
import unittest

from wcpredict.models import MarketFamily
from wcpredict.quality import Confidence
from wcpredict.services import MarketPrediction
from wcpredict.ui.interaction_models import evaluate_odds_rows, localized_default_odds_rows


class OddsInteractionTests(unittest.TestCase):
    def setUp(self):
        self.predictions = [
            MarketPrediction(MarketFamily.MATCH_RESULT, "1X2", "Spain", None, 0.50, Confidence.HIGH, "test"),
            MarketPrediction(MarketFamily.MATCH_RESULT, "1X2", "Draw", None, 0.25, Confidence.HIGH, "test"),
            MarketPrediction(MarketFamily.DRAW_NO_BET, "Draw No Bet", "Spain", None, 2 / 3, Confidence.HIGH, "test"),
        ]

    def test_default_rows_are_localized_without_changing_canonical_source(self):
        rows = localized_default_odds_rows("Spain", "Japan")
        self.assertEqual("Resultado del partido", rows[0]["market_family"])
        self.assertEqual("1X2", rows[0]["market_name"])
        self.assertEqual("Spain", rows[0]["selection_name"])

    def test_entered_odds_preserve_current_ev_and_draw_no_bet_push_math(self):
        edited = [
            {"market_family": "Resultado", "market_name": "1X2", "selection_name": "Spain", "line": None, "decimal_odds": 2.20, "bookmaker": "Winamax"},
            {"market_family": "Resultado", "market_name": "Empate no válido", "selection_name": "Spain", "line": None, "decimal_odds": 1.80, "bookmaker": "Winamax"},
        ]
        result = evaluate_odds_rows(self.predictions, edited)
        self.assertEqual(2, len(result.entered))
        self.assertEqual(2, len(result.comparisons))
        self.assertAlmostEqual(0.10, result.comparisons[0].expected_value)
        self.assertAlmostEqual(0.25, result.comparisons[1].probability)
        self.assertAlmostEqual(0.15, result.comparisons[1].expected_value)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_ui_interaction_models.OddsInteractionTests -v
```

Expected: import failure because `wcpredict.ui.interaction_models` does not exist.

- [ ] **Step 3: Implement the pure odds helpers**

```python
from dataclasses import dataclass

from wcpredict.market_catalog import default_market_rows, normalize_market_rows
from wcpredict.odds import OddsComparison, compare_odds_to_probability
from wcpredict.services import MarketPrediction
from wcpredict.ui.translations import (
    canonical_market,
    canonical_market_family,
    canonical_selection,
    localize_market,
    localize_market_family,
    localize_selection,
)


@dataclass(frozen=True)
class OddsEvaluation:
    entered: tuple[dict, ...]
    comparisons: tuple[OddsComparison, ...]


def localized_default_odds_rows(team_a: str, team_b: str) -> list[dict]:
    rows = [dict(row) for row in default_market_rows(team_a, team_b)]
    for row in rows:
        row["market_family"] = localize_market_family(row["market_family"])
        row["market_name"] = localize_market(row["market_name"])
        row["selection_name"] = localize_selection(row["selection_name"])
    return rows


def evaluate_odds_rows(
    predictions: list[MarketPrediction], edited_rows: list[dict]
) -> OddsEvaluation:
    canonical_rows = []
    for source in edited_rows:
        row = dict(source)
        row["market_family"] = canonical_market_family(str(row.get("market_family") or ""))
        row["market_name"] = canonical_market(str(row.get("market_name") or ""))
        row["selection_name"] = canonical_selection(str(row.get("selection_name") or ""))
        canonical_rows.append(row)
    entered = normalize_market_rows(canonical_rows)
    index = {(row.market_name, row.selection_name): row for row in predictions}
    comparisons = []
    for row in entered:
        model = index.get((row["market_name"], row["selection_name"]))
        if model is None:
            continue
        push_probability = 0.0
        model_probability = model.probability
        if model.market_name == "Draw No Bet":
            draw_model = index.get(("1X2", "Draw"))
            push_probability = draw_model.probability if draw_model else 0.0
            model_probability *= max(0.0, 1.0 - push_probability)
        comparisons.append(compare_odds_to_probability(
            model_probability,
            row["decimal_odds"],
            model.market_family,
            model.market_name,
            model.selection_name,
            model.confidence.value,
            push_probability=push_probability,
        ))
    return OddsEvaluation(tuple(entered), tuple(comparisons))
```

Update the odds section in `pages.py` to call `localized_default_odds_rows` and `evaluate_odds_rows`; use `evaluation.entered` for persistence and `evaluation.comparisons` for `ev_rows`.

- [ ] **Step 4: Run focused and existing odds tests**

Run:

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_ui_interaction_models.OddsInteractionTests tests.test_odds tests.test_market_catalog -v
```

Expected: PASS with identical EV values.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/wcpredict/ui/interaction_models.py src/wcpredict/ui/pages.py tests/test_ui_interaction_models.py
git commit -m "refactor(ui): isolate odds interaction calculations"
```

### Task 2: Prepare and cache per-match player UI context

**Files:**
- Modify: `src/wcpredict/ui/interaction_models.py`
- Modify: `src/wcpredict/ui/pages.py:2766-3030`
- Modify: `tests/test_ui_interaction_models.py`

**Interfaces:**
- Consumes: selected match teams, `bundle.current_players`, imported lineups, `MatchAuxiliaryBundle` volume predictions and goalkeeper baselines.
- Produces: `prepare_player_match_context(...) -> PlayerMatchContext`, indexed by canonical team with prebuilt roster rows.

- [ ] **Step 1: Add failing player-context tests**

```python
from wcpredict.ui.interaction_models import prepare_player_match_context


class PlayerInteractionTests(unittest.TestCase):
    def test_context_filters_zero_minutes_and_prebuilds_team_rosters(self):
        players = [
            {"team_name": "Spain", "player_name": "Forward", "position": "FW", "minutes": 180, "games": 2, "starts": 2, "goals": 1, "assists": 0, "shots": 5, "shots_on_target": 2},
            {"team_name": "Spain", "player_name": "Keeper", "position": "GK", "minutes": 180, "games": 2, "starts": 2, "save_percentage": 75.0, "saves": 6, "goals_conceded": 2},
            {"team_name": "Japan", "player_name": "Unused", "position": "FW", "minutes": 0},
        ]
        context = prepare_player_match_context(
            "Spain", "Japan", players, [],
            {"shots_on_target": {"Spain": 5.0, "Japan": 3.0}},
            {"Spain": object(), "Japan": object()},
        )
        spain = context.by_team["Spain"]
        self.assertEqual(["Forward", "Keeper"], [row["player_name"] for row in spain.players])
        self.assertEqual(["Forward"], [row["player_name"] for row in spain.field_players])
        self.assertEqual(["Keeper"], [row["player_name"] for row in spain.goalkeepers])
        self.assertEqual(3.0, spain.opponent_sot_per90)
        self.assertEqual(2, len(spain.roster_rows))
        self.assertEqual(0, len(context.by_team["Japan"].players))

    def test_player_context_cache_builds_once_for_repeated_warm_interactions(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from wcpredict.ui import pages

        repo = Mock()
        repo.list_imported_lineups.return_value = []
        auxiliary = SimpleNamespace(team_volume_predictions={}, goalkeeper_baselines={})
        pages._player_match_context_cached.clear()
        try:
            with patch.object(pages, "_repo", return_value=repo):
                first = pages._player_match_context_cached(
                    77, (100, 200), "test-engine", "Spain", "Japan", [], auxiliary
                )
                second = pages._player_match_context_cached(
                    77, (100, 200), "test-engine", "Spain", "Japan", [], auxiliary
                )
            self.assertIs(first, second)
            self.assertEqual(1, repo.list_imported_lineups.call_count)
        finally:
            pages._player_match_context_cached.clear()
```

- [ ] **Step 2: Run the player test and verify RED**

Run the `PlayerInteractionTests` class. Expected: import failure for `prepare_player_match_context`.

- [ ] **Step 3: Implement immutable prepared player contexts**

Add these definitions to `interaction_models.py`:

```python
from typing import Any

from wcpredict.names import same_team
from wcpredict.player_markets import is_goalkeeper


@dataclass(frozen=True)
class PlayerTeamContext:
    players: tuple[dict, ...]
    field_players: tuple[dict, ...]
    goalkeepers: tuple[dict, ...]
    roster_rows: tuple[dict, ...]
    opponent_sot_per90: float | None
    goalkeeper_baseline: Any


@dataclass(frozen=True)
class PlayerMatchContext:
    lineups: tuple[dict, ...]
    by_team: dict[str, PlayerTeamContext]


def _per90(value, minutes: int) -> float | None:
    return round(90.0 * float(value or 0) / minutes, 2) if minutes else None


def _roster_row(row: dict) -> dict:
    minutes = int(row.get("minutes") or 0)
    games = max(1, int(row.get("games") or 0))
    starts = int(row.get("starts") or 0)
    result = {
        "Jugador": row.get("player_name"),
        "Posición": row.get("position") or "—",
        "Min": minutes,
        "Partidos": games,
        "Titularidad": f"{min(1.0, starts / games):.0%}",
    }
    if is_goalkeeper(row):
        save_pct = row.get("save_percentage")
        result.update({
            "Save %": round(float(save_pct), 1) if save_pct is not None else None,
            "Paradas": int(row.get("saves") or 0),
            "GC": int(row.get("goals_conceded") or 0),
            "Intercepciones": row.get("interceptions") or 0,
            "Pases": int(row.get("passes") or 0),
            "Amarillas": int(row.get("yellow_cards") or 0),
            "Rojas": int(row.get("red_cards") or 0),
        })
    else:
        result.update({
            "Goles": int(row.get("goals") or 0),
            "Asist.": int(row.get("assists") or 0),
            "Tiros": int(row.get("shots") or 0),
            "SOT": int(row.get("shots_on_target") or 0),
            "Amarillas": int(row.get("yellow_cards") or 0),
            "Rojas": int(row.get("red_cards") or 0),
            "Pases": int(row.get("passes") or 0),
            "G/90": _per90(row.get("goals"), minutes),
            "A/90": _per90(row.get("assists"), minutes),
            "Tiros/90": _per90(row.get("shots"), minutes),
            "SOT/90": _per90(row.get("shots_on_target"), minutes),
        })
    return result


def prepare_player_match_context(
    team_a: str,
    team_b: str,
    current_players: list[dict],
    lineups: list[dict],
    team_volume_predictions: dict,
    goalkeeper_baselines: dict,
) -> PlayerMatchContext:
    sot = team_volume_predictions.get("shots_on_target", {})
    opponents = {team_a: team_b, team_b: team_a}
    by_team = {}
    for team_name in (team_a, team_b):
        players = tuple(sorted(
            (
                row for row in current_players
                if same_team(str(row.get("team_name") or ""), team_name)
                and int(row.get("minutes") or 0) > 0
            ),
            key=lambda row: (-int(row.get("minutes") or 0), str(row.get("player_name") or "")),
        ))
        field_players = tuple(row for row in players if not is_goalkeeper(row))
        goalkeepers = tuple(row for row in players if is_goalkeeper(row))
        by_team[team_name] = PlayerTeamContext(
            players=players,
            field_players=field_players,
            goalkeepers=goalkeepers,
            roster_rows=tuple(_roster_row(row) for row in players),
            opponent_sot_per90=sot.get(opponents[team_name]),
            goalkeeper_baseline=goalkeeper_baselines.get(team_name),
        )
    return PlayerMatchContext(tuple(lineups), by_team)
```

Add this cache in `pages.py`:

```python
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
```

Replace repeated lineup querying, sorting, goalkeeper/field filtering, opponent-SOT construction, and roster-row construction in the player section with this context. Keep `derive_player_assumption`, `estimate_player_market_probability`, and `compare_odds_to_probability` calls unchanged.

- [ ] **Step 4: Run player-market and UI interaction tests**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_ui_interaction_models.PlayerInteractionTests tests.test_player_markets tests.test_player_markets_extended -v
```

Expected: PASS; roster order and calculator outputs remain unchanged.

- [ ] **Step 5: Commit Task 2**

```powershell
git add src/wcpredict/ui/interaction_models.py src/wcpredict/ui/pages.py tests/test_ui_interaction_models.py
git commit -m "perf(ui): cache prepared player match context"
```

### Task 3: Scope prediction interactions to one Streamlit fragment

**Files:**
- Modify: `src/wcpredict/ui/pages.py:2381-3124`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_streamlit_smoke.py`

**Interfaces:**
- Consumes: selected `match`, `MatchAnalysisBundle`, collector `cached` bundle, and `Repository`.
- Produces: `_render_prediction_workspace(match, bundle, cached, repo) -> None`, decorated with `@st.fragment`.

- [ ] **Step 1: Add failing structural tests**

```python
def test_prediction_workspace_is_fragment_scoped(self):
    source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
    self.assertIn("@st.fragment\ndef _render_prediction_workspace(", source)
    outer_start = source.index("def render_prediction_lab")
    workspace_start = source.index("def _render_prediction_workspace")
    outer = source[outer_start:source.index("def render_dashboard")]
    self.assertIn("_render_prediction_workspace(match, bundle, cached, repo)", outer)
    self.assertNotIn('"Vista de análisis"', outer)

def test_odds_and_player_controls_live_inside_workspace_fragment(self):
    source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
    workspace = source[source.index("def _render_prediction_workspace"):source.index("def render_prediction_lab")]
    self.assertIn('"Vista de análisis"', workspace)
    self.assertIn('elif section == "Mercados y EV":', workspace)
    self.assertIn('elif section == "Jugadores":', workspace)
```

- [ ] **Step 2: Run contract tests and verify RED**

Run `tests.test_app_contract.AppContractTests` and confirm failure because the workspace function is absent.

- [ ] **Step 3: Extract the workspace without changing its branch bodies**

Create the function before `render_prediction_lab`:

```python
@st.fragment
def _render_prediction_workspace(match, bundle: MatchAnalysisBundle, cached, repo: Repository) -> None:
    team_a, team_b = match.team_a.name, match.team_b.name
    predictions = bundle.predictions
    score_only_predictions = bundle.score_only_predictions
    primary = bundle.primary
    ml_probabilities = bundle.ml_probabilities
    ml_features = bundle.ml_features
    ml_model_meta = bundle.ml_model_meta
    current_players = bundle.current_players
    section = st.segmented_control(
        "Vista de análisis",
        ["Modelo", "Marcadores", "Mercados y EV", "Jugadores", "Datos / SofaScore", "Guardado"],
        default="Modelo",
        label_visibility="collapsed",
    )
    # Paste the existing branch block beginning with `deep_ml_probabilities =`
    # and ending after the Guardado empty-state branch without changing any
    # branch expression, widget key, repository write, or rendered copy.
```

Replace the original selector and branch block in `render_prediction_lab` with:

```python
_render_prediction_workspace(match, bundle, cached, repo)
```

Do not move the daily refresh, match selector, hero, collector refresh, main bundle acquisition, or top-level probability summary into the fragment.

- [ ] **Step 4: Extend the smoke test to visit every prediction section**

After navigating to `🎯 Predicción y valor`, locate the segmented control and set each of `Modelo`, `Marcadores`, `Mercados y EV`, `Jugadores`, `Datos / SofaScore`, and `Guardado`; run the app and assert `app.exception` remains empty after every selection.

- [ ] **Step 5: Run contract and smoke tests**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_app_contract tests.test_streamlit_smoke -v
```

Expected: all UI contracts and navigation paths pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add src/wcpredict/ui/pages.py tests/test_app_contract.py tests/test_streamlit_smoke.py
git commit -m "perf(ui): scope prediction controls to fragment"
```

### Task 4: Replace global cache clearing with narrow invalidation

**Files:**
- Modify: `src/wcpredict/ui/pages.py:2471-2475, 2708-2710, 2760-2764, 3044-3051, 3719-3739`
- Modify: `tests/test_app_contract.py`

**Interfaces:**
- Produces: `_invalidate_match_analysis_caches()`, `_invalidate_odds_caches()`, and `_invalidate_player_caches()`.
- Consumes: Streamlit cached functions already defined in `pages.py`.

- [ ] **Step 1: Add failing cache-invalidation contracts**

```python
def test_ui_mutations_never_clear_all_streamlit_caches(self):
    source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
    self.assertNotIn("st.cache_data.clear()", source)
    self.assertNotIn("st.cache_resource.clear()", source)
    self.assertIn("def _invalidate_match_analysis_caches", source)
    self.assertIn("def _invalidate_odds_caches", source)
    self.assertIn("def _invalidate_player_caches", source)
```

- [ ] **Step 2: Run the contract and verify RED**

Expected: failure on the three existing global-clear call sites.

- [ ] **Step 3: Implement explicit invalidators**

```python
def _invalidate_match_analysis_caches() -> None:
    _match_analysis_bundle_cached.clear()
    _match_volume_context_cached.clear()
    _match_auxiliary_context_cached.clear()
    _player_match_context_cached.clear()


def _invalidate_odds_caches() -> None:
    _manual_odds_cached.clear()


def _invalidate_player_caches() -> None:
    _match_analysis_bundle_cached.clear()
    _match_auxiliary_context_cached.clear()
    _player_match_context_cached.clear()
    _player_intelligence_rows_cached.clear()
```

Add:

```python
@st.cache_resource(show_spinner=False)
def _manual_odds_cached(match_id: int, db_sig: tuple[int, int]):
    return _repo().list_manual_odds(match_id)
```

Use `_manual_odds_cached(match.id, _db_signature())` for read-only odds displays. Call `_invalidate_odds_caches()` after CSV/manual odds writes, `_invalidate_match_analysis_caches()` after collector/deep evidence imports, and `_invalidate_player_caches()` after the player-bank refresh. Remove every global cache clear.

- [ ] **Step 4: Run cache, odds, refresh, and contract tests**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_app_contract tests.test_daily_refresh tests.test_odds tests.test_ui_interaction_models -v
```

Expected: PASS and no global cache clearing in `pages.py`.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/wcpredict/ui/pages.py tests/test_app_contract.py
git commit -m "perf(ui): invalidate only affected caches"
```

### Task 5: Verify output parity and complete regression suite

**Files:**
- Modify only if a verification failure identifies a regression in files already changed by Tasks 1-4.

**Interfaces:**
- Verifies all interfaces from Tasks 1-4 and the unchanged prediction/penalty modules.

- [ ] **Step 1: Run syntax compilation**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m compileall -q src tests
```

Expected: exit code 0.

- [ ] **Step 2: Run focused mathematical parity tests**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest tests.test_ui_interaction_models tests.test_odds tests.test_player_markets tests.test_player_markets_extended tests.test_knockout_bracket tests.test_penalty_history_model -v
```

Expected: PASS; knockout and penalty-history outputs are unchanged.

- [ ] **Step 3: Run the complete test suite**

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe' -m unittest discover -s tests -v
```

Expected: every test passes with no unhandled exceptions.

- [ ] **Step 4: Inspect the final diff and working tree**

```powershell
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors; `data/worldcup.sqlite` and unrelated untracked artifacts remain untouched and uncommitted.

- [ ] **Step 5: Record verification without creating an empty commit**

If all code changes were already committed in Tasks 1-4, report the exact test count and duration. If verification required a corrective edit, rerun the failing focused test first, then the full suite, and commit only that correction with:

```powershell
git add src/wcpredict/ui/interaction_models.py src/wcpredict/ui/pages.py tests/test_ui_interaction_models.py tests/test_app_contract.py tests/test_streamlit_smoke.py
git commit -m "test(ui): verify interaction performance regression coverage"
```
