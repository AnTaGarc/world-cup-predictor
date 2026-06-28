# Streamlit Interaction Performance Design

## Objective

Make the application feel immediate when changing analysis views and when editing odds or player-market inputs, without changing any probability, statistic, confidence, EV formula, market output, or stored data semantics.

The work targets rerun scope, repeated data preparation, and cache invalidation. It does not tune the prediction model. The penalty-history model is explicitly deferred to a separate follow-up.

## Current State and Evidence

The prediction page already has useful performance boundaries:

- `_match_analysis_bundle_cached` caches the expensive per-match prediction bundle.
- Secondary volume and goalkeeper work is deferred to cached auxiliary bundles.
- Only the selected analysis section is rendered.
- The global player-intelligence page caches profiles and uses a fragment for ranking interactions.

The remaining interaction cost comes from four paths:

1. Controls inside the prediction workspace still trigger a Streamlit rerun above their immediate calculation.
2. The odds editor rebuilds saved-odds queries, market rows, localization, indexes, tables, and visual output when one value changes.
3. The player calculator rebuilds lineups, roster filtering, display tables, market availability, and goalkeeper context when the player, line, or odds changes.
4. Refresh/import paths call global `st.cache_data.clear()` and `st.cache_resource.clear()`, discarding unrelated models, repositories, collector stores, schedules, and cached analyses.

The mathematical operations for EV and a single player market are already cheap. The design therefore reduces the amount of application code rerun around those operations instead of changing their formulas.

## Constraints

- Preserve all current model outputs bit-for-bit for identical inputs.
- Preserve immediate calculation when a user changes an odds or player input; do not add a mandatory Calculate button.
- Preserve the existing Streamlit information architecture and visible sections.
- Preserve local and Streamlit Cloud operation.
- Do not introduce background workers, new services, or new runtime dependencies.
- Do not include or overwrite the current uncommitted `data/worldcup.sqlite` change or unrelated untracked files.
- Use test-first implementation for every behavioral boundary or refactor.

## Considered Approaches

### 1. Scoped fragments and prepared view models — selected

Keep the expensive match bundle as the source of truth, isolate the interactive workspace from the page shell, and prepare immutable odds/player contexts once per relevant data signature. Widget interactions then rerun only the workspace and perform only the small calculation that depends on the changed value.

This preserves immediate feedback and requires no infrastructure change.

### 2. Forms and explicit submit buttons

Forms would prevent reruns while typing, but would add friction to odds comparison and player-market exploration. This conflicts with the approved immediate-calculation requirement.

### 3. Background precomputation

Threads or external workers could prewarm every match and view, but would increase complexity, memory use, and deployment risk on Streamlit Cloud. Most of the required data is already cached, so this would solve the wrong layer.

## Proposed Architecture

### Prediction page shell

`render_prediction_lab` remains responsible for operations that should happen once on a full page run:

- repository and daily-data state;
- match selection and page header;
- current match analysis bundle;
- refresh/import actions that can change persisted evidence.

It passes immutable match and bundle inputs into a single fragment-backed prediction workspace. A widget interaction inside that workspace must not rerun daily refresh, match-label construction, the page hero, collector availability checks, or the main bundle lookup path.

The fragment owns the analysis-section selector and renders only the selected section. No nested fragment hierarchy is required.

### Odds context and calculation

Introduce a focused prepared context for the selected match containing:

- localized default market rows;
- the prediction index used for market lookup;
- saved odds and the data needed by the static market summary;
- auxiliary volume data already produced by the existing cached bundle.

This context is cached by match ID, database signature, and prediction-engine version. Editing the data editor only performs:

1. canonicalization of edited values;
2. normalization of populated odds;
3. lookup in the prepared prediction index;
4. `compare_odds_to_probability` for populated comparable rows;
5. rendering of the resulting EV ranking.

The formulas for draw-no-bet push probability, fair odds, edge, and EV remain unchanged.

Saving odds writes to the repository and invalidates only match-dependent saved-odds/prepared contexts. It must not unload ML artifacts, the repository, or collector resources.

### Player context and calculation

Introduce a focused prepared player-market context for the selected match containing:

- imported lineup state;
- players indexed and sorted by canonical team;
- available market families per player;
- opponent shots-on-target expectations;
- goalkeeper baselines;
- prebuilt rows for the static squad table.

The context is cached by match ID, database signature, and prediction-engine version. Changing team, position, player, family, line, or odds only performs the dependent selection and the existing calls to:

- `derive_player_assumption`;
- `estimate_player_market_probability`;
- `compare_odds_to_probability` when odds are present.

No rate, minutes rule, lineup adjustment, goalkeeper rule, confidence rule, or player-market probability formula changes.

### Cache invalidation

Replace global cache clears in prediction/player refresh paths with narrow invalidation functions.

The default mechanism remains signature-based invalidation: a database or model file change produces a new cache key. Where immediate eviction is needed, clear only the affected cached functions. Repository and collector resource caches remain alive unless their own configuration/path changes.

Each mutation path documents which contexts it invalidates:

- match evidence import: selected match analysis, volume, auxiliary, odds, and player contexts;
- odds save/import: saved-odds and odds prepared context only;
- player-bank refresh: match analysis/player contexts and global player-intelligence rows;
- settlement/deep-stat import: all prediction contexts keyed by the new database signature, without clearing model/resource caches.

## Data Flow

For a cold match selection:

1. The page shell resolves daily state and the selected match.
2. Existing cached builders compute or retrieve the main analysis bundle.
3. The workspace renders the selected section.
4. Odds or player prepared context is built only if that section is opened.

For a warm widget interaction:

1. Streamlit reruns the prediction workspace fragment.
2. Prepared context is retrieved from cache.
3. Only edited odds or selected player inputs are recalculated.
4. The result is rendered without executing the page shell or prediction engine.

## Error Handling

- Missing player or lineup data keeps the current warnings and low-confidence behavior.
- Missing comparable markets continue to produce no invented EV.
- Cache preparation failures surface through the existing section-level UI rather than silently altering results.
- Refresh failures preserve the last valid cached data, matching current behavior.
- Narrow invalidation must never be required for correctness: database/model signatures remain the fallback correctness boundary.

## Verification and Success Criteria

Automated tests will prove that:

- odds edits do not invoke prediction, volume, player-profile, or daily-refresh builders;
- player control changes do not invoke those builders;
- changing the analysis section reruns only the workspace boundary;
- odds and player outputs match pre-refactor fixtures exactly;
- refresh and save paths do not call global Streamlit cache clearing;
- affected prepared contexts are invalidated after writes;
- the complete existing test suite remains green.

The implementation will also include a deterministic call-count harness around the expensive builders. Wall-clock thresholds are intentionally secondary because CI and Streamlit Cloud hardware vary. The acceptance target is one expensive computation on a cold key and zero expensive recomputations for warm odds/player interactions.

## Out of Scope

- Changing xG, Dixon-Coles, Negative Binomial, ML weights, volume models, or EV mathematics.
- Improving `penalty_history_model.py`.
- Combining the 611 historical penalty attempts with likely takers and opposing goalkeeper data.
- Visual redesign unrelated to responsiveness or interaction cost.
- Database cleanup, deployment, or committing the current SQLite working-tree change.

## Follow-up: Penalty History

The prior Claude conversation specified a later model using 611 historical Transfermarkt penalty attempts, the likely five starting takers, opposing-goalkeeper save percentage, recent conversion, and team shootout asymmetry. The current implementation only regularizes aggregate team conversion and can override the goalkeeper fallback with a neutral 50/50 context. That work will receive its own design and implementation cycle after this optimization is verified.
