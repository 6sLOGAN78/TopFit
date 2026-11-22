---
phase: 02-live-jobs-index
plan: 11
subsystem: ingest
tags: [expiry, dedupe, ghost, purge, volume-guard]
requires: [02-09, 02-10]
provides:
  - expiry policies (absence, ttl) with volume guard
  - cross-source dedupe with group_sources
  - repost detection and ghost signals
  - ranker purge-source / expire-ttl commands
key-files:
  created:
    - backend/ranker/ingest/expiry.py
    - backend/ranker/ingest/dedupe.py
    - backend/ranker/ingest/ghost.py
    - backend/tests/test_expiry.py
    - backend/tests/test_dedupe.py
    - backend/tests/test_ghost.py
    - backend/tests/test_cli_purge.py
  modified:
    - backend/ranker/ingest/pipeline.py
    - backend/ranker/ingest/store.py
    - backend/ranker/cli.py
    - backend/config/sources.yaml
requirements-completed: [ING-08, ING-06]
completed: 2026-11-22
---

# Phase 2 Plan 11: Expiry, dedupe, ghost signals and purge Summary

Guarded per-source expiry (absence after 2 complete runs, TTL for capped feeds) with a 50%-of-median volume guard, cross-source dedupe grouping, repost and ghost signals, D-20 counters in `ingest_runs`, and an operator purge command.

## Public signatures

- `expiry`: `ExpiryConfig(absence_missed_runs=2, volume_guard=VolumeGuard(ratio=0.5, window=5, min_history=2), ttl_days, unseen_days)`; `volume_guard(conn, source, scope|None, fetched, cfg) -> GuardDecision(allowed, normal, reason)`; `expire_absent_after_run(conn, *, source, run_id, scopes, cfg) -> int`; `expire_ttl(engine_or_conn, *, source, cfg, now, run_id=None, healthy=None) -> int`; `reactivate_run(engine, run_id) -> int`; `purge_source(engine, source) -> int`.
- `dedupe`: `DedupeStats(changed, duplicates)`; `recompute_dedupe(conn, keys) -> DedupeStats`.
- `ghost`: `STALE_DAYS = 60`, `REPOST_FLAG_AT = 3`; `detect_reposts(conn, *, run_id, source) -> int`; `refresh_ghost_signals(conn, *, source, now) -> int`.
- `RunSummary` gained `expired, duplicates_grouped, ghost_flagged, reposts`; `store.finish_run` takes the same four; CLI ingest JSON line includes them.
- CLI: `ranker purge-source <name> --yes` (prints `{"purged": N}`; no `--yes` or unknown source exits 2), `ranker expire-ttl <name>` (prints `{"expired": N}`; non-ttl source exits 2).

## D-18 decisions

- The existing `dedupe_key` (company + title with seniority + city/remote) is the grouping key; junior and senior never merge. Grouping is across all sources (PARTITION BY dedupe_key only).
- Canonical = newest posted_at, then longer description_clean, then higher id. `group_sources` on the canonical row = sorted distinct sources of its active group, including itself (a singleton lists its own source); non-canonical rows get `{}`.
- Ghost-flagged jobs stay active; `ghost_reasons` is a signal for Phase 4. Thresholds are module constants.

## Thresholds (sources.yaml, documented in a header comment)

absence_missed_runs 2; volume_guard ratio 0.5, window 5, min_history 2 (cold start expires nothing); Adzuna ttl_days 45, unseen_days 30 (unseen rule only when the source had a succeeded run in the last 48 h or the current run succeeded).

## Behaviour notes

- Expiry runs only for `succeeded` runs. The total-run guard must pass, then each complete scope is guarded separately using `notes.scope_counts`. Decisions are stored in `ingest_runs.notes.guard` (`allowed`, `reason`, `normal`, plus `scopes`). An expiry error is caught, noted as `guard_error`, and expires nothing.
- Dedupe, repost detection and ghost refresh run for succeeded and partial runs over keys touched or expired in the run.
- purge sets rows to `removed`, blanks content, rewrites `dedupe_key` to `purged:<id>`, then recomputes dedupe for the old keys so another source's duplicate becomes canonical.

## Deviations from Plan

- [Rule 1 - Test adaptation] The plan expects an adapter 500 to give `partial`; real Remotive yields `failed` when nothing was fetched. The partial-run test uses a stub adapter with a failed scope. Either way nothing expires.
- Remotive items cannot have an empty URL (RawJob requires one), so `no_apply_url` is unit-tested in test_ghost.py. The pipeline test triggers ghost_flagged through `stale_60d`.
- `notes.guard` carries extra keys (`normal`, `scopes`) beyond `{allowed, reason}`.
- No pyproject/uv.lock changes.

## Verification

Full suite 747 passed (1 deselected llm), ruff and pyright clean.

## Commits

- 8dd070c feat(02-11): expiry policies with volume guard and cross-source dedupe
- 2a0ca4c feat(02-11): repost detection and ghost signals
- task 3 commit: feat(02-11): wire expiry, dedupe and ghost signals into runs; purge-source and expire-ttl CLI

## Self-Check: PASSED
