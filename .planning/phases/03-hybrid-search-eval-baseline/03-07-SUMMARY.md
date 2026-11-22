---
phase: 03-hybrid-search-eval-baseline
plan: 07
subsystem: search
tags: [rate-limit, postgres, retention, smoke]
requires: [03-02, 03-04, 03-05]
provides: [rate_limit_search dependency, rate_limit_counters table, prune_search_state job]
affects: [deploy/smoke.sh, deploy/compose.prod.yaml]
key-files:
  created:
    - backend/migrations/versions/0007_rate_limits.py
    - backend/ranker/search/ratelimit.py
    - backend/tests/test_rate_limit.py
  modified:
    - backend/ranker/api/search.py
    - backend/ranker/settings.py
    - backend/ranker/worker/scheduler.py
    - backend/tests/test_scheduler_jobs.py
    - deploy/smoke.sh
    - deploy/compose.prod.yaml
    - .env.example
requirements: [SEC-04, OPS-03]
---

# Phase 3 Plan 07: Rate limits, retention pruning, smoke search

Postgres UNLOGGED fixed-window rate limits on guest search (60/hour per signed `rk_gid` guest, 300/hour per HMAC'd IP), a daily prune job, and a one-search deploy smoke check.

## Decisions
- Window start is computed from Python `now` (passed to `date_bin`), so time_machine tests work and Retry-After is exact.
- Per-IP bucket uses the real client IP. `RATE_LIMIT_TRUST_XFF` (default false, set true for the api in compose.prod.yaml) makes the code use only the last X-Forwarded-For hop, the one Caddy adds. Earlier hops are client-controlled and ignored. Trust boundary: Caddy is the only published port (check-prod-ports.sh) and overwrites XFF from untrusted clients. Without the flag, XFF is ignored (tested).
- IPv6 is cut to its /64 before hashing; IPv4-mapped IPv6 is counted as the IPv4; IPv4 stays whole. Raw IPs are never stored.
- OPS-03: deploy.sh already runs `alembic upgrade head` before `up`; unchanged.

## Deviations from Plan
- [Rule 2 - Security] Added the XFF trust flag, /64 normalisation and tests, per coordinator instruction.
- Retention job registered in `register_core_jobs` as the plan says (daily 02:00 UTC).

## Commits
- 6c3ee99 rate limits (migration 0007, dependency, settings)
- 4543af0 prune job and smoke search
- the XFF/IPv6 fix and this summary are in the follow-up commit

## Verification
pytest 1094 passed, 2 deselected; ruff, pyright and openapi_export --check clean (OpenAPI unchanged).
