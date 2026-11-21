---
phase: 01-foundations-walking-skeleton-deploy
plan: 01
subsystem: api
tags: [fastapi, uv, alembic, pgvector, fastembed, testcontainers, itsdangerous]

requires: []
provides:
  - uv-managed `ranker` backend package (Python 3.14, locked deps)
  - /api/healthz, /api/readyz, /api/session/ping
  - Embedder abstraction (FastEmbedEmbedder, FakeEmbedder, build_embedder)
  - Alembic async baseline migration 0001 (CREATE EXTENSION vector)
  - Stub worker (python -m ranker.worker) with SIGTERM/SIGINT clean exit
  - pytest suite on real pgvector 0.8.7 / PG18 container
affects: [01-02, 01-03, 01-04, 01-05, 01-06]

tech-stack:
  added: [fastapi 0.165, sqlalchemy[asyncio] 2.1, psycopg 3.3, alembic 1.20, fastembed 0.9, structlog, itsdangerous, apscheduler<4, pytest 9, testcontainers, ruff 0.17.6, pyright 1.1.414]
  patterns: [create_app factory + lifespan state, settings via lru_cache get_settings, lazy fastembed import, sync session fixture running alembic]

key-files:
  created:
    - backend/pyproject.toml
    - backend/uv.lock
    - backend/alembic.ini
    - backend/migrations/env.py
    - backend/migrations/versions/0001_baseline_vector.py
    - backend/ranker/main.py
    - backend/ranker/settings.py
    - backend/ranker/api/health.py
    - backend/ranker/api/session.py
    - backend/ranker/embeddings/embedder.py
    - backend/ranker/worker/__main__.py
    - backend/tests/conftest.py
  modified: []

key-decisions:
  - "uv 0.9.18 could not self-update (package-manager install), so build backend is uv_build>=0.9.18,<0.10 instead of 0.13.x"
  - "alembic.ini keeps script_location = %(here)s/migrations (generated default) so it resolves regardless of cwd"
  - "Scoped pytest filterwarnings ignore for starlette's httpx->httpx2 deprecation warning instead of swapping packages"

patterns-established:
  - "Importing ranker.main never reads settings; settings are read in lifespan"
  - "readyz/health errors expose only type(exc).__name__"

requirements-completed: [FND-02, FND-05, OPS-04]

duration: ~25min
completed: 2026-11-21
---

# Phase 1 Plan 01: Backend Walking Skeleton Summary

Pinned uv/Python 3.14 FastAPI backend serving /api/healthz, a real readyz (pgvector extversion >= 0.8.3 + 384-d embedding probe), a signed rk_ping cookie round-trip, an Alembic vector baseline, and a SIGTERM-clean stub worker, all tested against a real pgvector PG18 container.

## Tasks
| Task | Commit | Notes |
|------|--------|-------|
| 1 RED: scaffold, migration, failing tests | 7add299 | suite failed on missing ranker.main |
| 2 GREEN: API slice | eedc1a0 | 8 tests, ruff, pyright clean |
| 3 Worker stub | 9632c97 | SIGTERM and SIGINT tests, 10 tests total pass |

## Deviations from Plan

**1. [Rule 3 - Blocking] starlette DeprecationWarning under filterwarnings=error**
- Starlette 1.x warns that `httpx` with `starlette.testclient` is deprecated (wants `httpx2`). Added a narrowly scoped ignore in pyproject with a comment, as the plan permits; did not swap packages.

**2. [Rule 1 - Lint] pyright reportDeprecated on `AsyncIterator` with `@asynccontextmanager`** - used `AsyncGenerator[None]` in lifespan.

**3. uv_build range** - `>=0.9.18,<0.10` because local uv could not self-update (documented in key-decisions).

**4. Test cleanup** - worker test closes the subprocess stdout pipe to avoid an unraisable ResourceWarning under `filterwarnings=error`.

Commit trailer uses `Claude Sonnet 5.5` per the session attribution instruction (plan text says Opus 5.5).

## Known Stubs
None blocking. `ranker/services`, `llm`, `tasks` are intentional docstring-only anchors for later phases; worker has a Phase 2 marker comment.

## Threat Flags
None beyond the plan's threat model (T-01-01..05 mitigated and tested; readyz test asserts DB password absent).

## Self-Check: PASSED
Commits 7add299, eedc1a0, 9632c97 exist; `uv run pytest` (10 passed), ruff check/format and pyright all clean.
