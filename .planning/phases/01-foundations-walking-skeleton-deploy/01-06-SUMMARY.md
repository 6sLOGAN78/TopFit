---
phase: 01-foundations-walking-skeleton-deploy
plan: 06
subsystem: infra
tags: [github-actions, ci, cd, ghcr, ssh]
requires: [01-03, 01-04, 01-05]
provides:
  - ".github/workflows/ci.yml: CI gates, GHCR publish, gated SSH deploy with HTTPS smoke"
affects: [01-07]
key-files:
  created: [.github/workflows/ci.yml]
decisions:
  - "uv pinned to 0.9.18 in CI (matches local lock generation and uv_build<0.10); plan suggested 0.13.9"
  - "web uses node-version-file web/.nvmrc (24.22.3) instead of node-version 24"
  - "Added npm run format:check step to web job"
metrics:
  completed: 2026-11-21
---

# Phase 1 Plan 06: CI/CD workflow Summary

Single workflow with jobs backend (ruff, format, pyright, pytest on testcontainers), web (lint, format, typecheck, build), deploy-config (caddy validate, check-prod-ports, bash -n), images (offline `--network none` selfcheck, GHCR push on main with lowercase owner and sha-<7>/main tags), gate (secret presence output) and deploy (scp, deploy.sh, smoke.sh), skipped with a notice until secrets exist.

Tasks 1 and 2 were committed together as one commit, 7d856fd, because both edit the one file and it was written in a single pass.

## Verification
- actionlint (rhysd/actionlint:latest, pulled) reports no errors.
- No `if:` references `secrets.*`; only the images job has `packages: write`.
- Not run on GitHub (no push); first real run happens after merge.

## Deviations from Plan
- uv version 0.9.18 instead of 0.13.9 (see decisions; low risk, either works since uv_build is fetched in an isolated build env).
- Node version taken from .nvmrc; added format:check step.
- Single commit for both tasks rather than two.

## Self-Check: PASSED
- .github/workflows/ci.yml present; commit 7d856fd exists.
