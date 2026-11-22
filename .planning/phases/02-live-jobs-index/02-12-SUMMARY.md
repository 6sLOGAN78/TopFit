---
phase: 02-live-jobs-index
plan: 12
subsystem: ingest
tags: [llm, enrichment, tasks, norm-03, norm-05]
requires: [02-02, 02-03, 02-08]
provides:
  - enqueue_enrichment(engine, run_id=None, limit=5000) -> EnqueueStats
  - make_enrich_handler / make_backlog_handler; kinds enrich_job and enrich_backlog registered
  - PROMPT_VERSION = "enrich-v1", PURPOSE = "job_enrich"
affects: [02-13 wires enqueue_enrichment into run_ingest and schedules enrich_backlog]
key-files:
  created: [backend/ranker/ingest/enrich.py, backend/tests/test_enrichment.py]
  modified: [backend/ranker/tasks/handlers.py]
key-decisions:
  - "Deferral cap rule: no key (gateway.available() False) and BudgetExceeded defer free and unlimited. A provider outage with a key defers 6 h +/-10% only while tasks.created_at is under 7 days old (OUTAGE_DEFER_MAX_AGE); after that LLMUnavailable is re-raised so attempts are consumed with backoff until dead at max_attempts=5."
  - "BudgetExceeded defers until resets_at plus up to 1 h jitter."
  - "apply_enrichment is one guarded UPDATE: CASE per column so only still-unknown columns change, eligibility only for remote_type = 'remote', WHERE id and content_hash match and status active."
metrics:
  tasks: 2
  completed: 2026-11-22
---

# Phase 2 Plan 12: LLM Enrichment Summary

Unknown remote_eligibility, seniority and employment_type become deduplicated `enrich_job` tasks (key `enrich:<id>:<hash>`), filled from `llm_enrichment_cache` when possible, otherwise by a Haiku `JobEnrichment` call in the worker. Job text is wrapped in `<job>` tags with an "untrusted data" system prompt (excerpt: first 3,000 + last 1,000 chars); output is three enums only.

## Commits

- 979f1c6 feat(02-12): enrichment enqueue, handler, registration (both tasks in one commit)

## Tests

`uv run pytest -q`: 700 passed, 1 deselected. `ruff check`, `ruff format --check`, `pyright` clean. 10 new tests cover selection, dedupe, backlog mode, cache short-circuit, fill-only-unknown, prompt shape, hash-changed no-call, 10-pass no-key deferral (attempts 0, no llm_calls), budget deferral, outage cap (1 day deferred; 8 days consumes attempts to dead), LLMOutputInvalid failure path, handler registration.

## Deviations from Plan

- [Minor] Both tasks were committed together since the handler and enqueue share one module and test file.
- [Minor] `enqueue` takes `delay_seconds` (02-02 API), not `run_after`; unused here.
- `from_cache` counts only jobs actually changed by the cached result. A job whose cached result is all `unknown` stays selectable by backlog sweeps (cheap no-op, but consumes `limit` slots).
- Added `# noqa: S608` on the SELECT built from static SQL fragments with bound params.

## Known Stubs

None.

## Self-Check: PASSED

enrich.py, handlers.py, test_enrichment.py exist; commit 979f1c6 in history.
