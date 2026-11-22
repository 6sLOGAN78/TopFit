---
phase: 02-live-jobs-index
plan: 13
subsystem: ingest-ops
tags: [scheduler, worker, admin, selfcheck, compose]
requires: [02-11, 02-12]
provides:
  - "ingest and expire_ttl task handlers; enrich_enqueued on RunSummary"
  - "register_source_jobs, reap_abandoned_runs (worker/scheduler.py)"
  - "POST /api/admin/ingest/{source} with require_admin dependency"
affects: [02-14, 02-15, 02-16]
tech-stack:
  added: []
  patterns: ["D-17 morning-window validation at worker start", "swappable require_admin dependency"]
key-files:
  created: [backend/ranker/api/admin.py, backend/tests/test_ingest_task.py, backend/tests/test_scheduler_jobs.py, backend/tests/test_admin_ingest.py]
  modified: [backend/ranker/ingest/pipeline.py, backend/ranker/tasks/handlers.py, backend/ranker/worker/scheduler.py, backend/ranker/worker/__main__.py, backend/ranker/selfcheck.py, backend/ranker/api/__init__.py, backend/ranker/settings.py, .env.example, backend/config/sources.yaml, compose.yaml, deploy/compose.prod.yaml]
decisions:
  - "enqueue_enrichment runs after finish_run; its count is on RunSummary.enrich_enqueued, not in ingest_runs.notes"
  - "Schedule config is validated by a throwaway register_source_jobs call before the worker loop, so a bad config crashes the worker at start"
metrics:
  tasks: 3
  tests: "777 passed (22 new for this plan)"
---

# Phase 2 Plan 13: Scheduling, ingest tasks and admin trigger Summary

The leader worker now schedules every enabled, configured source (D-17 morning window enforced), runs queued `ingest` and `expire_ttl` tasks, and an admin can trigger a source over HTTP.

## Commits
- 408ff0a: ingest/expire_ttl handlers, enrichment enqueue after each run
- 9ff24fa: scheduler registration, reaper, selfcheck configs, memory limits, sources.yaml stagger table
- 2a7201c: admin trigger endpoint, ADMIN_TOKEN setting

## Notes
- Ingest handler treats SourceLocked, RunQuotaReached, SourceNotConfigured (and UnknownSource) as done with an `ingest_skipped` log. One embedder is built lazily per worker.
- Scheduler: sweeps at TTL 00:30 UTC, enrich backlog 01:30 UTC, hourly abandoned-run reaper (runs older than 2 h, also run once at leadership acquisition). Sources not registered are logged and skipped; absence-policy intraday runs are documented in the table but only the morning entry and `max_runs_per_day` are enforced.
- selfcheck loads all eight config files and lists them under `configs`; still no get_settings.
- Admin: 404 unset, 401 via `hmac.compare_digest`, 404 unknown source, 202 `{task_id}`, 409 pending, mode Literal (422). Logs only source and task_id.

## Deviations from Plan

**1. [Rule 1 - Bug] Adzuna schedule did not satisfy D-17.** It was 22:00 UTC with no `batch: morning`. Changed to `23:40 batch: morning` (still `enabled: false`) and updated the comment. File: backend/config/sources.yaml. Commit 9ff24fa.

**2. [Rule 2 - Missing critical] Fail-at-start for bad schedules.** A ConfigError raised inside the leader loop would have been swallowed by the generic exception handler (logged as db_unavailable, leadership dropped, retried forever). Added an upfront validation call in worker `run()`. Commit 9ff24fa.

No other deviations.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model (T-02-44 to T-02-48 mitigations implemented; brute-force rate limiting deferred to SEC-04 as planned).

## Verification
- `uv run pytest -q`: 777 passed, 1 deselected; ruff check, ruff format --check and pyright clean.
- `docker compose -f compose.yaml config -q` OK; compose.prod.yaml has 2 mem_limit entries, `ports:` count unchanged (2). No apscheduler in api or main.py.
- Docker `--network none` selfcheck run was not executed locally (CI does it).

## Self-Check: PASSED
