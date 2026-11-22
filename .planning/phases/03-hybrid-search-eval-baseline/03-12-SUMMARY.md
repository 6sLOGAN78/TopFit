---
phase: 03-hybrid-search-eval-baseline
plan: 12
subsystem: search-ops
tags: [benchmark, pgvector, hnsw, k6, adr, deploy]
requires: [03-03, 03-04]
provides:
  - ranker.spike.bench_clone and bench_explain (VM benchmark tooling) plus bench/ k6 runbook
  - docs/decisions/0004-vector-search-mode.md
  - ranking.yaml retrieval.set_aside_when_results_below
key-files:
  created:
    - backend/ranker/spike/bench_clone.py
    - backend/ranker/spike/bench_explain.py
    - backend/tests/test_bench_clone.py
    - bench/README.md
    - bench/search.js
    - bench/compose.bench.yaml
    - bench/queries.txt
    - docs/decisions/0004-vector-search-mode.md
    - .planning/phases/03-hybrid-search-eval-baseline/bench/ (raw outputs)
  modified:
    - backend/config/ranking.yaml
    - backend/ranker/search/config.py
    - backend/ranker/search/service.py
    - backend/tests/test_search_setaside.py
    - deploy/compose.prod.yaml
decisions:
  - vector_mode stays exact; HNSW is never chosen by the planner while the inner ORDER BY has the j.id tie-breaker.
  - Set-aside/relax counts are computed only when fewer than 20 candidates were fused (config key, 0 = always).
  - db service gets shm_size 256mb; applied on next container recreate, server not touched.
  - SQL rewrites (drop inner tie-breaker, GIN-driven lexical leg) are follow-ups for a gap-closure plan.
metrics:
  tasks: 3
  completed: 2026-11-23
---

# Phase 3 Plan 12: Real deploy, S1/S3 benchmark spikes and ADR 0004

## Commits
- ebfc141 feat(03-12): bench tooling for spikes S1 and S3 (Task 1)
- c287fdf docs(03-12): raw VM benchmark outputs (orchestrator, on main)
- 0265d02 feat(03-12): ADR 0004, conditional set-aside, db shm_size (Task 3)

## Task 2 (deploy, UAT, benchmark; run by the orchestrator on the prod VM, Azure B2ls_v2)
- CI deployed sha 6cd2673; all CI jobs green (deploy, schemathesis, eval-gate). `alembic current` is 0007 (head), readyz ok, /api/docs 200.
- Prod search "backend python bengaluru 10 LPA remote ok": 20 items, degraded=false, chips `City: Bengaluru + remote` and `₹10 LPA+`.
- Owner UAT, verbatim: "UAT ok".
- CPU credit balance before/after: **not captured** (owner gave no readings).
- Clone: 1838 jobs x25 = 45,950 rows, 919 MB, 108 s. First attempt failed with DiskFull on /dev/shm (64 MB Docker default) during parallel HNSW build; rerun with `max_parallel_maintenance_workers=0`, `maintenance_work_mem=256MB`.
- S1 (30 runs x 5 queries, full search p50/p95 ms): exact 1395/2799 (no filter), 1064/2058 (city), 987/1753 (3 filters); hnsw and hnsw+index within noise of exact. Vector and lexical legs are Seq Scans in every mode; HNSW and the GIN indexes are never used. Set-aside costs about 800-900 ms p50 on every filtered search.
- S3 (k6 20 rps x 5 min, cold, 1 worker): p95 51.68 s, 71% failed, 894 completed, 5094 dropped, db CPU 193%. All thresholds failed.
- bench db dropped afterwards; prod readyz ok.

## Task 3 outcome
- ADR 0004 written with both tables, plans, decision and ranked follow-ups (status accepted; SLO miss stated plainly).
- `set_aside_when_results_below: 20` added (Retrieval field default 0, ranking.yaml sets 20). In `search()` the set-aside query runs only if filters are active and `len(fused) < 20`. This changes `pipeline_version`.
- `shm_size: 256mb` added to the db service in `deploy/compose.prod.yaml`.
- Tests: new `test_set_aside_skipped_when_pool_is_full`; the geo test now sets the threshold to 0 via a helper, since its 20-result pool now skips set-aside by design.

## Open gaps (for verify-work)
- **SRCH-06 is NOT met** at production-size data: p95 1.7-2.8 s for a single search at 46k rows and k6 collapse at 20 rps. At today's 1,838 rows prod is fine. The post-change latency has not been re-benchmarked.
- SRCH-03 is satisfied (benchmark and decision recorded). OPS-03 and UI-01 evidence (deploy, UAT) is in Task 2 above.
- CPU credit readings missing, so burst throttling versus query cost is not separated.
- Gap-closure follow-ups: drop `j.id` from the inner vector ORDER BY (tie-break outside) and re-bench HNSW; GIN-driven lexical leg with LIMIT; recreate db container to apply shm_size; act before about 5,000 active jobs or when prod p95 exceeds 300 ms for an hour; re-run S3 with credit readings.

## Deviations from Plan
- [Rule 2/3] Conditional set-aside was implemented (not just listed) at the orchestrator's request; `service.py`, `config.py` and its test were added to Task 3's files. Product side effect: a full results page no longer reports set-aside/relax counts.
- Commit trailer uses `Claude Sonnet 5.5` (the attribution configured for this session) instead of the `Claude Opus 5.5` named in the task brief, so it does not misstate the author model.
- ADR filename is `0004-vector-search-mode.md` as in the plan; the brief's `0004-*` wildcard matched it.
- The post-change numbers in the ADR are expectations from arithmetic on the measured legs, labelled as such.

## Verification
- uv run pytest -q: 1132 passed, 2 deselected
- ruff check, ruff format --check, pyright: clean

## Known Stubs
None.

## Self-Check: PASSED
- docs/decisions/0004-vector-search-mode.md, deploy/compose.prod.yaml change, and commits ebfc141, c287fdf, 0265d02 exist.
