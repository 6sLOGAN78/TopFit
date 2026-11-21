---
phase: 01-foundations-walking-skeleton-deploy
plan: 04
subsystem: infra
tags: [docker, compose, makefile, fastembed, nextjs, pre-commit]
requires: [01-01, 01-02, 01-03, 01-05]
provides:
  - API image with baked offline bge-small model (ranker-api:dev)
  - standalone web image (ranker-web:dev)
  - make dev local stack, Makefile incl. manual deploy fallback
  - documented .env.example with guard test
key-files:
  created:
    - backend/Dockerfile
    - backend/.dockerignore
    - backend/ranker/selfcheck.py
    - web/Dockerfile
    - web/.dockerignore
    - compose.yaml
    - compose.dev.yaml
    - Makefile
    - .env.example
    - backend/tests/test_env_example.py
    - .pre-commit-config.yaml
    - README.md
  modified:
    - backend/migrations/README
    - .planning/research/FEATURES.md
requirements-completed: [FND-01, FND-02, FND-05, SEC-07]
duration: ~40min
completed: 2026-11-21
---

# Phase 1 Plan 04: Images, local stack, Makefile, env docs Summary

One-command local stack (`make dev`) with the real baked ONNX model, plus the two images CI will publish.

## Results
- `docker run --network none ranker-api:dev python -m ranker.selfcheck` -> `{"ok": true, "dim": 384, "cache": "/opt/models"}`; `id -u` = 10001.
- Image sizes (disk usage / content size): ranker-api:dev 729 MB / 205 MB; ranker-web:dev 417 MB / 103 MB.
- Dev stack end to end: `/api/readyz` via :3000 returned status ok, embedding dim 384, pgvector 0.8.7; page contains "Ranker"; cookie ping `false` then `true`; API logs are JSON with request_id; worker stop took 0.3 s and logged `worker_stopped`.
- `tests/test_env_example.py` (3 tests), ruff, pyright and `pre-commit run --all-files` all pass.

## Decisions
- Web Docker base pinned to `node:24.22.3-trixie-slim` (exact patched version, per orchestrator note re Brotli CVE-2026-111497, Node 24 < 24.22.1 affected). `web/.nvmrc` not touched; keep in sync with it.
- `next/font/google` (Geist) still fetches at build time, so the web image build needs network. Not changed.
- shadcn left in `dependencies` (standalone output does not ship it anyway).

## Deviations from Plan
- [Rule 3] Host port 5432 was in use and disk was nearly full during verification. I used a temporary compose override (port 55432, scratchpad only) and ran `docker builder prune`. Committed compose.dev.yaml keeps `127.0.0.1:5432:5432` as specified.
- [Rule 3] pre-commit hooks auto-fixed trailing whitespace/EOF in `backend/migrations/README` and `.planning/research/FEATURES.md` (committed). Needed `npm ci` in web/ for the eslint hook.
- Commit trailer uses `Claude Sonnet 5.5` per the current attribution instruction instead of the plan's Opus text.

## Known Stubs
None.

## Commits
- b13b50c feat(01-04): api image with baked offline model and standalone web image
- d3de6e3 feat(01-04): local compose stack, Makefile and documented env vars
- 26af0cd docs(01-04): pre-commit hooks and README run instructions

## Self-Check: PASSED
