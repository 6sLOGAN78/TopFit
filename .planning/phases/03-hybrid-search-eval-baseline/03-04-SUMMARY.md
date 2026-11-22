---
phase: 03-hybrid-search-eval-baseline
plan: 04
subsystem: search
tags: [ranking, rrf, cursor, itsdangerous, why-matched]
requires: [03-02, 03-03]
provides:
  - ranker.search.rank (score_candidates, apply_company_cap, why_matched)
  - SearchOptions for eval variants (legs, apply_signals, log, now, page_size)
  - signed Load-more cursor over search_logs.ranked_ids
affects: [03-09, 03-10, 03-14]
key-files:
  created: [backend/ranker/search/rank.py, backend/tests/test_search_rank.py, backend/tests/test_search_cursor.py, backend/tests/test_search_options.py]
  modified: [backend/config/ranking.yaml, backend/config/README.md, backend/ranker/search/config.py, backend/ranker/search/service.py, backend/ranker/search/sql.py]
decisions:
  - Cursor pages reconstruct ParsedSummary from search_logs (filters, source, cached, skills); chips are not restored on pages 2+.
  - Canonical skills are now stored in search_logs.parse JSON ("skills") so cursor pages can compute matched_skills.
  - Company key is company_id, else lower-cased name, else job id.
metrics:
  tasks: 2
  completed: 2026-11-22
---

# Phase 3 Plan 04: Guest ranking, company cap, why-matched, signed cursor

Guest ranking is normalised RRF (divided by the fixed maximum 2/(k+1), never min-max) plus freshness, region-certainty and salary-known boosts, minus ghost and unknown-seniority penalties. Weights live in ranking.yaml (now version 2). A per-company cap defers extras to just after position 10. Every card gets a rule-based why_matched line. Load more hydrates from stored ranked_ids behind a 24 h signed cursor.

## Commits
- b70c301 feat(03-04): pure guest ranking signals, company cap and why-matched
- 3d87ddf feat(03-04): wire ranking, signed Load-more cursor and SearchOptions into search()

## Behaviour notes
- search() hydrates all fused candidates (up to 100) once, scores, caps, then returns the first page; ranked_ids is the full final order.
- Cursor: URLSafeTimedSerializer, salt rk-cursor-v1, 24 h. Bad, tampered or unknown cursors give HTTP 422 with message invalid_cursor in the standard envelope. No contract change; openapi_export --check clean.
- Cursor pages skip jobs no longer active (page can be short) and write no search_logs row.
- search_logs.results entries carry score plus rrf, lex_rank, vec_rank, cos, recency, region, salary_known, ghost. With apply_signals=False the signal features are null and score is the raw RRF.
- SearchOptions(log=False) returns search_log_id None and no next_cursor.
- why_matched matched terms: query skills present on the job plus query words found in the title (max 3).

## Deviations from Plan
None of substance. Scored.features is typed dict[str, float | None] (JSON-safe nulls instead of NaN). HYDRATE_SQL gained company_id and normalized_name columns for the cap.

## Verification
- uv run pytest -q: 1050 passed, 2 deselected
- ruff check, ruff format --check, pyright, openapi_export --check: clean
- No min( in rank.py (no min-max normalisation of rrf).

## Known Stubs
None.

## Self-Check: PASSED
