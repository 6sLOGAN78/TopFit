---
phase: 02-live-jobs-index
plan: 02
subsystem: worker, tasks queue, config
tags: [postgres, skip-locked, apscheduler, advisory-lock, yaml-config]
requires: []
provides:
  - tasks table and SKIP LOCKED queue (enqueue, claim, complete, fail, defer, reap_expired_leases, TaskDeferred)
  - consume_tasks loop and build_handlers registry (empty, filled by later plans)
  - leader-locked APScheduler in the worker (OPS-02), lease reaper job
  - typed YAML config loader and backend/config shipped in the image
  - conftest fixtures engine and clean_db
affects: [02-12, 02-13, ingest plans]
tech-stack:
  added: []
  patterns: [SQLAlchemy Core text() with bound params, no ORM models this phase]
key-files:
  created:
    - backend/ranker/config_loader.py
    - backend/config/README.md
    - backend/migrations/versions/0002_tasks.py
    - backend/ranker/tasks/{__init__,queue,consumer,handlers}.py
    - backend/ranker/worker/scheduler.py
    - backend/ranker/ingest/{__init__,normalize/__init__,sources/__init__}.py
    - backend/tests/{test_config_loader,test_tasks_queue,test_worker_leader}.py
  modified:
    - backend/ranker/worker/__main__.py
    - backend/ranker/settings.py
    - backend/Dockerfile
    - backend/tests/conftest.py
    - backend/tests/test_env_example.py
    - .env.example
key-decisions:
  - "No ORM models this phase: tables are owned by Alembic migrations and accessed via Core text(); migrations/env.py keeps target_metadata = None."
  - "Deferral decrements attempts (floored at 0) so transient LLM/budget outages never burn attempts."
  - "Task retry delay: min(2^attempts * 30 s, 6 h) plus up to 10% jitter from SystemRandom."
metrics:
  tasks: 3
  completed: 2026-11-22
---

# Phase 2 Plan 02: Tasks queue, leader-locked scheduler, config loader Summary

Postgres-only task queue with exclusive SKIP LOCKED claims, a worker that consumes tasks in every process but runs APScheduler in exactly one (pg advisory lock 727274), and a typed YAML config loader baked into the image.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Config loader, skeletons, fixtures | 61d5028 | config_loader, Dockerfile COPY config and CONFIG_DIR, engine/clean_db fixtures |
| 2 Tasks queue | 89aa581 | migration 0002, queue/consumer/handlers, 8 queue tests incl. 2 consumers over 100 tasks |
| 3 Worker consumer + scheduler | 59f4808 | try_acquire_leader, build_scheduler, reaper job, leader handover test |

## Verification

Full suite: 46 passed. ruff check, ruff format --check and pyright (whole project) clean. test_worker.py unchanged and passing (worker survives unreachable DB).

## Deviations from Plan

- [Rule 3] Added `ranker/tasks/__init__.py` (not in the file list) so the package is a regular package.
- [Rule 3] `# pyright: ignore[reportMissingTypeStubs]` on the apscheduler import (it ships no stubs); consistent with the strict-pyright rule.
- Task 1 committed the TASK_POLL_SECONDS / TASK_LEASE_SECONDS lines in `.env.example` early (Task 2 added the Settings fields); the env-example test passes at each commit boundary.
- Enqueue takes `delay_seconds` instead of an absolute `run_after` (simpler SQL, no clock skew between app and DB).

## Known Stubs

`build_handlers` returns an empty mapping by design; plans 02-12 and 02-13 register handlers.

## Self-Check: PASSED
