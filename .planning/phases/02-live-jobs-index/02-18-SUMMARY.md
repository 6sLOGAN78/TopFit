---
phase: 02-live-jobs-index
plan: 18
subsystem: ingest, api, db
tags: [gap-closure, expiry, memory, readyz, security, config, fx]
gap_closure: true
requires: [02-14, 02-16]
provides:
  - expire_dropped_board / expire_stale_board_jobs (board-source expiry)
  - per-board bounded ingest (BoardAdapter.set_scope_flush)
  - startup embedding self-check + cheap readyz
  - hide_parameters outside development
  - board company_names, Jobicy geo validation
  - ECB-sourced fx.yaml
affects: [ingest pipeline, board maintenance, /api/readyz, SQLAlchemy engines, job cards, salary conversion]
tech-stack:
  added: []
  patterns:
    - optional adapter hook discovered with getattr (scope flush)
    - startup self-check cached on app.state
key-files:
  created:
    - backend/tests/test_board_expiry.py
    - backend/tests/test_ingest_memory.py
    - backend/tests/test_db_engine.py
  modified:
    - backend/ranker/ingest/expiry.py
    - backend/ranker/ingest/pipeline.py
    - backend/ranker/ingest/boards.py
    - backend/ranker/ingest/sources/boardbase.py
    - backend/ranker/ingest/sources/greenhouse.py
    - backend/ranker/ingest/sources/lever.py
    - backend/ranker/ingest/sources/ashby.py
    - backend/ranker/ingest/sources/jobicy.py
    - backend/ranker/ingest/sources/internshala.py
    - backend/ranker/api/health.py
    - backend/ranker/main.py
    - backend/ranker/db/engine.py
    - backend/migrations/env.py
    - backend/config/sources.yaml
    - backend/config/fx.yaml
    - backend/tests/test_readyz.py
    - backend/tests/test_sources_{greenhouse,lever,ashby,free_apis,internshala}.py
decisions:
  - "Board age fallback only for boards with a successful check in the last 48 h, and only after a succeeded run passed the source-level volume guard"
  - "readyz has no background embedding refresh: the ONNX model is immutable after load"
  - "hide_parameters on whenever app_env != development (test too, so the suite runs the production path); Alembic always"
  - "Internshala source_refused note keeps the first refusal (later scopes only echo host_refused)"
metrics:
  completed: 2026-11-22
  tasks: 5
  commits: 5
  tests: "952 passed, 2 deselected (llm)"
---

# Phase 2 Plan 18: Gap Closure Summary

This plan closes six gaps. Dropped boards' jobs now expire, and stale board jobs expire after 7 days but only when the board has answered OK recently. Board ingest holds one board in memory at a time. `/api/readyz` uses a cached embedding check from startup. Bound SQL values are hidden outside development. Lever and Ashby jobs show configured company names. Jobicy only accepts the documented geo values. FX rates come from the ECB with their source recorded.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 30d38c0 | Expire on drop + guarded board age fallback |
| 2 | d63734b | Bound board-ingest memory to one board; Internshala `source_refused` note |
| 3 | 4e1ae85 | Startup embedding self-check, cheap readyz, hide_parameters |
| 4 | 14bc8e6 | Board display names, Jobicy geo/count validation |
| 5 | 693d317 | fx.yaml from ECB reference rates |

## 1. Board expiry (ING-08, T-02-72)

New in `ranker/ingest/expiry.py`:

- `ExpiryConfig.board_unseen_days: int = Field(default=7, ge=1, le=365)`, `BOARD_HEALTHY_HOURS = 48`.
- `async def expire_dropped_board(conn, *, source, slug, run_id) -> int`: one UPDATE of the active jobs of `(source, source_scope=slug)`. It only runs when `source_boards` has `(ats=source, slug, status='dropped')`.
- `async def expire_stale_board_jobs(conn, *, source, run_id, now, cfg) -> int`: one UPDATE of the active jobs of `source` where either:
  - the board is `dropped`; or
  - `last_seen_at < now - board_unseen_days` AND the board is `seed`/`active` with `last_checked_at >= now - 48 h`.
  - It never touches rejected boards or scopes that have no registry row. It uses bound parameters only.

Wiring:

- In `pipeline._expire`, the absence branch now runs `expire_stale_board_jobs` for `source in ALL_ATS` after `expire_absent_after_run` and records `notes.guard.board_fallback`. This code is only reached after a run succeeded and passed the source-level volume guard. A failed run, a cold_start or a below_50pct run expires nothing.
- `boards.run_board_maintenance` expires each newly dropped board with the `board_discovery` run id and records `notes.expired_on_drop`. If the daily fetch sees a 404, there is no run id; the pipeline fallback catches that board on the next succeeded run.
- In `sources.yaml`, the greenhouse, lever and ashby sections have `board_unseen_days: 7` and the expiry-thresholds comment is extended. Schedules are unchanged.
- Every expiry records `expired_by_run_id`, so `reactivate_run` undoes it. The tests check this for both paths.

## 2. Per-board memory contract (T-02-75)

- `BoardAdapter.set_scope_flush(hook)` is an optional hook. `pipeline._run` finds it with `getattr` and registers its local `flush()`. The `SourceAdapter` protocol is unchanged.
- `fetch_boards` runs these steps for each board, all before the next sleep and request:
  1. Fetch the payload.
  2. Parse it, then `del payload`.
  3. Build `unique`, then `del jobs`.
  4. Record the board check.
  5. Yield the jobs, with `del job` after each yield.
  6. `unique.clear()` and `del unique`.
  7. `await self._scope_flush()`, which normalises and upserts the buffered postings.
- Failed and invalid payloads are released on the `continue` paths.
- Loop variables held the last posting of the previous board in three places: the pipeline's `raw`, the boardbase yield loop, and each adapter's `fetch` wrapper (`async for job in self.fetch_boards(): yield job`). All three are now deleted after use.
- Large boards are still split into `store.BATCH_SIZE` (300) buffers. A test checks that a 350-posting board flushes as 300 then 50.
- Embedding is still one `embed_missing(..., batch_size=32)` pass after the fetch loop: keyset pages of 32 texts with a 1,500-char cap. The memory contract is written up in a comment. Embedding per board would only add model calls.
- Tests (`test_ingest_memory.py`, 3 boards × 300 synthetic postings of about 8 KB each):
  - When each new board's request starts, there are 0 live RawJobs from earlier boards and 0 live payload weakrefs.
  - The call order is `fetch:b1, upsert:b1, fetch:b2, ...`, using 120 postings per board so that only the hook can flush.
  - The embedder never receives more than 32 texts per call.
  - The tracemalloc peak for 3 boards is at most 1.5× the peak for 1 board.
- RED check: I ran these tests against the old code. The gc/weakref test failed with 300 live earlier RawJobs and 1 live payload per board fetch, and the call-order test also failed. **The tracemalloc ratio test also passed on the old code.** Overhead from normalise and upsert dominates the synthetic peak, so that test is a tolerant regression guard, not proof. The proof is the gc/weakref and call-order tests.
- I did not take the optional local max-RSS measurement (`/usr/bin/time -v ranker ingest greenhouse`), because the task rules forbid live network calls except the single ECB fetch. **Production memory must be re-measured at the next deploy** (scheduled Greenhouse run in the 1 GiB worker).
- Internshala: on FetchRefused or RobotsDisallowed, the run notes now carry `extra_notes["source_refused"] = {"reason": <first refusal>}`. The log line stays. There is no retry or bypass (D-15).

## 3. readyz (CR-01, T-02-70) and hidden parameters (WR-04, T-02-71)

- `embedding_selfcheck(embedder)` runs once in lifespan. It uses `anyio.to_thread.run_sync(..., abandon_on_cancel=True)` inside `fail_after(30)`, logs only `error_type`, and the result is stored in `app.state.embedding_check`.
- `readyz` runs `SELECT 1` and the pgvector version check under a 3 s timeout, plus `model.ok = embedder is not None` and the cached embedding check. There is no inference on the request path. `EMBED_DIM` replaces the literal 384.
- **No background refresh**, by choice: the ONNX model is immutable once loaded, so a refresh would only add idle CPU load. The module docstring says this.
- The response shape is unchanged, so the `smoke.sh` jq checks still hold. A test shows exactly 1 embed call across startup plus 5 probes. When the self-check fails, readyz returns 503 with `embedding == {"ok": False, "dim": 0}`, and the exception message does not appear in the logs or the body.
- `create_engine_from_settings` passes `hide_parameters=settings.app_env != "development"`, so parameters are hidden in production and test and visible in development. `migrations/env.py` always passes `hide_parameters=True`. A test shows the bound value is absent from the DBAPIError text in test and present in development.

## 4. Board display names (T-02-74) and Jobicy

- `BoardsConfig.company_names` is sanitised:
  - keys must pass `valid_slug`; a bad key logs a warning that contains no data;
  - values are stripped; empty values, values over 80 chars and values containing control characters are dropped.
- `board_company_name(slug, names)` is a new helper.
- Lever and Ashby use `company_name or company_from_slug(slug)`. Greenhouse uses the payload `company_name`, then the configured name, then the slug. The new `company_name` argument is keyword-only and defaults to `None`, so discovery's `parse_board(payload, slug)` call still works.
- New `company_names` entries in `sources.yaml`:
  - greenhouse: groww Groww, gitlab GitLab, canonical Canonical, druva Druva, turing Turing, databricks Databricks, stripe Stripe, cloudflare Cloudflare, elastic Elastic, mongodb MongoDB, datadog Datadog, airbnb Airbnb, figma Figma
  - lever: cred CRED, meesho Meesho, paytm Paytm, zeta Zeta, mindtickle MindTickle
  - ashby: ramp Ramp, notion Notion, openai OpenAI, linear Linear, supabase Supabase, posthog PostHog, replit Replit, sarvam Sarvam AI
- **One-time effect:** the content hash includes `company_name`. On the next run, Lever and Ashby jobs whose name changes (for example Cred → CRED, Sarvam → Sarvam AI, Openai → OpenAI, Posthog → PostHog, Mindtickle → MindTickle) will hash differently and be re-normalised once. Greenhouse jobs whose payload has no `company_name` are affected the same way.
- Jobicy:
  - `geo: Literal["apac","usa","canada","europe"] | None = None` and `count: int = Field(50, ge=1, le=100)`.
  - The docstring records what the docs said on 2026-11-22 (https://jobicy.com/jobs-rss-feed): the parameters are count, cursor, geo, industry and tag. There is no India slug, which explains the 400. `apac` would drop worldwide-remote postings, so the feed stays unfiltered.
  - The docs now name `/api/jobs` (cursor-paginated). The adapter keeps `/api/v2/remote-jobs`; switching is a follow-up.
  - The `sources.yaml` comment is updated to match.

## 5. FX outcome (T-02-73)

- I made one fetch of `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`. The ECB reference date is **2026-11-20**.
- I parsed it with the linear regexes in `python3 -I`, with no XML entity expansion. Every new rate passed the ±15% gate; the largest drift was AUD at 6.5%.
- Rates (INR per unit):

| Currency | Before (rounded, unconfirmed) | After (ECB) |
|----------|------------------------------|-------------|
| USD | 88.5 | 92.68 |
| EUR | 102.5 | 101.85 |
| GBP | 116.0 | 121.63 |
| SGD | 68.5 | 71.93 |
| AUD | 58.0 | 61.78 |
| CAD | 63.5 | 64.78 |
| AED | 24.1 | 25.23 (USD / 3.6725 peg) |

- `as_of` is 2026-11-22. The header records the source URL, the reference date, the cross method and "refreshed by hand per D-12; no runtime fetching". The "NOT re-fetched" wording is removed.
- `test_ingest_eval.py` still passes. `backend/ranker` contains no `eurofxref` reference.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Adapter `fetch` wrappers pinned the previous board's last posting**
- **Found during:** Task 2. The gc test found 1 live earlier RawJob, referenced by the `async for job in self.fetch_boards(): yield job` frame.
- **Fix:** added `del job` after the yield in `greenhouse.py`, `lever.py` and `ashby.py`. These files were not in the Task 2 file list. Task 4 also edits them.
- **Commit:** d63734b

**2. [Rule 1 - Bug] Pipeline loop variable `raw` pinned the previous board's last posting**
- **Fix:** added `del raw` after `buffer.append(raw)` in `pipeline._run`.
- **Commit:** d63734b

**3. Internshala `source_refused` keeps the first refusal**
- After the first refusal, later scopes report `host_refused`. A plain assignment would have overwritten the real cause, for example `FetchRefused:403`. I used `setdefault` so the first refusal is kept. The test asserts `{"reason": "FetchRefused:403"}`.

**4. Worktree base**
- The worktree branch started at 6740c86, before the plan commit. I fast-forwarded it to fd4e9b9 before starting.

### Not done (optional)
- Local max-RSS before/after measurement. It needs live Greenhouse calls, which this run's rules forbid. Production must be re-measured at the next deploy.

## Known Stubs

None.

## Verification

- `uv run pytest -q`: 952 passed, 2 deselected (`llm` marker).
- `ruff check .`, `ruff format --check .` and `pyright` (0 errors) are all clean.
- Nothing touched `docs/decisions/0001-sources.md`, the ingest-eval fixtures, `deploy/`, STATE.md, ROADMAP.md, Phase 3, or the `ranker/llm/` files.

## Self-Check: PASSED

- The new test files and SUMMARY exist.
- Commits 30d38c0, d63734b, 4e1ae85, 14bc8e6 and 693d317 are present on the branch.
