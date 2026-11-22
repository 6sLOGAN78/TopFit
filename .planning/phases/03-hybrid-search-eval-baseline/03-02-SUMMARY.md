---
phase: 03-hybrid-search-eval-baseline
plan: 02
subsystem: api-contract
tags: [openapi, hey-api, zod, schemathesis, scalar, fastapi]
requires: [03-01]
provides:
  - error envelope on every API error (ranker.api.errors)
  - docs/openapi.json plus `python -m ranker.openapi_export --check`
  - generated typed web client in web/src/lib/api
  - Scalar docs at /api/docs
  - CI schemathesis job
affects: [03-03, 03-06, 03-08]
tech-stack:
  added: [numpy, scalar-fastapi, schemathesis, "@hey-api/openapi-ts 0.99.0", zod, "@tanstack/react-query", vitest]
key-files:
  created:
    - backend/ranker/api/errors.py
    - backend/ranker/openapi_export.py
    - backend/tests/test_error_envelope.py
    - backend/tests/test_openapi_contract.py
    - docs/openapi.json
    - web/openapi-ts.config.ts
    - web/vitest.config.ts
    - web/src/lib/api/
  modified:
    - backend/ranker/main.py
    - backend/ranker/api/admin.py
    - backend/ranker/api/health.py
    - backend/tests/test_admin_ingest.py
    - .github/workflows/ci.yml
    - web/package.json
    - web/eslint.config.mjs
    - web/.prettierignore
requirements-completed: [OPS-10]
completed: 2026-11-22
---

# Phase 3 Plan 02: Typed API contract Summary

Pydantic models are the single source of truth: errors use one envelope, `docs/openapi.json` is regenerated and gated in CI, and a hey-api generated Zod + SDK + TanStack Query client is committed in `web/src/lib/api`.

## Package legitimacy gate
Task 1 was resolved by the orchestrator, which ran the read-only registry checks: numpy 2.5.5 (numpy/numpy, 153 releases), scalar-fastapi 1.10.1 (scalar/scalar, 26 releases), schemathesis 4.33.4 (schemathesis/schemathesis, 510 releases), @hey-api/openapi-ts 0.99.0 (hey-api/hey-api, created 2024-03-22, no install scripts), zod 4.6.7, @tanstack/react-query 5.108.2 and vitest 5.1.1 (no install scripts). Owner reply, verbatim: "approved". Packages were installed with the plan's pins.

## Commits
- 51ed35a feat(03-02): error envelope, Scalar docs, OpenAPI export with staleness check
- 9e32684 feat(03-02): hey-api generated web client and CI contract jobs

## Results
- Backend: 998 passed, 2 deselected (llm marker). ruff check, ruff format --check, pyright clean. `openapi_export --check` exits 0.
- Web: gen:api produces no diff, typecheck, lint, format:check, build pass; `npm test` passes with no test files (--passWithNoTests).
- Local Schemathesis dry run (uvicorn + pgvector PG18, fake embedder/LLM, 50 seeded jobs, schemathesis 4.33.4, the four CI checks): "415 generated, 415 passed", 6/6 operations tested. One warning: POST /api/admin/ingest/{source} repeatedly returned 404 (admin endpoint hidden when ADMIN_TOKEN is unset), so it is not fuzzed past auth.

## Decisions
- A2 was handled by post-processing: FastAPI keeps HTTPValidationError for 422 on routes that do not declare it, so `use_error_envelope_in_openapi` rewrites every 422 to ErrorResponse and removes HTTPValidationError/ValidationError from components.
- hey-api config from the plan (client-fetch, zod compatibilityVersion 4, sdk validator.response zod, tanstack react-query) worked unchanged on 0.99.0; no option-shape fix needed.

## Deviations from Plan
- [Rule 1 - Bug] `tests/test_admin_ingest.py` asserted the old `{"detail": "already_pending"}` 409 body; updated to the envelope (`error.code == "conflict"`). This is the intended behaviour change.
- [Rule 2] Added `HealthResponse` and `ReadyzResponse` models with `response_model` on healthz/readyz, and declared 409 on the admin route, so Schemathesis response conformance covers the 503 readyz body and 409.
- `RATE_LIMIT_ENABLED=false` from the plan's CI env was omitted because no such setting exists in `ranker/settings.py` (rate limiting arrives in a later plan). The CI job also sets `APP_ENV=test` and `COOKIE_SECURE=false`.
- The 429 `RateLimited` handler exists but no route raises it yet, so it is declared in OpenAPI only on routes that opt in (search already declares 429).
- `from scalar_fastapi import get_scalar_api_reference` carries a pyright ignore because the library's signature is partially unknown to strict pyright.

## Known Stubs
None.

## Threat Flags
None beyond the plan's threat model. T-03-05 (versioned Scalar CDN URL), T-03-06 (validation details drop input values, tested) and T-03-07 (Schemathesis not_a_server_error) are mitigated.

## Self-Check: PASSED
