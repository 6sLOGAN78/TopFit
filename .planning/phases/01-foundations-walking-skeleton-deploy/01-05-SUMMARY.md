---
phase: 01-foundations-walking-skeleton-deploy
plan: 05
subsystem: infra
tags: [caddy, docker-compose, bootstrap, deploy, ufw]
requires: []
provides:
  - single-origin Caddy reverse proxy (web at /, api at /api and /api/*)
  - standalone production compose stack
  - idempotent host bootstrap, deploy, smoke and memory scripts
affects: [01-07]
key-files:
  created:
    - deploy/Caddyfile
    - deploy/compose.prod.yaml
    - deploy/check-prod-ports.sh
    - deploy/bootstrap.sh
    - deploy/deploy.sh
    - deploy/smoke.sh
    - deploy/measure-memory.sh
metrics:
  completed: 2026-11-21
---

# Phase 1 Plan 05: Server-side deploy artifacts Summary

Caddy single-origin TLS proxy, standalone prod Compose stack (only caddy publishes ports, PG18 volume path) and four idempotent provider-agnostic scripts for bootstrap, deploy, smoke and memory measurement.

## Commits
- e3f42da: Caddyfile, compose.prod.yaml, check-prod-ports.sh
- 284b128: bootstrap.sh, deploy.sh, smoke.sh, measure-memory.sh

## Validation done
- `caddy validate` in caddy:2.11.9-alpine: valid
- `check-prod-ports.sh`: passes, only `caddy` publishes ports, db volume `/var/lib/postgresql`
- `bash -n` on all scripts: pass
- shellcheck (container): no errors or warnings; two info notes only (SC2009 ps|grep in measure-memory.sh, SC2329 trap-invoked function in smoke.sh), both false positives
- `bootstrap.sh` with no args exits 2 with usage and changes nothing

## Deviations from Plan
- Commit trailer uses `Co-Authored-By: Claude Sonnet 5.5` per the attribution instruction in this session, instead of the Opus 5.5 trailer named in the plan.
- Temp-dir cleanup in check-prod-ports.sh and smoke.sh uses `rm -f` of known files plus `rmdir` rather than `rm -rf`, to stay clear of the safety check on recursive removal. Behaviour is equivalent.

## Deferred (needs a real VM / backend image from 01-01 / 01-04)
- Running `bootstrap.sh` on Ubuntu 24.04 (needs root and a VM; only syntax, lint and usage path verified)
- `deploy.sh`, `smoke.sh`, `measure-memory.sh` end-to-end (need images ghcr.io/.../ranker-api and ranker-web and a live host); the `/api/readyz` JSON field paths (`.checks.pgvector.ok`, `.checks.embedding.dim`) are taken from the plan and must match plan 01-01's implementation
- cgroup path for `memory.peak` assumes cgroup v2 with the systemd Docker driver (Ubuntu 24.04 default)

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
All seven files exist under deploy/; commits e3f42da and 284b128 exist.
