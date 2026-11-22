---
phase: 02-live-jobs-index
plan: 09
subsystem: embeddings
tags: [embeddings, pgvector, ingest, cli]
requires: [02-08]
provides:
  - embed_missing / invalidate_other_models / job_embedding_text
  - embed pass inside every ingest run
  - "ranker embed --missing | --all --model X" CLI
key-files:
  created:
    - backend/ranker/embeddings/service.py
    - backend/tests/test_embed_service.py
    - backend/tests/test_pipeline_embed.py
    - backend/tests/test_cli_embed.py
  modified:
    - backend/ranker/embeddings/__init__.py
    - backend/ranker/ingest/pipeline.py
    - backend/ranker/cli.py
    - backend/tests/test_pipeline_remotive.py
requirements-completed: [SRCH-01, ING-09]
completed: 2026-11-22
---

# Phase 2 Plan 09: Embed pass and backfill CLI

Every ingest run now embeds new or changed active jobs (384-d, tagged with `embedding_model`) off the event loop in batches of 32; a backfill CLI fills NULLs or migrates to another model.

## Commits
- 105b030 Task 1: embed_missing service and tests
- cd77c7c Task 2: pipeline hook, `ranker embed` CLI, tests

## Public signatures
- `embed_missing(engine, embedder, model_name, *, batch_size=32, limit=None) -> int` (keyset cursor on `id`, so a batch can never loop; UPDATE guarded by `embedding IS NULL`)
- `invalidate_other_models(engine, model_name) -> int`, `job_embedding_text(title, company, skills, description_clean) -> str`, `EmbeddingDimensionError`
- `run_ingest(..., embedder: Embedder | None = None)`; `RunSummary.embedded`; `ingest_runs.notes.embedded`
- CLI `ranker embed` prints `{"embedded": N, "model": ...}`; ingest JSON line also gains `embedded`.

## Deviations from Plan
**1. [Rule 3 - Blocking] Updated test_pipeline_remotive.py (02-08 file).** It asserted `embedding is None` after ingest; that is now false by design, so it asserts not-None.
**2. [Minor] `RunSummary.embedded` field added** (default 0) so callers and the CLI can report it, in addition to the notes entry.
**3.** Embedder build failure is treated like an embed failure (partial, `EmbedError:<Type>`); a run already `failed` stays failed.

## Verification
Full suite 703 passed; `ruff check`, `ruff format` and `pyright` clean. No network or LLM calls (fake embedder).

## Known Stubs
None.

## Self-Check: PASSED
