# Acceptance Criteria

1. The full unittest suite passes with fresh evidence and covers every behavior added or corrected by this audit.
2. The app runs locally in Streamlit, all five Spanish navigation views render, and the primary desktop workflow has no relevant runtime or browser-console errors.
3. SQLite remains canonical and retains provenance/timestamps for collector data, deep reviewed JSON, manual edits, SofaScore imports, lineups, odds, predictions, results, and evaluations.
4. Schedule fixtures are selectable and canonical team aliases do not duplicate or lose matches.
5. Refresh is bounded to one event, zero automatic odds credits, timeout/cached fallback, sanitized failures, and no exposed keys.
6. All finished results available before prediction time feed form; future matches never leak; friendlies are downweighted; tournament/recency/opponent effects and shrinkage are visible and bounded.
7. The 28 reviewed deep-stat dossiers remain linked and idempotent. Primary xG/shots/SOT/possession/corners/cards populate team stats while all numeric leaves remain auditable observations.
8. Deep xG and volume rows affect only later fixtures. The target fixture is excluded from its own prediction and calibration feature construction.
9. A post-match queue distinguishes missing final scores from missing statistics. It must not say `pendientes de resultado/estadísticas` when statistics already exist.
10. For a selected post-match fixture, the UI shows existing deep/team-stat coverage, presents imported statistics read-only, asks only for missing statistics, and explains that the score is required for outcome/Brier evaluation.
11. Missing final goals never prevent imported team statistics from feeding later form and supported volume-market estimates. Conversely, statistics alone never fabricate a winner or Brier outcome.
12. Settling a score is idempotent, marks the match finished, feeds later form, and automatically evaluates every compatible saved prediction without duplicate backtests.
13. Calibration reports Brier score, hit rate, probability bands, market-family reliability, sample sizes, and drift; small samples cannot promote confidence.
14. Core match predictions expose probability, fair price where applicable, low/base/high range, confidence, origin, sample size, and explanation. No-history output is explicit low-confidence baseline.
15. Corners/cards/shots/SOT are estimated only from complete observed for/against rates; unsupported cases remain not estimable rather than invented.
16. Odds entry supports an editable multi-market table and strict CSV import. Comparable rows produce implied probability, fair odds and ranked EV; unmatched odds remain storable without fabricated EV.
17. The operational score model and chronological ML appear on the same percentage scale, each sums to 100%, their distinct inputs/status are explained, and ML remains a challenger until temporal validation supports promotion.
18. Coverage counts collector statistics, deep statistics, sources, daily player-bank rows, and confirmed lineups separately. A missing lineup must not be displayed as zero available players.
19. Player selection is available separately for both teams. The user chooses player, market, line, and odds; rates per 90, expected minutes, starter probability, sample size, and opponent baseline are derived from observed data.
20. Missing player metrics remain null/unknown. Missing passes never become zero, unavailable player markets are not offered or are not estimable, and zero-minute rows never divide by zero.
21. Player intelligence has separate impact, goals, assists, and shots views with minutes/sample context. Passes appear only when genuinely observed; style labels remain interpretable and bounded in cost.
22. Long audit ledgers and technical model policy tables remain available on demand but do not dominate the main reading path. Essential probability/confidence/source/missing-data values remain visible without hover.
23. SofaScore URL import is optional, previews before persistence, preserves public-source metadata, uses no browser cookies/secrets, and fails without destroying cached data.
24. Data Quality exposes freshness, completeness, stale/partial/conflicting states, provider status, manual corrections, and source hierarchy.
25. README documents setup, refresh limits, data precedence, manual/CSV odds, deep JSON, SofaScore limitations, player-data limitations, settlement/calibration, tests, and run commands.
26. No credentials, runtime DB, provider caches, raw private responses, temporary QA scripts, or unrelated workspace files are staged or committed.
27. Fresh-context review maps the original approved spec plus later deep-data/player/clarity requirements to concrete code, test, database, and rendered evidence before `SHIP`.
28. Opening the normal dashboard runs the 24-hour freshness gate; it does not require visiting a particular old match to discover current fixtures.
29. The daily match provider upserts all fixtures with known teams into the canonical `matches` table, including newly resolved knockout fixtures, without duplicating an existing date/team pairing. Unknown knockout placeholders are skipped until both teams are known.
30. Dashboard and match selector expose every known fixture from today through the next two calendar days; if the provider has no such fixture, the UI says so explicitly rather than showing an arbitrary historical first row.
31. Match prediction reports the most probable exact score and its probability from the same normalized score matrix used for goals/1X2. A deterministic test verifies the score/probability and the rendered label.
