---
phase: 01-foundations-walking-skeleton-deploy
plan: 03
subsystem: observability
tags: [structlog, asgi-correlation-id, redaction, logging]
requires: [01-01]
provides:
  - ranker.observability (configure_logging, redact, REDACT_KEYS, RequestLogMiddleware)
  - JSON logs with request_id in API, worker and Alembic
affects: [01-04]
key-files:
  created: [backend/ranker/observability.py, backend/tests/test_logging_redaction.py, backend/tests/test_request_id.py]
  modified: [backend/ranker/main.py, backend/ranker/worker/__main__.py, backend/migrations/env.py]
requirements-completed: [SEC-07, OPS-04]
completed: 2026-11-21
---

# Phase 1 Plan 03: Structured Logging and Redaction Summary

Single-line JSON logging for API, worker, Alembic, uvicorn, SQLAlchemy and loguru (fastembed), with request IDs on every request log line and a redaction processor (keys at any depth, emails in text) placed after the exception renderer (show_locals=False) and before the JSON renderer.

## Tasks
| Task | Commit | Notes |
|------|--------|-------|
| 1 RED tests on rendered output | 6c341bb | 8 failing tests (module missing) |
| 2 GREEN implementation + wiring | 105d062 | 18 tests pass, ruff and pyright clean |

## Deviations from Plan
- Tests: the stdlib extras case logs at WARNING (sqlalchemy.engine INFO is not enabled by default); the request test sets the `httpx` logger to WARNING because the test client itself logs the full URL with query string (test-client noise, not the app).
- Invalid request-id test uses `bad-INJECT` (no raw newline, since the HTTP client rejects it); the invalid-UUID path is the same.
- loguru is forwarded via a level map (pyright flags `logging.getLevelName` as deprecated).
- Commit trailer uses Claude Sonnet 5.5 per session attribution instruction.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
Commit 6c341bb and the feat commit exist; full suite (18 passed), ruff, pyright 0 errors; worker stdout lines parse as JSON.
