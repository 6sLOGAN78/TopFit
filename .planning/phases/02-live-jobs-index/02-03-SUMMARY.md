---
phase: 02-live-jobs-index
plan: 03
subsystem: llm
tags: [llm, anthropic, budget, gateway, pydantic]
requires: [02-01, 02-02]
provides:
  - LLMGateway.structured() with llm_calls logging, monthly budget reservation, degradation
  - JobEnrichment typed schema plus schema lint
  - llm_calls and llm_enrichment_cache tables (migration 0003)
  - FakeProvider and AnthropicProvider
affects: [02-12 enrichment, later rerank/resume/agent phases]
tech-stack:
  added: []
  patterns: [provider Protocol + fake, advisory-lock budget reservation, config-driven price table]
key-files:
  created:
    - backend/migrations/versions/0003_llm.py
    - backend/config/llm_prices.yaml
    - backend/ranker/llm/errors.py
    - backend/ranker/llm/schemas.py
    - backend/ranker/llm/cost.py
    - backend/ranker/llm/client.py
    - backend/ranker/llm/providers/__init__.py
    - backend/ranker/llm/providers/fake.py
    - backend/ranker/llm/providers/anthropic.py
    - backend/tests/test_llm_schemas.py
    - backend/tests/test_llm_gateway.py
    - backend/tests/test_llm_anthropic_provider.py
  modified:
    - backend/ranker/llm/__init__.py
    - backend/ranker/settings.py
    - .env.example
key-decisions:
  - "Budget is enforced by reserving the worst-case cost in a pending llm_calls row under pg_advisory_xact_lock(727275); the row is finalised after the call"
  - "Failed calls that returned usage record real cost; timeouts and cancellation keep the reserved worst-case cost"
metrics:
  tasks: 3
  completed: 2026-11-22
---

# Phase 2 Plan 03: LLM Gateway Summary

Single LLM gateway (`ranker.llm.LLMGateway.structured`) that logs every attempt to `llm_calls` with no prompt or response text, enforces a $5 UTC-month budget via atomic reservation, degrades via `LLMUnavailable`/`BudgetExceeded`, and ships a fake and an Anthropic provider.

## Commits

- 85942ca contracts: errors, typed schemas with lint, price table, cost maths
- 0f9fc20 gateway, fake provider, migration 0003, settings, .env.example
- 87dae0f Anthropic provider and tests
- 93d7b62 fix: real cost on invalid output, worst-case on timeout/cancel, try/finally
- 80cc65a fix: advisory-lock budget reservation

## Installed SDK findings (Assumption A8)

Read from the installed anthropic 2.x source:
- `AsyncAnthropic(api_key=, max_retries=, timeout=)` confirmed. The SDK uses `httpx2`, so the timeout is `anthropic.Timeout(timeout_s, connect=5.0)`, not `httpx.Timeout`.
- `messages.parse(..., output_format=Model, output_config={"effort": "low"}, thinking=...)` returns a `ParsedMessage` with `parsed_output`.
- `usage` fields: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (the last two optional).
- Exceptions: `APIConnectionError` (and `APITimeoutError`), `APIStatusError` with `BadRequestError`, `RateLimitError`, `InternalServerError` subclasses. Thinking config types include `disabled` and `between_tools`.

## Deviations from Plan

1. [Rule 3] Anthropic timeout uses `anthropic.Timeout` instead of `httpx.Timeout` because the installed SDK is built on httpx2.
2. [Coordinator-requested fix 1, security review] Fail-open budget gap: when the provider returned but schema validation failed, the row used to record no cost. It now records the real cost from usage. Timeouts and cancellation record the worst-case estimate. Every attempt path writes a row (try/finally, shielded against cancellation). Tests added: invalid output cost, timeout cost, cancellation row. Commit 93d7b62.
3. [Coordinator-requested fix 2, security review] Budget race: replaced the read-then-call check with a reservation. One transaction takes `pg_advisory_xact_lock`, re-reads spend (including pending reservations), and inserts a pending row costing the worst case; after the call the row is updated to real cost and status. A crashed process leaves the worst-case cost counted, which is conservative. Test: two concurrent calls that each fit alone but not together, only one is allowed. Commit 80cc65a.
4. The Provider `parse` signature has no `effort`/`thinking` parameters (RESEARCH sketch had them); the Anthropic provider derives them from the model name (Haiku: thinking disabled; others: between_tools). Gateway `structured()` also takes `est_input_tokens` as the plan specified.
5. `llm_prices.yaml` omits the Haiku long-context tier (not verified from the pricing page); prompts here are far below 100k tokens. `cost.py` supports the tier if added.

## Test results

`uv run pytest -q`: 76 passed, 1 deselected (the opt-in `@pytest.mark.llm` live test). `ruff check`, `ruff format --check` and `pyright` clean.

## Known Stubs

None.

## Self-Check: PASSED

All created files exist and commits 85942ca, 0f9fc20, 87dae0f, 93d7b62, 80cc65a are in git history.
