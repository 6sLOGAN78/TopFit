---
phase: 03-hybrid-search-eval-baseline
plan: 10
subsystem: evals
tags: [snapshot, pooling, llm-grading, blind-review, kappa]
requires: [03-09, 03-04, 03-05]
provides:
  - ranker.evals.snapshot (export_snapshot, load_snapshot, verify_hash)
  - ranker.evals.grading (pool_candidates, grade_pool, blind_review, agreement_report, GradeOut)
  - "ranker eval snapshot export|load, eval pool|grade|review|agreement"
key-files:
  created: [backend/ranker/evals/snapshot.py, backend/ranker/evals/grading.py, backend/tests/test_eval_snapshot.py, backend/tests/test_eval_pooling.py, backend/tests/test_eval_review.py]
  modified: [backend/ranker/cli.py, .gitignore]
decisions:
  - Snapshot values are serialised as strings for numeric/date/timestamptz columns and cast on load, so export -> load -> export gives the same hash.
  - Load refuses a non-empty jobs table before migrating; migration swaps DATABASE_URL (env.py reads settings, not alembic.ini) and restores it.
  - Pooling runs under exact_config() like the ranking eval, so pooled and scored rankings use the same vector mode.
metrics:
  tasks: 2
  completed: 2026-11-23
---

# Phase 3 Plan 10: Eval-set tooling

## Commits
- 1f52f57 feat(03-10): eval snapshot export and hash-verified load
- 95c086a feat(03-10): eval pooling, LLM grading, blind review and agreement

## Behaviour notes
- Export: active canonical jobs with an embedding, dedupe_key first then cos > 0.97 (lowest id wins), gzip mtime=0, sorted keys, id order. `description_raw` is never selected. Writes `job_pool_snapshot.jsonl.gz`, `snapshot.sha256` (sha256sum format) and `snapshot.meta.json` (rows, as_of UTC, embed_model, sha256).
- Load (`--gz`, optional `--sha256-file`, default `snapshot.sha256` beside the gz, `--replace`): verify hash, refuse non-empty target, `alembic upgrade head` on DATABASE_URL, insert with original ids, reset id sequence. Works on a never-migrated database (tested on a freshly created DB). `--replace` truncates jobs and companies with CASCADE, so use it on eval databases only.
- Pool: union of top 20 per variant, written to `evals/data/pool.jsonl` with the variants that surfaced each pair; already-judged pairs are excluded.
- Grade: `purpose="eval_grade"`, tier agent, enum-only `GradeOut` (passes lint_schema), no temperature, `graded_by` = `LLM_MODEL_AGENT`, `rubric_version` grade-v1. Prints estimated worst-case cost and requires `--yes`. BudgetExceeded or LLMUnavailable stops cleanly (exit 1); rerun resumes.
- Review: seeded sample (`random.Random(seed)` over the sorted pair list), shows persona and posting only, saves after every answer, `q` quits, reviewed rows are skipped on rerun. Agreement: n, exact %, within-1 %, quadratic and linear weighted kappa.
- `.gitignore` now excludes `evals/data/job_pool_snapshot.jsonl.gz`, `evals/data/pool.jsonl` and `evals/results/`.

## Deviations from Plan
- [Rule 3] `evals/results/` and the private snapshot were not gitignored; added entries (requested in the task brief). `pool.jsonl` is also ignored as an intermediate artifact.
- Pooling reads job ids only; the plan's key link pattern `legs="(lex|vec)"` is satisfied through the shared `VARIANTS` map from 03-09 rather than literal strings.
- No live-LLM test was added (the `@pytest.mark.llm` option was "if any").

## Verification
- uv run pytest -q: 1125 passed, 2 deselected (new: 4 snapshot, 5 pooling/grading, 6 review/agreement)
- ruff check, ruff format --check, pyright, `python -m ranker.openapi_export --check`: clean

## Known Stubs
None. `evals/data/*` content is produced by 03-13/03-14.

## Self-Check: PASSED
