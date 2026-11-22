---
phase: 03-hybrid-search-eval-baseline
plan: 03
subsystem: search
tags: [query-parse, filters, set-aside, postgres]
requires: [03-01]
provides:
  - ranker.search.parse (parse_rules, parse_query, QueryParse, _model_fallback hook for 03-05)
  - compile_filters / build_set_aside_sql in ranker.search.sql
  - config/query_synonyms.yaml, skills.yaml query_ok, SkillMatcher.extract_query
affects: [03-04, 03-05, 03-08]
key-files:
  created:
    - backend/ranker/search/parse.py
    - backend/config/query_synonyms.yaml
    - backend/tests/test_parse_rules.py
    - backend/tests/test_parse_cache.py
    - backend/tests/test_search_filters.py
    - backend/tests/test_search_setaside.py
  modified:
    - backend/ranker/search/sql.py
    - backend/ranker/search/service.py
    - backend/ranker/ingest/skills.py
    - backend/config/skills.yaml
    - backend/config/README.md
requirements-completed: [SRCH-05, SRCH-06, UI-05, UI-06]
completed: 2026-11-22
---

# Phase 3 Plan 03: Rules query parser, filters and set-aside Summary

Free text is read by rules into editable chips (city, min salary, work mode, seniority), the filters are applied inside both retrieval legs, and each response reports per-filter set-aside counts and single-relaxation counts.

## Commits
- 821840e feat: rules query parser, synonyms, query_ok skills
- 072d7ff fix: bound the query-location cache (coordinator security request)
- 2ca1813 feat: whitelisted filter compiler (D-08 geography, D-09 salary)
- cce4ebf feat: set-aside/relax counts and wiring into search()

## Results
- Full backend suite: 1023 passed, 2 deselected (llm marker) before the final test-typing edit; the changed test file re-run passes (7). ruff check, ruff format --check and pyright clean.
- Demo query "backend python bengaluru 10 LPA remote ok" gives chips "City: Bengaluru + remote" and "₹10 LPA+", lex text "backend python".
- Set-aside query latency at 23 seeded jobs: about 33 ms for the whole search including the set-aside query. 03-12 must measure at ~50k rows; if too slow make it conditional on results < 20.

## Decisions
- Geo default (no city): `country_code='IN'` OR remote with eligibility worldwide/india/apac/unknown OR (country NULL and not remote). The third branch matches ADR 0002 (`geo_unknown`: on-site with unknown country is kept); it also keeps `remote_type='unknown'` rows with no country.
- City filter with remote_ok adds remote worldwide/india/apac only (unknown eligibility is not included when a city is named).
- Place detection accepts an n-gram only if it is a known city alias or a minimal span, so "backend python bengaluru" does not swallow "backend python".
- Work-mode words are read before cities so "remote india" keeps the remote chip; a bare remote with a city sets remote_ok instead of a work-mode chip.
- stopwords and role_words live in query_synonyms.yaml (YAML booleans `on`/`no` are quoted).
- `lex_text_for` moved to parse.py and is re-exported from service.

## Deviations from Plan
- [Rule 2 - Security, coordinator request] `_location` cache is `lru_cache(maxsize=4096)` (was unbounded `@cache` keyed by user n-grams); other `@cache` functions in ranker/search take no arguments. Test in tests/test_parse_cache.py.
- Skill canonicals are lowercase in skills.yaml ("python", "go"), not "Python" as the plan text says; tests use the real values.
- Plan test data tweak: US-only remote jobs are seeded without salary, so set_aside is {city: 7, min_salary: 6} and relax is {city: 7, min_salary: 6}.
- Plain skill words (python) stay in the english lex text; only symbol skills and ambiguous query_ok words (go, r, c, node) are dropped from it, as in 03-01.
- OpenAPI check not run: `ranker.openapi_export` does not exist on this branch (03-02 runs in parallel). models.py and api/search.py are unchanged, so the schema does not move.
- Threat T-03-10: parse JSON stores only counts and sources; no residue text.

## Known Stubs
- `_model_fallback` in parse.py returns None by design; 03-05 fills it. `why_matched` and `next_cursor` remain 03-01 placeholders.

## Self-Check: PASSED
