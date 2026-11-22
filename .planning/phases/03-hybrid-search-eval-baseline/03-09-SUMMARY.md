---
phase: 03-hybrid-search-eval-baseline
plan: 09
subsystem: evals
tags: [ndcg, bootstrap, kappa, ci-gate]
requires: [03-02, 03-04]
provides:
  - ranker.evals.metrics (ndcg_at_k, recall_at_k, mrr, p_at_k, unjudged_rate_at_k, bootstrap_ci, weighted_kappa)
  - ranker.evals.ranking_eval (Persona, Judgment, run_ranking_eval, check_gate, report_markdown)
  - "ranker eval ranking" CLI command
  - CI eval-gate job
affects: [03-10, 03-13, 03-14]
key-files:
  created: [backend/ranker/evals/metrics.py, backend/ranker/evals/ranking_eval.py, backend/tests/test_eval_metrics.py, backend/tests/test_eval_kappa.py, backend/tests/test_eval_gate.py, backend/tests/fixtures/ranking_eval/personas.jsonl, backend/tests/fixtures/ranking_eval/judgments.jsonl]
  modified: [backend/ranker/cli.py, backend/pyproject.toml, .github/workflows/ci.yml]
decisions:
  - Exact vector mode is forced by copying the config dir to a temp CONFIG_DIR and rewriting only the vector_mode line, so pipeline_version is unchanged when the real config is already exact.
  - Personas with no judgments are skipped and listed in the report instead of scoring 0.
  - Result JSON (evals/results/<ts>_<sha>.json) holds the timestamp outside the report object so report JSON is byte-identical across runs.
metrics:
  tasks: 3
  completed: 2026-11-22
---

# Phase 3 Plan 09: Offline ranking eval and baseline gate

`ranker eval ranking` runs the production `search()` path (no LLM, `log=False`, `page_size=50`, fixed `as_of`) over personas, scores nDCG@10, Recall@50, MRR and P@5 with seeded (20261122) percentile bootstrap 95% CIs, reports unjudged@10, and gates on nDCG@10 against `baseline.json`.

## Commits
- 75f7461 feat(03-09): ranking metrics, bootstrap CIs and weighted kappa
- 1c82454 feat(03-09): ranker eval ranking command with baseline gate
- 343792d ci(03-09): eval-gate job with private snapshot fetch and clean skip

## Behaviour notes
- Variants: `lex`, `vec`, `hybrid` (RRF, no signals), `hybrid_signals` (default). `--ranking-config` evaluates an alternative ranking.yaml. Only `--mode no-llm` is accepted.
- Gate: exit 1 when baseline - current > max-drop (default 0.02), printing `{"gate": "failed", "drop": ...}`; exit 0 otherwise, and exit 0 with a notice when no baseline file exists. Usage errors exit 2 with JSON on stderr.
- Effective grade is `human_grade` when set, else `grade`.
- `--as-of` defaults to `evals/data/snapshot.meta.json` `as_of`; usage error if neither exists.
- CI job: skips with `::notice::` when `EVAL_DATA_TOKEN` or `vars.EVAL_DATA_REPO` is empty; otherwise downloads release `snapshot-v1`, runs `sha256sum -c`, migrates, loads (`ranker eval snapshot load`, delivered by 03-10), runs the gate. Not in `images.needs`. `actions/cache` pinned to the v5 commit SHA.
- No migration added.

## Deviations from Plan
None of substance. The "verified on the PR run" acceptance item for the CI skip needs a real PR run; locally the YAML parses and the skip step is present.

## Verification
- uv run pytest -q: 1098 passed, 2 deselected (new: 10 metrics/kappa tests, 7 gate tests)
- ruff check, ruff format, pyright, openapi_export --check: clean

## Known Stubs
None. `evals/data/*` and `evals/baseline.json` come from 03-10/03-13/03-14. `evals/results/` is not gitignored here (not my file to edit).

## Self-Check: PASSED
