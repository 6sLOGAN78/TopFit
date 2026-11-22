---
phase: 03-hybrid-search-eval-baseline
plan: 11
subsystem: evals
tags: [query-parse, eval, d-17, haiku, jev]
requires: [03-05, 03-10]
provides:
  - "evals/data/parse_queries.jsonl (53 gold queries)"
  - "ranker eval parse command (rules vs haiku vs jev)"
affects: [03-14]
key-files:
  created:
    - backend/ranker/evals/parse_eval.py
    - backend/tests/test_eval_parse.py
    - evals/data/parse_queries.jsonl
  modified:
    - backend/ranker/search/parse.py
    - backend/ranker/cli.py
decisions:
  - "force_fallback sends the whole query to the model and skips the residue gate and cache; default False leaves production unchanged"
  - "QueryParse gained role_family (set only from a model answer, read by nothing in search) so model variants can be scored on it"
metrics:
  tasks: 2
  completed: 2026-11-23
---

# Phase 3 Plan 11: D-17 query-parse measurement Summary

Gold set of 53 queries plus `ranker eval parse`, scoring rules-only, rules+Haiku and rules+Jev through the production `parse_query` on per-field accuracy, p50/p95 latency and cost per 1k queries. The live run is left to 03-14.

## What was built
- 53 gold queries: the four UI examples, city aliases, salary forms (LPA, lakh, glued L, rupee sign, crore), localities (Whitefield, Powai, Hinjewadi), work-mode and seniority phrasings, and 10 filterless queries. All `labelled_by: claude-opus-5-5`, `reviewed: false`; a human pass is still needed before the numbers are quoted as ground truth.
- `parse_eval.py`: loader with 1-based line errors and closed-set checks, `run_parse_eval`, markdown report. Fields scored: city, min salary bucket, work mode (including `remote_or_city`), seniority, plus role_family for model variants (rules report n/a). Reports exact-match on the four filters, degraded-call count, models used (llm_calls.model, which holds the routed model for Jev), and the individual misses.
- Cost comes from `llm_calls` rows (`purpose='query_parse'`, id above the pre-run maximum), so it reflects the gateway's reserve-then-settle figures.
- A variant whose provider is unavailable returns `status: "not run"` with `reason: "<variant> provider not configured"`.
- CLI: `--queries`, `--variants`, `--out`, `--yes`. Model variants without `--yes` print the estimated call count and exit 2 (T-03-34). Results go to `evals/results/parse_<ts>.json`.

## Deviations from Plan
- [Rule 2] `QueryParse.role_family` added (default None) in `ranker/search/parse.py`, because the plan asks for role-family accuracy and the dataclass carried no such field. Non-breaking.
- Variant type is named `ParseVariant` to avoid clashing with the ranking eval's `Variant` in cli.py.

## Verification
- Full suite: 1142 passed, 2 deselected (before the final tiny typing change); `tests/test_eval_parse.py`: 11 tests (loader errors, shipped gold validity, rules/haiku/jev scoring, cache bypass, not-run, degraded, CLI confirmation gate and end-to-end with a fake gateway). Tests use only FakeProvider.
- ruff check and format clean, pyright 0 errors, `openapi_export --check` passes.
- No live run was made; no .env contents were read or printed.

## Known Stubs
None.

## Commits
- 29597b0 gold set, parse_eval, force_fallback
- 4ba1353 `ranker eval parse` command
