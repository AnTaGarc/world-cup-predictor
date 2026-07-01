# Daily Refresh Partial Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify mixed daily-provider outcomes as partial and expose the failed provider's stored error without mislabeling the calendar as obsolete.

**Architecture:** Keep status classification in `daily_refresh.py`, where all provider outcomes are known. Add a small UI helper in `pages.py` that reads the latest failed checks for `DailyRefreshResult.failed` and renders safe Streamlit text.

**Tech Stack:** Python 3.12, Streamlit, SQLite repository, `unittest`.

## Global Constraints

- Preserve cached snapshots and the existing one-hour retry backoff.
- Do not change import, prediction, or bracket behavior.
- Render provider errors as Streamlit text, never provider-controlled HTML.

---

### Task 1: Correct mixed refresh classification

**Files:**
- Modify: `tests/test_daily_refresh.py`
- Modify: `src/wcpredict/daily_refresh.py`

**Interfaces:**
- Consumes: existing `ensure_current_world_cup_data(...)`.
- Produces: unchanged `DailyRefreshResult` API with corrected `status` semantics.

- [ ] Add a failing test with one cached provider returning 403 and another recent successful provider; assert `status == "partial"`, one failed provider and one skipped provider.
- [ ] Run `python -m unittest tests.test_daily_refresh -v`; verify it fails with `stale != partial`.
- [ ] Change final classification so any failure plus any `updated`, `unchanged`, or `skipped` result yields `partial`; only all-provider failures yield `stale` or `failed`.
- [ ] Run the daily-refresh tests and verify all pass.

### Task 2: Clarify dashboard label and expose error detail

**Files:**
- Modify: `tests/test_app_contract.py`
- Modify: `src/wcpredict/ui/pages.py`

**Interfaces:**
- Produces: `_daily_refresh_failure_details(repo, daily_result) -> list[str]` and a dashboard expander.
- Consumes: `Repository.list_dataset_refresh_checks(provider_id)`.

- [ ] Add failing contract tests requiring `Datos diarios`, `_daily_refresh_failure_details`, and a `Detalle de errores` expander.
- [ ] Run the focused contract test and verify it fails.
- [ ] Implement the helper using only provider ids from `daily_result.failed`, latest check messages truncated to 240 characters, and render each with `st.write` inside an expander.
- [ ] Run `tests.test_app_contract`, `tests.test_daily_refresh`, then the complete suite.
- [ ] Run `git diff --check`, commit, and publish through `push_project.ps1` so durable data changes are included and caches remain excluded.
