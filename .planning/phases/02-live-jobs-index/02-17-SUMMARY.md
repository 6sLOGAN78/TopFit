---
phase: 02-live-jobs-index
plan: 17
subsystem: ingest-evals
tags: [eval, normalisation, d-20, ci-floor, cli]
requires: [02-08, 02-11, 02-16]
provides:
  - "ranker eval ingest / eval capture / runs report"
  - "78 labelled real postings and per-field CI floors"
affects: [02-15]
tech-stack:
  added: []
  patterns: ["pure scoring over a JSONL fixture", "gold in_scope null = ambiguous, excluded"]
key-files:
  created:
    - backend/ranker/evals/__init__.py
    - backend/ranker/evals/ingest_eval.py
    - backend/tests/test_ingest_eval.py
    - backend/tests/fixtures/ingest_eval/postings.jsonl
    - backend/tests/fixtures/ingest_eval/floors.json
    - docs/evals/ingest-normalisation.md
  modified:
    - backend/ranker/cli.py
    - README.md
key-decisions:
  - "Gold in_scope may be null (ambiguous: foreign location with work mode not stated); such records are excluded from all metrics"
  - "Floors are measured accuracy minus 0.05, rounded down; no normaliser rules tuned"
requirements-completed: [ING-06, NORM-02, NORM-03, NORM-04, NORM-05]
duration: ~1h
completed: 2026-11-22
---

# Phase 2 Plan 17: Ingest normalisation eval Summary

Scoring CLI, capture helper and run-counter report, plus 78 hand-labelled real postings with a CI accuracy floor per field and a written-up eval doc. The sample is smaller and thinner than planned (see deviations).

## Commits

- 919daaa: eval module, capture helper, `eval ingest|capture` and `runs report` commands, tests
- 8d793f6: labelled fixture, floors, floor tests, eval doc, README section

## Results (normalizer_version 1, 2026-11-22)

| field | n | accuracy | coverage | precision |
|---|---|---|---|---|
| in_scope | 70 | 0.91 | 1.00 | 0.91 |
| remote_type | 9 | 0.89 | 0.89 | 1.00 |
| onsite_days_per_week | 9 | 0.89 | 0.67 | 1.00 |
| remote_eligibility | 9 | 0.67 | 1.00 | 0.67 |
| seniority | 9 | 0.89 | 0.89 | 0.88 |
| employment_type | 9 | 1.00 | 0.56 | 1.00 |
| salary | 9 | 1.00 | 0.22 | 1.00 |
| city | 9 | 1.00 | 0.22 | 1.00 |
| country_code | 9 | 0.89 | 0.33 | 0.67 |
| industry | 9 | 0.44 | 0.56 | 0.80 |

Composition: greenhouse 20, ashby 20, lever 10, arbeitnow 10, remoteok 7, remotive 6, jobicy 5 (78). Gold: 10 in scope, 60 out of scope, 8 ambiguous. Owner-reviewed labels: 0.

## Deviations from Plan

**1. [Source failure] Internshala returned 0 postings.** The first request got a bot-challenge page (`challenge:200`); PoliteFetcher stopped as designed. Nothing was retried or bypassed. Recorded in the doc. Adzuna skipped (no keys).

**2. [Scope] 78 records, not about 100 (acceptance asked for at least 90).** The Internshala quota could not be filled from the others: a top-up capture pass over greenhouse/ashby/lever was blocked by the permission layer as a second capture of the same sources, so it was not made. The test floor for record count is 75 and the doc states the shortfall.

**3. [Sample skew] Only 10 in-scope gold records.** The deterministic spread over alphabetically ordered boards picked mostly non-engineering and non-India roles, so per-field numbers (except in_scope) rest on 9 to 10 postings. Stated prominently in the doc and README; recommended follow-up is a tech-title-biased top-up capture.

**4. [Rule 2 - design] Gold `in_scope` allows null** for ambiguous postings (8 records), excluded from every metric, so ambiguous labels cannot inflate or deflate scores.

**5. Capture configuration.** `capture_samples` narrows Internshala (2 job + 2 internship categories, 2 pages) and Arbeitnow (3 pages) to keep the one capture polite. Board sources need a DB engine (seed registry); the capture ran against a throwaway testcontainers Postgres from the already-present image.

## Self-Check: PASSED

Files exist; commits 919daaa and 8d793f6 exist; full suite 898 passed, ruff check and format clean, pyright 0 errors; fixture has 0 email matches.
