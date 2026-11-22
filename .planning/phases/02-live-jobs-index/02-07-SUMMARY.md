---
phase: 02-live-jobs-index
plan: 07
subsystem: ingest
tags: [migration, pgvector, source-adapter, tenacity, remotive]
requires: [02-01, 02-02, 02-03, 02-04, 02-05]
provides:
  - jobs-index schema (migration 0004)
  - SourceAdapter plugin contract and registry
  - retrying HTTP client with per-attempt callback
  - Remotive adapter and fixture
affects: [02-08, 02-09, 02-10, 02-11]
key-files:
  created:
    - backend/migrations/versions/0004_jobs_index.py
    - backend/ranker/ingest/sources/base.py
    - backend/ranker/ingest/sources/remotive.py
    - backend/ranker/ingest/http.py
    - backend/config/sources.yaml
    - backend/tests/fixtures/remotive_jobs.json
    - backend/tests/test_migration_jobs_index.py
    - backend/tests/test_ingest_http.py
    - backend/tests/test_sources_remotive.py
  modified:
    - backend/ranker/ingest/sources/__init__.py
requirements-completed: [ING-01, ING-04, ING-07]
completed: 2026-11-22
---

# Phase 2 Plan 07: Jobs index schema, source contract, retrying HTTP, Remotive adapter

Migration 0004 (companies, jobs with vector(384)/generated tsv/partial HNSW/D-18/D-19 columns, ingest_runs with D-20 counters), a registry-based SourceAdapter contract with no source CHECK, a tenacity HTTP client honouring Retry-After with per-attempt counting, and a Remotive adapter.

## Commits
- 8436c59 Task 1: migration, contract, sources.yaml, fixture
- Task 2 commit: HTTP client + Remotive adapter (see git log)

## Public signatures
- `ranker.ingest.sources.base`: `RawJob` (pydantic; tz-aware `posted_at`), `FetchResult` dataclass (complete_scopes, failed_scopes, calls_used, scope_counts, invalid_items, extra_notes), `CronSpec`, `BaseSourceConfig` (extra=allow), `SourcesConfig` (RootModel; use `.root`), `AdapterContext(settings, engine, config, mode, now)`, `SourceAdapter` Protocol (`name`, `expiry_policy`, `configured()`, `fetch() -> AsyncIterator[RawJob]`, `result()`), `register(name, factory)`, `get_adapter_factory(name)`, `registered_sources() -> list[str]`, `UnknownSource`. Factory type: `Callable[[AdapterContext], SourceAdapter]`.
- `ranker.ingest.http`: `USER_AGENT`, `build_client(*, timeout=20.0) -> httpx.AsyncClient`, `async get_json(client, url, *, params=None, max_attempts=5, sleep=asyncio.sleep, on_attempt: Callable[[], None] | None = None) -> object`, `redact_url(url)`, `SourceHTTPError(status, error_type, retry_after)` with `str` = `error_type:status`.
- Remotive failed scope string: `HTTPStatusError:500` (type:status).

## Fixture counts
9 payload items, 8 unique ids (1003 duplicated, last wins and its title ends "(updated)"): 6 in-scope tech (1001-1006), 2 out-of-scope (1007 Sales, 1008 Customer Support).

## Deviations
None of substance. The CHECK value sets in the migration are hard-coded (migrations stay self-contained); tests assert equality with the Literals. Retry-After accepts numeric seconds only (HTTP-date ignored, non-finite/negative ignored, capped 120).

## Verification
Full suite: 668 passed; ruff and pyright clean; migration downgrade 0003 / upgrade head round trip passes.

## Self-Check: PASSED
