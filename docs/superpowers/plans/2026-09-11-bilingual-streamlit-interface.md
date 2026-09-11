# Bilingual Streamlit Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a fully contextual Spanish/English Streamlit interface that asks for a language only on the first browser visit and preserves all sports data, deep statistics, and audits.

**Architecture:** A language-neutral i18n service will expose semantic message keys, interpolation, plurals, and controlled-value localisation. A first-party Streamlit component will persist the selected language in browser `localStorage`, while `st.session_state` mirrors it only for reruns. Existing UI renderers will receive translations at presentation time; canonical data and model identifiers remain unchanged.

**Tech Stack:** Python 3.12, Streamlit >=1.63, Streamlit Components v2, pandas, unittest, HTML/CSS/JavaScript embedded in the first-party component.

**Spec:** `docs/superpowers/specs/2026-09-11-bilingual-streamlit-interface-design.md`

## Global Constraints

- Supported languages are exactly `es` and `en`.
- The browser preference is stored under the namespaced key `wcpredict.language.v1`.
- First visit must show the language gate; no automatic locale selection is allowed.
- A valid stored preference must bypass the language gate after reloads, new Streamlit sessions, and deployments.
- The persistent sidebar selector must allow language changes from every page.
- Translation is presentation-only: do not rewrite SQLite, JSON evidence, caches, model artefacts, deep stats, or audit records.
- Proper names and named statistical methods remain canonical.
- Provider identifiers must not appear parenthetically in source headings.
- No runtime machine translation or external translation service.
- The hero titles are `Centro de análisis predictivo del Mundial 2026` and `2026 World Cup Predictive Analytics`.
- Reachable UI must contain no betting, odds, market, EV, or bookmaker copy.

---

### Task 1: Language-neutral translation service

**Files:**
- Create: `tests/test_i18n.py`
- Create: `src/wcpredict/ui/i18n.py`
- Modify: `src/wcpredict/ui/translations.py`
- Modify: `src/wcpredict/ui/view_models.py`

**Interfaces:**
- Produces: `Language = Literal["es", "en"]`
- Produces: `normalise_language(value: object) -> Language | None`
- Produces: `translate(key: str, *, language: Language, count: int | None = None, **values: object) -> str`
- Produces: `localize_controlled(kind: str, value: object, *, language: Language) -> object`
- Produces: `localize_table_columns(rows: list[dict], *, language: Language) -> list[dict]`
- Consumes: canonical values already produced by repositories and view models.

- [ ] **Step 1: Write failing catalogue and API tests**

```python
class I18nTests(unittest.TestCase):
    def test_catalogues_have_identical_keys_and_placeholders(self):
        self.assertEqual(set(CATALOGUES["es"]), set(CATALOGUES["en"]))
        for key in CATALOGUES["es"]:
            self.assertEqual(placeholders(CATALOGUES["es"][key]), placeholders(CATALOGUES["en"][key]))

    def test_translate_uses_contextual_football_terms(self):
        self.assertEqual(translate("metric.shots_on_target", language="es"), "Tiros a puerta")
        self.assertEqual(translate("metric.shots_on_target", language="en"), "Shots on target")
        self.assertEqual(translate("score.most_likely", language="en"), "Most likely scoreline")

    def test_invalid_language_is_not_accepted(self):
        self.assertIsNone(normalise_language("fr"))
        self.assertIsNone(normalise_language(None))

    def test_plural_forms_are_language_specific(self):
        self.assertEqual(translate("count.matches", language="es", count=1), "1 partido")
        self.assertEqual(translate("count.matches", language="en", count=2), "2 matches")
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n -v`

Expected: import failure for `wcpredict.ui.i18n` because the service does not exist.

- [ ] **Step 3: Implement the minimal i18n core and seed catalogue**

```python
Language = Literal["es", "en"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("es", "en")

def normalise_language(value: object) -> Language | None:
    return value if value in SUPPORTED_LANGUAGES else None

def translate(key: str, *, language: Language, count: int | None = None, **values: object) -> str:
    entry = CATALOGUES[language][key]
    template = entry["one" if count == 1 else "other"] if isinstance(entry, dict) else entry
    return template.format(count=count, **values)
```

Move the existing metric, status, confidence, model, origin, stage, position, and table-column mappings behind `localize_controlled`. Keep canonicalisation helpers independent of display language.

- [ ] **Step 4: Run i18n and view-model tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_view_models -v`

Expected: all tests pass and existing canonical values remain unchanged.

- [ ] **Step 5: Commit the i18n foundation**

```powershell
git add tests/test_i18n.py src/wcpredict/ui/i18n.py src/wcpredict/ui/translations.py src/wcpredict/ui/view_models.py
git commit -m "feat: add contextual bilingual translation service"
```

### Task 2: Persistent browser language preference and first-visit gate

**Files:**
- Create: `tests/test_language_preference.py`
- Create: `src/wcpredict/ui/language_preference.py`
- Modify: `app.py`
- Modify: `requirements.txt`
- Modify: `src/wcpredict/ui/theme.py`

**Interfaces:**
- Consumes: `normalise_language` and `translate` from Task 1.
- Produces: `PreferenceState(status: Literal["resolving", "unselected", "resolved"], language: Language | None)`
- Produces: `resolve_preference(component_value: object, session_value: object) -> PreferenceState`
- Produces: `render_language_preference() -> Language | None`; `None` means the application shell must return before rendering pages.
- Produces: browser component state fields `ready: bool` and `language: "es" | "en" | None`.

- [ ] **Step 1: Write failing state-machine and component-contract tests**

```python
class LanguagePreferenceTests(unittest.TestCase):
    def test_unresolved_component_withholds_the_application(self):
        self.assertEqual(resolve_preference(None, None).status, "resolving")

    def test_missing_storage_requires_first_visit_choice(self):
        state = resolve_preference({"ready": True, "language": None}, None)
        self.assertEqual(state.status, "unselected")

    def test_stored_language_bypasses_gate_in_new_session(self):
        state = resolve_preference({"ready": True, "language": "en"}, None)
        self.assertEqual((state.status, state.language), ("resolved", "en"))

    def test_component_uses_namespaced_local_storage(self):
        self.assertIn('wcpredict.language.v1', COMPONENT_JS)
        self.assertIn('localStorage.getItem', COMPONENT_JS)
        self.assertIn('localStorage.setItem', COMPONENT_JS)
```

- [ ] **Step 2: Run preference tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_language_preference -v`

Expected: import failure because `language_preference.py` does not exist.

- [ ] **Step 3: Implement the state machine and Components v2 bridge**

Register a component whose JavaScript reads `wcpredict.language.v1` on mount, emits `{ready: true, language}`, writes only validated `es`/`en` values, and catches browser-storage exceptions by emitting `language: null`. Pin `streamlit>=1.63,<2` so the deployed component API is deterministic.

In `app.py`, call `render_language_preference()` before `apply_theme`, sidebar navigation, repository construction, or any page renderer. Return immediately while resolving or choosing. After resolution, render a keyed `ES | EN` sidebar control and persist changes through the same component.

- [ ] **Step 4: Add the bilingual gate and selector styles**

Add focused gate-card styles and compact sidebar language-control styles to `theme.py`. All gate copy must come from `translate`; only the language names `Español` and `English` may be literal self-labels.

- [ ] **Step 5: Run preference and Streamlit smoke tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_language_preference tests.test_streamlit_smoke -v`

Expected: state transitions and app import pass without repository or page work before preference resolution.

- [ ] **Step 6: Commit browser persistence**

```powershell
git add tests/test_language_preference.py src/wcpredict/ui/language_preference.py app.py requirements.txt src/wcpredict/ui/theme.py
git commit -m "feat: persist language choice in Streamlit browser"
```

### Task 3: Application shell and dashboard translation

**Files:**
- Modify: `tests/test_i18n.py`
- Modify: `tests/test_app_contract.py`
- Modify: `app.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/bracket.py`
- Modify: `src/wcpredict/ui/theme.py`

**Interfaces:**
- Consumes: active `Language` and `translate` from Tasks 1–2.
- Produces: a translated app shell, dashboard, match window, and knockout bracket while preserving match IDs and links.

- [ ] **Step 1: Write failing shell and dashboard contract tests**

```python
def test_hero_uses_approved_titles_in_both_catalogues(self):
    self.assertEqual(translate("hero.title", language="es"), "Centro de análisis predictivo del Mundial 2026")
    self.assertEqual(translate("hero.title", language="en"), "2026 World Cup Predictive Analytics")

def test_retired_slogan_and_betting_copy_are_absent(self):
    workspace = APP_SOURCE + PAGES_SOURCE + BRACKET_SOURCE
    for text in ("Decidir con probabilidades, no con ruido", "cuotas manuales", "Mercados y EV"):
        self.assertNotIn(text, workspace)

def test_source_heading_does_not_expose_provider_id(self):
    self.assertNotIn("Dataset externo (mominullptr)", PAGES_SOURCE)
```

- [ ] **Step 2: Run focused contracts and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_app_contract -v`

Expected: failures for the old hero slogan, source heading, and missing hero catalogue keys.

- [ ] **Step 3: Translate the app shell and dashboard**

Replace page labels, sidebar copy, hero, counters, match-window messages, refresh statuses, error disclosures, bracket round labels, match metadata, and dashboard empty states with semantic keys. Pass language explicitly into HTML builders such as `render_bracket` rather than reading global state inside pure render helpers.

- [ ] **Step 4: Remove decorative provider IDs**

Render `source.external_dataset` as `Dataset externo` / `External dataset`. Preserve the provider ID in internal refresh lookups and technical data rows.

- [ ] **Step 5: Run dashboard contracts and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_app_contract tests.test_bracket_scores -v`

Expected: all tests pass with unchanged bracket scores and match links.

- [ ] **Step 6: Commit shell and dashboard localisation**

```powershell
git add tests/test_i18n.py tests/test_app_contract.py app.py src/wcpredict/ui/pages.py src/wcpredict/ui/bracket.py src/wcpredict/ui/theme.py
git commit -m "feat: translate app shell and dashboard"
```

### Task 4: Predictive analysis, scorelines, team statistics, and audits

**Files:**
- Modify: `tests/test_i18n.py`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_audit.py`
- Modify: `tests/test_knockout_audit.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/audit.py`
- Modify: `src/wcpredict/knockout_audit.py`

**Interfaces:**
- Consumes: canonical `MatchAnalysisBundle`, audit rows, team-stat rows, score probabilities, and active language.
- Produces: language-specific presentation rows without changing audit DTO field names, values, deltas, or colour categories.

- [ ] **Step 1: Write failing bilingual analysis tests**

```python
def test_analysis_section_labels_are_contextual(self):
    self.assertEqual(translate("analysis.sections.scorelines", language="es"), "Marcadores")
    self.assertEqual(translate("analysis.sections.scorelines", language="en"), "Scorelines")
    self.assertEqual(translate("analysis.sections.team_stats", language="en"), "Team statistics")

def test_audit_localisation_preserves_numeric_payload(self):
    original = {"metric": "shots_on_target", "predicted": 3.85, "actual": 3, "delta": -0.85}
    english = localize_audit_row(original, language="en")
    self.assertEqual(english["Metric"], "Shots on target")
    self.assertEqual((english["Predicted"], english["Actual"], english["Delta"]), (3.85, 3, -0.85))
```

- [ ] **Step 2: Run analysis and audit tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_audit tests.test_knockout_audit tests.test_app_contract -v`

Expected: missing catalogue keys and missing language-aware audit presenter.

- [ ] **Step 3: Translate the predictive workspace**

Translate the match selector, probability summary, model explanation, immediate reading, expected score, alternative scorelines, model diagnostics, six analysis sections, score matrix, team projections, source/history messages, tooltips, and all dataframe columns. Keep `1X2` only where it is a canonical model identifier; use `Match outcome probabilities` / `Probabilidades del resultado` in visible copy.

- [ ] **Step 4: Translate audits at presentation time**

Add language-aware presenter functions that map metric and column labels after audit calculations complete. Translate closed-match summary, phase expanders, observed-stat counts, deep-stats comparison, deviation descriptions, knockout decisions, and evidence captions. Assert that row counts, numeric values, phase keys, and colour tones are identical between languages.

- [ ] **Step 5: Run predictive and audit suites and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_app_contract tests.test_audit tests.test_knockout_audit tests.test_score_mode_consistency -v`

Expected: all tests pass; audit numeric payloads are unchanged.

- [ ] **Step 6: Commit analysis and audit localisation**

```powershell
git add tests/test_i18n.py tests/test_app_contract.py tests/test_audit.py tests/test_knockout_audit.py src/wcpredict/ui/pages.py src/wcpredict/audit.py src/wcpredict/knockout_audit.py
git commit -m "feat: translate predictive analysis and audits"
```

### Task 5: Player intelligence and projections

**Files:**
- Modify: `tests/test_i18n.py`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_player_analytics.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/view_models.py`

**Interfaces:**
- Consumes: canonical player metrics, team names, positions, confidence values, and model projections.
- Produces: bilingual player pages and projection tables without modifying player names or model metrics.

- [ ] **Step 1: Write failing player-language tests**

```python
def test_player_terms_are_contextual(self):
    self.assertEqual(translate("player.starts", language="es"), "Titularidades")
    self.assertEqual(translate("player.starts", language="en"), "Starts")
    self.assertEqual(translate("player.per_90", language="en", metric="Goals"), "Goals per 90")

def test_player_names_are_not_translated(self):
    rows = localize_player_rows([{"player_name": "Diego Gomez", "position": "MF"}], language="en")
    self.assertEqual(rows[0]["Player"], "Diego Gomez")
```

- [ ] **Step 2: Run player tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_player_analytics tests.test_app_contract -v`

Expected: missing player catalogue entries and language parameter support.

- [ ] **Step 3: Translate player interfaces**

Translate ranking tabs, filters, sorting controls, position groups, empty states, chart titles, per-90 labels, projection confidence, lineup status, squad messages, table columns, and explanatory copy. Keep player and team names unchanged. Ensure no odds or betting labels return in either language.

- [ ] **Step 4: Run player and contract tests and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_player_analytics tests.test_player_impact tests.test_app_contract -v`

Expected: all tests pass and player calculations are unchanged.

- [ ] **Step 5: Commit player localisation**

```powershell
git add tests/test_i18n.py tests/test_app_contract.py tests/test_player_analytics.py src/wcpredict/ui/pages.py src/wcpredict/ui/view_models.py
git commit -m "feat: translate player analytics interface"
```

### Task 6: Calibration, quality, capture, and knockout settlement

**Files:**
- Modify: `tests/test_i18n.py`
- Modify: `tests/test_app_contract.py`
- Modify: `tests/test_knockout_settlement_ui.py`
- Modify: `tests/test_postmatch_capture_ui.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/knockout_settlement.py`
- Modify: `src/wcpredict/ui/postmatch_capture.py`

**Interfaces:**
- Consumes: active language and existing canonical settlement/capture/calibration/quality objects.
- Produces: bilingual operational interfaces whose saved records are byte-for-byte equivalent for equivalent inputs.

- [ ] **Step 1: Write failing operational-interface contracts**

```python
def test_knockout_decisions_have_bilingual_labels(self):
    self.assertEqual(translate("knockout.regulation", language="es"), "90 minutos")
    self.assertEqual(translate("knockout.regulation", language="en"), "90 minutes")

def test_capture_actions_are_translated(self):
    self.assertEqual(translate("capture.save_review", language="en"), "Save review decisions")

def test_language_is_not_written_into_capture_payload(self):
    self.assertNotIn("language", build_capture_payload(FIXTURE_FORM))
```

- [ ] **Step 2: Run operational tests and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_knockout_settlement_ui tests.test_postmatch_capture_ui tests.test_app_contract -v`

Expected: missing operational message keys and language-aware renderers.

- [ ] **Step 3: Translate calibration and data-quality pages**

Translate calibration summaries, bins, reliability descriptions, validation warnings, source-health tables, resource/cost tiers, freshness statuses, database component labels, timestamps captions, and error-detail disclosures.

- [ ] **Step 4: Translate capture and settlement workflows**

Pass language into `render_postmatch_capture` and `render_knockout_settlement`. Translate period labels, instructions, validation errors, buttons, save/close confirmations, penalty-shootout copy, OCR review labels, and candidate-value tables. Keep decision codes such as `regulation`, `extra_time`, and `penalties` canonical in submitted payloads.

- [ ] **Step 5: Run operational suites and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_i18n tests.test_knockout_settlement_ui tests.test_knockout_settlement tests.test_postmatch_capture_ui tests.test_app_contract -v`

Expected: all tests pass and persistence payloads do not contain translated decision values.

- [ ] **Step 6: Commit operational localisation**

```powershell
git add tests/test_i18n.py tests/test_app_contract.py tests/test_knockout_settlement_ui.py tests/test_postmatch_capture_ui.py src/wcpredict/ui/pages.py src/wcpredict/ui/knockout_settlement.py src/wcpredict/ui/postmatch_capture.py
git commit -m "feat: translate calibration and audit workflows"
```

### Task 7: Exhaustive visible-copy contract and documentation

**Files:**
- Create: `tests/test_ui_copy_contract.py`
- Modify: `tests/test_i18n.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/data-sources.md`
- Modify: `.streamlit/config.toml`

**Interfaces:**
- Consumes: all UI modules and both catalogues.
- Produces: a repeatable static audit of user-visible literals and bilingual setup documentation.

- [ ] **Step 1: Write the failing exhaustive-copy scanner**

Build an AST test that inspects `app.py` and `src/wcpredict/ui/*.py` calls to Streamlit text APIs plus `callout`, `empty_state`, and Python HTML builders. Fail on untranslated string literals unless they are in a narrow allow-list containing proper names, symbols, CSS classes, and canonical internal codes.

```python
def test_streamlit_copy_uses_i18n_keys(self):
    violations = find_user_visible_literals(UI_FILES, allowed=VISIBLE_LITERAL_ALLOWLIST)
    self.assertEqual(violations, [], "Untranslated visible copy:\n" + "\n".join(violations))

def test_catalogues_have_no_betting_copy(self):
    forbidden = ("odds", "cuota", "bookmaker", "mercado", "market", "expected value", "EV")
    visible = "\n".join(flatten_catalogue_values())
    self.assertFalse([term for term in forbidden if contains_word(visible, term)])
```

- [ ] **Step 2: Run the scanner and confirm RED**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_ui_copy_contract -v`

Expected: a concrete list of remaining visible literals.

- [ ] **Step 3: Resolve every reported visible literal**

Add semantic catalogue entries or justify immutable proper names in the allow-list. Do not allow whole files, broad regular expressions, Spanish sentences, or English sentences. Re-run until the violation list is empty.

- [ ] **Step 4: Update user and architecture documentation**

Document the bilingual first-visit flow, persistent selector, `localStorage` key, privacy boundary, local component requirement, minimum Streamlit version, and testing commands. Remove retired slogan, odds, EV, bookmaker, and market-focused product descriptions from current documentation while preserving clearly marked historical design records.

- [ ] **Step 5: Run copy, catalogue, and documentation contracts and confirm GREEN**

Run: `$env:PYTHONPATH='src'; python -m unittest tests.test_ui_copy_contract tests.test_i18n tests.test_app_contract -v`

Expected: no untranslated reachable UI copy, no incomplete catalogue keys, and no forbidden product copy.

- [ ] **Step 6: Commit coverage and documentation**

```powershell
git add tests/test_ui_copy_contract.py tests/test_i18n.py README.md docs/architecture.md docs/data-sources.md .streamlit/config.toml
git commit -m "test: enforce complete bilingual interface copy"
```

### Task 8: Full regression, browser verification, and deployment

**Files:**
- Modify only if a failing test or observed UI defect requires a focused TDD repair.

**Interfaces:**
- Consumes: the complete bilingual implementation.
- Produces: verified local and Streamlit Cloud behaviour plus a reversible release commit history.

- [ ] **Step 1: Run static integrity checks**

Run: `git diff --check`

Run: `$env:PYTHONPATH='src'; python -m compileall -q app.py src`

Expected: both commands exit successfully with no output.

- [ ] **Step 2: Run the complete automated suite**

Run: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v`

Expected: all tests pass; the existing documented skip, if still applicable, is the only skip.

- [ ] **Step 3: Verify local first-visit and returning-visit flows**

Start Streamlit locally, open a clean browser profile, and verify: the application is withheld while storage resolves; the gate appears once; choosing Spanish persists after reload; switching to English updates the current page; opening a fresh Streamlit session in the same profile returns directly to English.

- [ ] **Step 4: Verify all pages in both languages**

Visit dashboard, predictive analysis and all six sections, player intelligence, calibration, data quality, knockout settlement, post-match capture, and a closed-match audit. Check long English labels, dataframe headers, HTML cards, help text, brackets, empty states, and error disclosures for clipping and untranslated copy.

- [ ] **Step 5: Verify data invariants**

Before and after switching languages, compare counts and checksums for the sports tables, evidence paths, deep-stat rows, predictions, audit rows, and model artefacts. Expected: no changes caused by language selection or page rendering.

- [ ] **Step 6: Push and verify Streamlit Cloud**

Push the implementation branch, merge only after the full suite passes, then push `main`. Wait for Streamlit Cloud to deploy the new commit and repeat the clean-profile, reload, selector, and representative-page checks against `https://mundial-2026-antagarc.streamlit.app/`.

- [ ] **Step 7: Record release evidence**

Report the final commit, test count, deployment commit, browser-persistence results, both-language visual checks, data-invariant result, and the pre-change commit that can be reverted to recover the previous application.
