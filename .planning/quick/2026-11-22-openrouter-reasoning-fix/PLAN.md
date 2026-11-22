---
type: quick
slug: openrouter-reasoning-fix
created: 2026-11-22
---

# Quick: fix OpenRouter enrichment failures (reasoning eats max_tokens) and count failed-call cost

## Problem (verified live 2026-11-22)
- In prod, 9 of 12 `job_enrich` calls via OpenRouter `anthropic/claude-haiku-5.5` failed with `LLMOutputInvalid`.
- Cause: OpenRouter sometimes turns on reasoning for Haiku 5.5. With `max_tokens=100`, the reply is `finish_reason=length`, `reasoning_tokens=100`, `content=None`.
- Sending `"reasoning": {"enabled": false}` gave 4 of 4 valid replies, each with `reasoning_tokens: 0`.
- Failed calls are still billed (usage, plus `usage.cost` in USD), but the gateway logs them as `cost_usd 0` and token counts `null`. That means the monthly budget under-counts.

## Task 1: disable reasoning in the OpenRouter provider
- File: `backend/ranker/llm/providers/openrouter.py`.
- In `parse()` (and in any other request builder in that file), add `"reasoning": {"enabled": False}` to the payload.
- Add a respx test in `backend/tests/test_llm_openrouter_provider.py` that asserts the request JSON has `reasoning.enabled == false`.

## Task 2: account for billed usage on invalid output
- When the reply parses as JSON with `usage`, but content is None, malformed, or fails schema validation, attach the usage to the raised `LLMOutputInvalid`:
  - `input_tokens`, `output_tokens`, `cached`
  - `cost_usd` from `usage.cost` when present
- Use optional attributes on the exception (see `backend/ranker/llm/errors.py`), so existing callers keep working.
- In `backend/ranker/llm/client.py`, where the gateway settles a failed call, use those numbers for the `llm_calls` row and the budget settle.
  - Cost: priced from tokens via `llm_prices.yaml`, or OpenRouter's `usage.cost` when present, whichever is larger.
  - Today the gateway settles failed calls at 0.
- Do not change behaviour for the Anthropic or fake providers.
- Tests (respx and fake only, no live network): an invalid-output reply that carries usage produces an `llm_calls` row with `success=false`, nonzero tokens, nonzero `cost_usd`, and budget spend that includes it. Use the existing gateway test patterns (testcontainers DB if that is what they use).

## Done when
- `uv run pytest -q` passes in `backend/` (default run, no `llm` marker).
- `ruff check`, `ruff format --check` and `pyright` are clean on the touched files.
- Atomic commits, each ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `SUMMARY.md` is written in this directory.
- Do not push and do not touch the server. The orchestrator deploys.
