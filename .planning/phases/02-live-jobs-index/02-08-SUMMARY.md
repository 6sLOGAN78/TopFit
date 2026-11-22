---
phase: 02-live-jobs-index
plan: 08
subsystem: ingest
tags: [pipeline, upsert, normalization, cli, remotive]
requires: [02-04, 02-05, 02-06, 02-07]
provides:
  - normalize_job composing every rule module
  - source-level content_hash and dedupe_key
  - idempotent PG18 RETURNING old/new upsert store
  - run lifecycle (lock, runs-per-day cap, ingest_runs stats)
  - "ranker ingest <source>" CLI
affects: [02-09, 02-10, 02-11]
key-files:
  created:
    - backend/ranker/ingest/normalize/job.py
    - backend/ranker/ingest/hashing.py
    - backend/ranker/ingest/store.py
    - backend/ranker/ingest/pipeline.py
    - backend/ranker/cli.py
    - backend/tests/test_normalize_job.py
    - backend/tests/test_pipeline_remotive.py
    - backend/tests/test_cli_ingest.py
requirements-completed: [ING-01, ING-05, ING-06, ING-11, NORM-01, NORM-07]
completed: 2026-11-22
---

# Phase 2 Plan 08: Remotive end-to-end ingest

`ranker ingest remotive` fetches the feed, scope-filters, normalises (all D-19 fields), hashes source-level content, upserts idempotently into `jobs`/`companies` and writes a complete `ingest_runs` row.

## Commits
- fb44b2c Task 1: normalize_job, hashing, test_normalize_job, RED test_pipeline_remotive (RED failure: `ImportError: cannot import name 'pipeline' from 'ranker.ingest'`)
- 306e707 Task 2: store, pipeline, CLI, CLI tests (GREEN)

## Upsert form chosen
`INSERT ... SELECT FROM jsonb_to_recordset(CAST(:rows AS jsonb))` (one JSON bound parameter, <= 300 rows per statement). `RETURNING new.id, (old.id IS NULL) AS inserted, (old.id IS NOT NULL AND (hash or normalizer_version differs)) AS changed` works on PG18.7 (verified by the testcontainers run). Every content column uses CASE-on-(hash OR normalizer_version); `embedding`/`embedding_model` are NULLed on change; `first_seen_run_id` is set only on insert; expired rows reactivate.

## Public signatures
- `ranker.ingest.normalize.job`: `NORMALIZER_VERSION = "1"`, `NormalizedJob`, `ScopeSkip(reason, title)`, `normalize_job(raw: RawJob) -> NormalizedJob | ScopeSkip`
- `ranker.ingest.hashing`: `content_hash(raw: RawJob) -> str`, `dedupe_key(company_normalized, title, seniority, city_or_remote) -> str`
- `ranker.ingest.store`: `BATCH_SIZE=300`, `create_run(conn, source, mode, now) -> int`, `finish_run(conn, run_id, *, status, fetched, inserted, updated, unchanged, skipped, failed, calls_used, in_scope, errors, notes, now)`, `runs_started_since(conn, source, since) -> int`, `prefetch_versions(conn, source, ids) -> dict[str, tuple[str, str]]`, `touch_unchanged(conn, run_id, source, ids, now) -> int`, `upsert_companies(conn, rows: Sequence[tuple[name, normalized_name, is_placeholder]]) -> dict[str, int]`, `upsert_jobs(conn, run_id, rows: Sequence[NormalizedJob], company_ids: dict[str, int], now) -> tuple[int, int]`
- `ranker.ingest.pipeline`: `run_ingest(engine, settings, source, *, mode="incremental", adapter=None) -> RunSummary`; `RunSummary(source, run_id, status, fetched, inserted, updated, unchanged, skipped, failed, calls_used)`; `SourceLocked`, `RunQuotaReached`, `SourceNotConfigured`; `UnknownSource` propagates from the registry.
- `ranker.cli`: typer `app` (with a callback so `ingest` stays a subcommand), exit codes 0 / 1 / 2 / 3 as specified.

## Deviations from Plan

**1. [Plan vs. reality] Rerun calls normalize_job for out-of-scope items.** Out-of-scope jobs are not stored (ADR 0002), so nothing remembers them and they are re-evaluated each run. The test asserts that normalize_job is called 0 times for stored jobs and only for the 2 out-of-scope ids; stored-job normalisation is skipped as required.

**2. [Rule 2 - correctness] Run status refinement.** When the adapter yielded nothing and no scope completed (e.g. Remotive 5xx after retries), the run is marked `failed` rather than `partial`, so the CLI exits 1 for a total outage. `partial` remains for a run with items plus failed scopes, or an adapter error mid-stream.

**3. Buffer atomicity.** Each 300-row buffer runs in one transaction; if it fails the whole buffer counts as `failed` (one redacted error) and counters from that buffer are discarded, so counts never claim touched rows that rolled back.

**4. Typer callback.** A single-command Typer app collapses the subcommand; an empty callback keeps `ranker ingest ...` working.

## Verification
Full suite 691 passed; `ruff check`, `ruff format --check` and `pyright` clean. No live network or LLM calls.

## Known Stubs
None. Embedding stays NULL until plan 02-09, expiry arrives in 02-11 (by design).

## Self-Check: PASSED
