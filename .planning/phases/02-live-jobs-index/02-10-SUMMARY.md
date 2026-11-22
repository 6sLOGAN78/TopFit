---
phase: 02-live-jobs-index
plan: 10
subsystem: ingest
tags: [adzuna, quota, backfill, redaction]
requires: [02-01, 02-08]
provides:
  - AdzunaAdapter (query slices, paging, quota budget, resumable backfill cursor)
  - source credential redaction (logs, run records)
affects: [02-11]
key-files:
  created:
    - backend/ranker/ingest/sources/adzuna.py
    - backend/tests/fixtures/adzuna_search_page1.json
    - backend/tests/fixtures/adzuna_search_page2.json
    - backend/tests/test_sources_adzuna.py
    - backend/tests/test_ingest_redaction.py
  modified:
    - backend/ranker/ingest/sources/__init__.py
    - backend/config/sources.yaml
    - backend/ranker/settings.py
    - backend/ranker/observability.py
    - .env.example
requirements-completed: [ING-02, ING-07, ING-04]
completed: 2026-11-22
---

# Phase 2 Plan 10: Adzuna adapter and credential redaction

Adzuna India search adapter under a quota budget computed from `SUM(calls_used)` in `ingest_runs` (UTC day and month), counting every HTTP attempt (including retries) through `get_json`'s `on_attempt`; credentials go only in `params`. Backfill resumes from `notes.backfill_cursor`, and `backfill.auto` spends leftover budget on it after the incremental slices until `backfill_complete` is recorded. Logging redaction now masks `app_key`, `app_id`, `api_key` and admin tokens, scrubs `key=value` secrets in free text, and silences httpx/httpcore INFO URL logging.

## Commits
- 4d67889 Task 1: Adzuna adapter, config, settings, fixtures, tests
- 3c119b4 Task 2: redaction and end-to-end canary test

## Public signatures
- `ranker.ingest.sources.adzuna`: `AdzunaAdapter(ctx, *, sleep=asyncio.sleep)`, `AdzunaConfig`, `AdzunaQuery`, `AdzunaQuota`, `AdzunaBackfill`; registered as "adzuna", `expiry_policy="ttl"`.
- `extra_notes` keys: `budget_exhausted`, `backfill_cursor` ({slice: next page, 0 = finished}), `backfill_complete`.
- Failed scope string: `HTTPStatusError:429` etc. under scope `search`.
- Settings: `adzuna_app_id`, `adzuna_app_key` (`SecretStr | None`).

## Deviations from Plan

**1. [ADR 0001 values] Adzuna is disabled with a zero budget.** The ADR records `Adzuna calls/day budget: 0` (no paid plan, 25 test calls only). `sources.yaml` therefore has `enabled: false`, `calls_per_day: 0`, `calls_per_month: 0`; the schedule entry (22:00 UTC) is kept so enabling it later is a config-only change. With budget 0 the adapter makes no calls even if run manually.

**2. [Test design] CLI JSON output** is the run summary only (no URLs or keys by construction); the end-to-end test asserts on the serialised `RunSummary` rather than invoking Typer.

No other deviations. `pipeline.py` and `cli.py` were not touched.

## Verification
Full suite 705 passed (1 deselected llm), ruff and pyright clean. No Adzuna or other live network calls; respx only; fake keys only.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
