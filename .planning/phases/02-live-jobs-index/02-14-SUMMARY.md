---
phase: 02-live-jobs-index
plan: 14
subsystem: ingest
tags: [greenhouse, lever, ashby, boards, discovery, scheduler, cli]
requires: [02-01, 02-13]
provides:
  - source_boards registry (migration 0005) with India-heavy seeds from the spike
  - Ashby, Greenhouse and Lever adapters (per-board scopes, absence expiry)
  - guarded discovery, weekly re-check, `ranker boards report|discover`, boards_recheck task
affects: [02-15, 02-16]
key-files:
  created:
    - backend/migrations/versions/0005_source_boards.py
    - backend/ranker/ingest/board_registry.py
    - backend/ranker/ingest/boards.py
    - backend/ranker/ingest/sources/boardbase.py
    - backend/ranker/ingest/sources/ashby.py
    - backend/ranker/ingest/sources/greenhouse.py
    - backend/ranker/ingest/sources/lever.py
    - backend/tests/fixtures/ashby_board.json
    - backend/tests/fixtures/greenhouse_board.json
    - backend/tests/fixtures/lever_postings.json
    - backend/tests/test_boards.py
    - backend/tests/test_sources_ashby.py
    - backend/tests/test_sources_greenhouse.py
    - backend/tests/test_sources_lever.py
  modified:
    - backend/config/sources.yaml
    - backend/ranker/ingest/sources/__init__.py
    - backend/ranker/cli.py
    - backend/ranker/tasks/handlers.py
    - backend/ranker/worker/scheduler.py
    - backend/tests/test_scheduler_jobs.py
    - backend/tests/test_ingest_task.py
requirements-completed: [ING-13]
completed: 2026-11-22
---

# Phase 2 Plan 14: Greenhouse, Lever and Ashby boards (ING-13)

Three board sources run through the shared pipeline with one scope per board, a `source_boards` registry seeded from the spike, guarded auto-discovery, and a weekly re-check.

## Commits
- 39509d0: source_boards registry, seeds, Ashby adapter
- ae84f0f: Greenhouse and Lever adapters
- 71adfec: discovery, recheck, CLI, task, schedule, import-cycle split

## Final seed list (generated from docs/decisions/0001-sources-spike.json, 2026-11-21)
- greenhouse (13): groww, gitlab, canonical, druva, turing, databricks, stripe, cloudflare, elastic, mongodb, datadog, airbnb, figma
- lever (5): cred, meesho, paytm, zeta, mindtickle
- ashby (8): ramp, notion, openai, linear, supabase, posthog, replit, sarvam
- `india_candidates` (37 slugs, greenhouse section) as in the plan. remotecom and every 404 slug are excluded; a test compares the YAML to the spike JSON.
- Schedules: greenhouse 22:00 + 10:00, lever 22:15 + 10:15, ashby 22:25 + 10:25 UTC (D-17 morning entry each; scheduler tests assert them).

## Ashby live field check (sarvam and ramp, one curl each, outside the suite)
All field names in the plan were confirmed (`jobs[]`: id, title, department, team, employmentType, location, secondaryLocations, publishedAt, isListed, isRemote, workplaceType, address.postalAddress, jobUrl, applyUrl, descriptionHtml/Plain, compensation.summaryComponents with compensationType/interval/currencyCode/minValue/maxValue). Differences or extras: `compensation` also carries `compensationTierSummary`, `scrapeableCompensationSalarySummary`, `compensationTiers`; `shouldDisplayCompensationOnJobPostings` and top-level `apiVersion` exist; `workplaceType` was present on every sarvam/ramp job; `addressCountry` is a name ("India", "USA") not an ISO code, so both "India" and "IN" map to IN. Equity components appear next to the Salary component with interval "NONE".

## Deviations from Plan

**1. [Rule 1 - Bug] Adapter fetches must not advance the zero-in-scope counter.** The plan has each fetch call `record_board_check`, which would drop a board after two empty daily runs (two runs a day means within a day), contradicting D-03's weekly cadence. Added `count_zero=False` to `record_board_check` for adapter calls; 404 still drops at once and the in-scope count and last_checked_at are still updated. Only the weekly recheck advances zero_checks. Tested.

**2. [Rule 3 - Blocking] Import cycle forced a module split.** `ranker.ingest.boards` imports `sources.base` (which runs `sources/__init__`, which imports the adapters, which imported boards). Fresh-interpreter imports (`python -m ranker.cli`) failed, caught by existing subprocess tests. Split into `board_registry.py` (pure registry, no sources import), `sources/boardbase.py` (config, count_in_scope, BoardAdapter) and `boards.py` (discovery, recheck, maintenance, re-exports of the registry API). Consequence: `sync_seeds(` appears once in boards.py (the call in run_board_maintenance); its definition lives in board_registry.py, so the plan's "≥ 2" grep is 1. Added a subprocess import test.

**3. Failed-scope string is `HTTPStatusError:500`** (codebase convention from 02-07, `type:status`), not the plan's `SourceHTTPError:500`.

**4. Discovery cap counts probes (candidate checks), not raw HTTP attempts.** A Lever candidate that 404s on the global host also tries the EU host, and tenacity retries 5xx (probe `max_attempts=3`); `calls_used` in ingest_runs records the real count. Candidate order: links found in job text, then India candidates (least recently checked first), then company-name guesses.

**5. Added a typed `number(value, maximum=...)` guard** for salary and epoch values (bool, NaN, negative and overflow-sized ints rejected). First version applied the 1e10 salary cap to epoch ms and nulled every Lever date; caught by the test and fixed (commit ae84f0f).

**6. Test issue found in the full run:** the `boards_recheck` handler test first monkeypatched after `build_handlers`, so the real maintenance ran once against the live ATS hosts from the test run (read-only probes, results only in the test DB). Fixed by patching before `build_handlers`. No live network remains in the suite.

## Known limitations
- A board dropped by a 404 or by two empty rechecks stops being a complete scope, so its already-stored jobs are no longer absence-expired. They would only be cleaned by a later purge. Not addressed in this plan.
- Company names for Lever and Ashby come from the slug (title-cased) since the payloads carry none; Greenhouse uses `company_name` when present.
- No local first run with real data was made, so there are no per-board in-scope counts beyond the spike estimates (greenhouse 326, lever 29, ashby 414 in scope).

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model. T-02-49 (slug regex plus DB CHECK plus host allow-list, traversal tests), T-02-50 (40 probes, 1 req/s, 30-day reprobe), T-02-51 (N=5 threshold), T-02-52 (entity-escaped flag, plain text preferred) and T-02-53 (per-board scopes) are implemented and tested.

## Verification
`uv run pytest -q`: 831 passed, 1 deselected (53 new tests across test_boards, test_sources_ashby/greenhouse/lever, scheduler and task tests); `ruff check`, `ruff format` and `pyright` clean.

## Self-Check: PASSED
