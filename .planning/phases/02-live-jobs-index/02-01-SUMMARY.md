---
phase: 02-live-jobs-index
plan: 01
subsystem: ingestion
tags: [spike, sources, adzuna, scraping, adr]
requires: []
provides:
  - ranker.spike.sources CLI (python -m ranker.spike.sources)
  - docs/decisions/0001-sources.md with marker lines for plans 02-10, 02-14, 02-15
affects: [02-10, 02-14, 02-15]
tech-stack:
  added: [anthropic, nh3, tenacity, typer, pyyaml, respx, time-machine, hypothesis]
  patterns: [robots-aware polite fetching, redacted error tags]
key-files:
  created:
    - backend/ranker/spike/sources.py
    - backend/ranker/spike/extra.py
    - backend/ranker/spike/common.py
    - backend/tests/test_spike_sources.py
    - backend/tests/test_spike_extra.py
    - docs/decisions/0001-sources.md
    - docs/decisions/0001-sources-spike.json
  modified: [backend/pyproject.toml, backend/uv.lock]
key-decisions:
  - "Active-job target: 1500 (10,000 unreachable without Adzuna)"
  - "ING-13 decision: pull-in (forced by D-14)"
  - "Adzuna calls/day budget: 0 (pending activation, 25 test calls; adapter ships, schedule disabled)"
  - "Adzuna results_per_page 50, category it-jobs, salary period year"
  - "Naukri, LinkedIn guest, Wellfound, Indeed: blocked, not used"
metrics:
  completed: 2026-11-22
---

# Phase 2 Plan 01: Source spike and ADR 0001 Summary

Live ING-00 spike across Adzuna, Greenhouse/Lever/Ashby, Remotive, four free remote APIs, HN, Internshala and four blocked-site probes, recorded in ADR 0001 with target 1500, Adzuna budget 0 and ING-13 pull-in.

## Commits
- 6ebda97: spike CLI (Task 1, earlier)
- 5cc8cee: package batch, llm marker, `ranker` script entry
- 26442f5: spike extension (Ashby, free APIs, HN, Internshala, probes) and tests
- 88bfc92: ADR 0001 and redacted spike JSON

## Measured numbers
- Remotive 16 total / 6 in scope. Adzuna: 102,143 total, it-jobs 4,019 (1 day) / 12,295 (7) / 35,034 (30); page cap 50, deep page 40 works; snippets only (no full_description); salaries annual. 9 of 25 test calls used.
- Boards (61 slugs): Greenhouse 14 of 42 live (4,340 jobs, 326 in scope), Lever 5 of 10 (310 / 29), Ashby 8 of 9 (1,410 / 414). In-scope is an upper bound (remote text counts as India-eligible).
- Arbeitnow 1,500 (32 in scope and eligible), Himalayas 120,098 (0 of 20 sampled India-eligible), Jobicy 100 (geo filter returned 400), RemoteOK 100 (7).
- HN 2026-11 thread: 242 top-level comments, 142 mention remote or an India city.
- Internshala: 50 cards per page, jobs listed 159/132/189/244/56 across five categories, internships 3-8 pages each.

## Deviations from Plan
1. [Rule 2 - Scope extension per orchestrator] Spike extended for D-14..D-20 sources (new modules `extra.py`, `common.py`; helpers renamed public so strict pyright passes across modules).
2. [Rule 3 - Blocking] Installing anthropic pulled in `httpx2`, whose logger leaked a query string into `test_logging_redaction`; silenced the `httpx2` logger in that test and in the spike.
3. `pgvector` Python package not added (embeddings written via `CAST(:v AS vector)`), as the plan states.
4. Spike client User-Agent changed to the honest RankerBot UA (D-15).
5. Spike heuristics: Internship card counts run 50-57 because promoted blocks match the card marker; Internshala internship totals not parsed (page-link count used).

## Auth gates
Adzuna keys, terms (terms-ok) and package verdict (packages-ok) were resolved at the owner checkpoint; no secret appears in the JSON or ADR (grep-checked).

## Known Stubs
None. Jobicy `geo=india` returned 400 and was not retried; revisit in the adapter plan.

## Self-Check: PASSED
Files and commits above exist; 50 tests pass, ruff and pyright clean.
