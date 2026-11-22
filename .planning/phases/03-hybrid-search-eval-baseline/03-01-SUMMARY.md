---
phase: 03-hybrid-search-eval-baseline
plan: 01
subsystem: search
tags: [postgres, pgvector, fts, rrf, fastapi]
requires: []
provides:
  - migration 0006 (jobs.skills_tsv + GIN, search_logs)
  - ranker.search package (models, config, sql, service)
  - GET /api/jobs/search, GET /api/meta
  - seeded-job test fixture (tests/fixtures/search/jobs_seed.py)
affects: [03-02, 03-03, 03-04, 03-05, 03-06]
tech-stack:
  added: []
  patterns: [OR plainto_tsquery rewrite, per-leg SQL without shared CTE, set_config planner switches]
key-files:
  created:
    - backend/migrations/versions/0006_search.py
    - backend/config/ranking.yaml
    - backend/ranker/search/{__init__,models,config,sql,service}.py
    - backend/ranker/api/search.py
    - backend/ranker/api/meta.py
    - backend/tests/fixtures/search/jobs_seed.py
  modified:
    - backend/ranker/api/__init__.py
    - backend/ranker/main.py
    - backend/ranker/embeddings/service.py
    - backend/config/README.md
key-decisions:
  - "Vector leg binds the query vector directly (no joined q CTE) so an HNSW ordering scan stays possible"
  - "Symbol skills (C++, C#, .NET, Node.js) are removed from the english lex text and matched via skills_tsv"
requirements-completed: [SRCH-02, SRCH-03, SRCH-04, SRCH-07, UI-04]
duration: ~1h
completed: 2026-11-22
---

# Phase 3 Plan 01: Hybrid search slice Summary

Hybrid retrieval (english OR tsquery + simple-config skills leg + exact vector leg, RRF k=60) behind `GET /api/jobs/search`, with every search logged to `search_logs` and the full API contract fixed up front.

## Commits
- 1e4d462 test: contract models, seed fixture, failing tests (RED)
- 0f88d05 feat: migration 0006, ranking.yaml, hybrid SQL and service (GREEN)
- e6fe0e0 feat: /api/jobs/search and /api/meta

## Results
- Full backend suite: 988 passed, 2 deselected (llm marker). ruff check, ruff format --check and pyright clean.
- Vector leg returns exactly 100 of 1000 rows unfiltered and with a 10% filter in exact and hnsw modes; no CTE Scan, no hnsw index in exact mode.

## Deviations from Plan
- [Rule 3] `_vector_literal` renamed to public `vector_literal` in `ranker/embeddings/service.py` (old name kept as alias), as the plan interface specified.
- Seed fixture: C++ job titled "C++ Developer" (plan said "C++ Engineer") so lexical ordering against the "C Firmware Developer" decoy is deterministic.
- `lex_hits`/`vec_hits` in search_logs are counted over the fused (truncated to fused_k) set, not the raw legs.
- Lexical-skill-removal only drops words containing `+ # .` that the matcher recognises; plain skill words (python, kafka) stay in the english text.
- The plan's "Go query_ok relaxation" from RESEARCH is not implemented (not in the plan's task actions); context regexes already cover "Go developer".
- Tie-break and "both legs outrank" RRF checks use direct SQL with vec_k=1 because the vector leg otherwise returns every row at this seed size.

## Known Stubs
- `JobResult.why_matched` is "" and `set_aside`, `relax`, `next_cursor` are empty/None by design; 03-03/03-04 fill them.

## Self-Check: PASSED
