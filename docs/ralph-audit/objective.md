# Objective

Audit and finish the local Streamlit World Cup 2026 prediction and EV workbench against the complete original approved design and every later user requirement. Work in the existing dirty branch without discarding, committing, merging, or overwriting unrelated accumulated work.

The product must be a usable Spanish analyst workbench, not a demo or a black-box betting bot. It must select scheduled World Cup fixtures, ingest and preserve real evidence with provenance, model match/team/volume/player markets honestly, accept manual or CSV bookmaker odds, compute fair odds and EV, store pre-match snapshots, settle results, and measure calibration.

Current-form and deep evidence are mandatory. The reviewed JSON contains 28 complete team-statistic match dossiers but generally lacks final goal scores and named player statistics. Those team statistics must remain linked to their fixtures, feed only later predictions through chronological xG and volume features, never leak the target match into itself, and remain distinguishable from final scores. A missing score may block outcome/Brier evaluation, but must never cause the UI to claim that already-imported statistics are missing.

The audit must also verify the latest UX requirements: reduce decorative or redundant charts/columns, compare the operational score model and chronological ML on one 1X2 scale with a plain-language explanation, count players from every active data bank instead of only imported lineups, preserve absent passes as unknown rather than zero, provide separate player views for impact/goals/assists/shots, and derive player rates/minutes/starter probability automatically after the user selects team, player, market, line, and odds.

Completion requires a fresh-context work phase, fresh-context review phase, configured verification, and reviewer `SHIP` against the acceptance criteria. If an external limitation remains, surface it precisely in-product and in the Ralph state rather than inventing data.

The calendar must be operational rather than a static seed. On normal daily use the app must refresh tournament fixtures and results at most once per 24 hours, upsert newly known group or knockout fixtures when both teams become known, and expose at least all known matches scheduled for today and the following two calendar days. Predictions must use the evidence already stored before each kickoff. Match detail must also report the single most probable exact score from the normalized score matrix, alongside 1X2 rather than replacing it.
