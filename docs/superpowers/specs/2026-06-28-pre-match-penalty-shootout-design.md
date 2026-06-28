# Pre-Match Penalty Shootout Model Design

## Objective

Build a pre-match penalty-shootout model for the World Cup 2026 knockout stage that uses the whole registered squad, not a fixed group of starters. The model must account for the probability that starters are substituted, substitutes enter, and different players remain eligible to take penalties after extra time.

The output must keep three probabilities separate:

- probability that the match reaches a shootout;
- conditional probability that each team wins if a shootout occurs;
- total probability that each team advances through regulation, extra time, or penalties.

This is a pre-match model. It may refresh when confirmed lineups become available, but it will not consume live substitutions or in-play events.

## Canonical Qualified Teams

The user-provided Round-of-32 image is the authoritative snapshot while the remaining group results and best-third assignments are being settled in SQLite.

The 32 qualified teams, stored under project canonical names, are:

1. Germany
2. Paraguay
3. France
4. Sweden
5. South Africa
6. Canada
7. Netherlands
8. Morocco
9. Portugal
10. Croatia
11. Spain
12. Austria
13. USA
14. Bosnia and Herzegovina
15. Belgium
16. Senegal
17. Brazil
18. Japan
19. Cote d'Ivoire
20. Norway
21. Mexico
22. Ecuador
23. England
24. DR Congo
25. Argentina
26. Cape Verde
27. Australia
28. Egypt
29. Switzerland
30. Algeria
31. Colombia
32. Ghana

Source aliases in the player bank must resolve as follows:

- `United States` -> `USA`
- `Bosnia & Herz.` -> `Bosnia and Herzegovina`
- `Côte d'Ivoire` -> `Cote d'Ivoire`
- `Congo DR` -> `DR Congo`
- `Cabo Verde` -> `Cape Verde`

The explicit snapshot prevents incomplete bracket state from suppressing data collection. Once the database resolves all 32 teams, an automated check must confirm that the dynamic bracket set matches this snapshot. A mismatch is reported; it does not silently add or remove teams.

## Current Coverage

The local database currently contains:

- 611 historical penalty attempts;
- 82 distinct takers;
- 9 teams with penalty history: Argentina, Brazil, Canada, Germany, Mexico, Morocco, South Africa, Switzerland, and USA;
- 368 players with tournament minutes among the 19 teams currently considered eligible by the unfinished dynamic bracket;
- no reusable Transfermarkt identity mapping exposed by `list_transfermarkt_player_ids`, even though historical `penalty_attempts` rows already contain Transfermarkt IDs.

The ingestion layer must therefore reuse IDs found in `penalty_attempts` before attempting a new search.

## Data Sources

### Qualified-team snapshot and bracket

- User-provided Round-of-32 image, normalized into a tracked fixture file.
- `knockout_bracket` and group standings for automatic reconciliation after outstanding matches are settled.

### Player availability and role

- `current_wc_player_stats`: full registered team banks (up to 26 players), position, appearances, starts, minutes, goals, assists, shots, cards, and general goalkeeper save percentage.
- `imported_lineups`: confirmed starters and bench when available before kickoff.
- `squad_context_events`: injuries, illnesses, suspensions, and other reviewed availability changes.

Players with zero tournament minutes remain in the squad pool. Zero minutes means no tournament evidence, not impossibility of appearing in a knockout match.

### Penalty history

- Transfermarkt player identity and penalty-history pages.
- `penalty_attempts`: taker, team, date, competition, regular/shootout phase, scored/missed outcome, opposing goalkeeper, opponent, minute, source URL, and reviewed identity.
- Identity-review CSV for ambiguous or missing Transfermarkt matches. Only exact/high-confidence identities may be accepted automatically.

### Goalkeeper evidence

- `current_wc_player_stats.save_percentage` as the broad current-tournament bank signal.
- `team_match_stats.saves`, opponent shots on target, and goals conceded.
- recency-weighted `GoalkeeperBaseline` from reviewed deep match statistics.
- penalty-specific outcomes grouped by `penalty_attempts.goalkeeper_name` when identity and sample are sufficient.

### Match-path evidence

- regulation xG already adjusted by form, opponent profile, player availability, goalkeeper evidence, and corrections;
- the existing extra-time score matrix using 30% of regulation xG;
- the existing probability chain for regulation draw and extra-time draw.

These inputs determine whether a shootout is reached. They must not be mixed into the conditional shootout-win calculation as if they were penalty-taking skill.

## Considered Approaches

### 1. Constrained Monte Carlo at minute 120 — selected

Simulate plausible on-field elevens at the end of extra time, select takers from each simulated eleven, and simulate the shootout including early termination and sudden death. This captures substitution uncertainty and produces interpretable player contributions.

### 2. Weighted squad-average conversion

Average every player's conversion using an on-field probability. This is faster but loses the dependence between who remains on the pitch, the first five takers, and sudden death.

### 3. Fixed five-taker list

Select five named takers before kickoff. This is simple but fails the central requirement because likely takers may start on the bench or leave the pitch before minute 120.

## Architecture

### 1. Qualified-team fixture and reconciliation

Create a tracked fixture containing the 32 canonical teams. Data collection reads this fixture during the current incomplete-bracket period. A reconciliation function compares it to dynamically resolved knockout teams and returns missing/unexpected names for the data-quality UI and tests.

### 2. Identity and history ingestion

Extend identity lookup to build its map from both the existing identity store and distinct `(player_name, team_name, transfermarkt_player_id)` rows in `penalty_attempts`.

Every player in each qualified registered squad is a target, including players with zero minutes. Collection order is prioritized by probability of being present at minute 120, but lack of a fetched page never removes a player from the model; it leaves that player on a prior.

Transfermarkt requests remain cached and rate-limited. Ambiguous identities are written to the review CSV and excluded from automatic history assignment.

### 3. Player penalty profile

Each squad member receives a `PenaltyPlayerProfile` containing:

- total scored and attempted;
- shootout scored and attempted;
- regular-time scored and attempted;
- recency-weighted conversion;
- posterior conversion and uncertainty interval;
- taker-propensity score;
- data-quality/confidence label.

Conversion uses a Beta prior centered on the global historical conversion rate. Shootout attempts receive more relevance than regular-time attempts, recent attempts decay less, and small samples remain close to the global prior. Missing data is represented by the prior, never by zero conversion.

Taker propensity uses historical attempt volume, recency, and shootout experience. Position provides only a weak fallback when no penalty history exists.

### 4. Probability of being on the field at minute 120

Build a `PlayerOnFieldProfile` for every registered squad member.

Before confirmed lineups, the model uses:

- starts divided by team matches, with shrinkage;
- appearances divided by team matches;
- minutes per appearance;
- position-specific substitution survival;
- goalkeeper continuity;
- reviewed availability events.

After confirmed lineups, starter and bench status replaces the uncertain start component but not substitution/survival uncertainty.

The output is a weight for ending extra time on the pitch. These independent weights are not treated as a valid lineup by themselves.

### 5. Constrained end-of-extra-time lineup sampler

Each simulation samples exactly one goalkeeper and ten outfield players from the available squad, weighted by the minute-120 profiles. It must:

- exclude confirmed unavailable players;
- allow zero-minute squad members with a conservative prior;
- strongly favor a confirmed starting goalkeeper while retaining a small replacement probability;
- preserve at least a minimally plausible positional composition;
- use a deterministic seed derived from the match and model version.

The sampler does not attempt to recreate the exact substitution timeline. It models the only state needed by the shootout: who is eligible at the end of extra time.

### 6. Taker selection and shootout simulation

For each sampled eleven:

1. Select the first five takers without replacement using taker-propensity weights.
2. Order them using propensity plus a small deterministic tie-breaker.
3. Keep the remaining eligible players for sudden death.
4. Calculate each kick's scoring probability from the taker's posterior conversion and the opposing goalkeeper's penalty-saving profile.
5. Simulate the first five rounds with early termination.
6. Continue sudden-death pairs until one team wins.

All eligible players, including the goalkeeper, must take before any player may take a second kick; the simulated order enforces that rule.

The model returns team win probability, uncertainty, expected taker order, and each player's probability of appearing among the first five.

### 7. Goalkeeper model

Build the goalkeeper penalty-saving estimate in layers:

1. penalty-specific faced history when the goalkeeper identity and sample are adequate;
2. recency-weighted deep save baseline;
3. daily-bank save percentage;
4. global penalty-save prior.

Each layer is shrunk toward the next broader layer. General save percentage may influence the penalty estimate modestly but must not be interpreted as penalty save percentage.

The current defect where an empty team history forces a neutral 50/50 value and bypasses goalkeeper evidence must be removed. With no taker history, goalkeeper evidence may still tilt the conditional shootout probability.

### 8. Team-level experience

Team shootout experience is derived only from attempts marked `phase=shootout`. It is a small, heavily shrunk contextual adjustment; it cannot dominate individual taker and goalkeeper evidence or double-count the same attempts.

### 9. Match integration

The conditional shootout probability replaces the current aggregate-team `home_penalty_win_probability` input to `predict_knockout_match`.

The existing chain remains:

```text
P(advance)
= P(win in 90)
+ P(draw in 90) * P(win in extra time)
+ P(draw after extra time) * P(win shootout | shootout reached)
```

Regulation, extra-time, and shootout components must still sum to one across both teams.

## UI

The knockout panel shows:

- `Llega a penaltis`: unconditional probability from the regulation/extra-time chain;
- `Si hay tanda`: conditional win probability for each team;
- most likely first-five takers, each with probability of being on the pitch, probability of taking one of the first five kicks, posterior conversion, and sample;
- likely goalkeeper at minute 120 and evidence source;
- coverage summary: resolved identities, players with history, attempts, and prior-only players;
- an expandable explanation of substitution uncertainty and fallbacks.

The primary advancement probability remains the headline. Taker details explain the penalty branch rather than replacing it.

## Caching and Performance

The model is pre-match and deterministic. Cache the penalty match context by:

- match ID;
- database signature;
- confirmed-lineup signature;
- penalty-history/model version.

Monte Carlo runs only on a cold key. Changing analysis views, odds, or unrelated player markets retrieves the cached result. Data refresh invalidates only penalty and affected match-analysis contexts.

The initial simulation target is 10,000 shootouts per match. Tests use a smaller fixed count. The production count may be adjusted only after measuring runtime and convergence.

## Error Handling and Honest Fallbacks

- Missing player history -> global conversion prior plus wide uncertainty.
- Missing Transfermarkt identity -> prior-only player, visible in coverage.
- Missing lineup -> tournament start/appearance model.
- Missing goalkeeper penalty history -> deep/general save evidence, then global prior.
- Missing all squad data -> team-level prior with explicit low confidence; no invented player list.
- Transfermarkt failure -> preserve cached pages and existing attempts.
- Snapshot/bracket mismatch -> surface both sets and require review; never silently alter the canonical 32.

## Testing

Tests must prove:

- all 32 provided teams survive alias normalization;
- every registered squad member is eligible as a target, including zero-minute players;
- existing Transfermarkt IDs in `penalty_attempts` are reused;
- ambiguous identities are not auto-assigned;
- substitutes can appear in sampled minute-120 lineups;
- every sampled lineup has exactly one goalkeeper and ten outfield players;
- unavailable players never appear;
- a strong historical taker is selected more often but small samples remain shrunk;
- no-history players receive the prior, not zero;
- shootout and regular penalties receive distinct weights;
- shootouts terminate correctly in the first five rounds and sudden death;
- simulation is deterministic for the same seed;
- team conditional win probabilities sum to one;
- no-history team context still uses goalkeeper evidence;
- advancement-method probabilities remain normalized;
- the Streamlit knockout panel renders with full, partial, and prior-only coverage;
- the complete existing suite remains green.

## Out of Scope

- Live in-play substitution/event ingestion.
- Betting placement or bookmaker-account integration.
- Treating general save percentage as direct penalty save percentage.
- Claiming exact substitution sequences before kickoff.
- Automatically accepting low-confidence Transfermarkt identity matches.
