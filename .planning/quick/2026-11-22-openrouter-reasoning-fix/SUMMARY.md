---
type: quick
slug: openrouter-reasoning-fix
status: complete
completed: 2026-11-22
commits: [856343e, 7fbd9b6]
---

# Quick: OpenRouter reasoning fix and billing for failed calls (summary)

OpenRouter requests now send `reasoning: {enabled: false}`, so Haiku 5.5 can no longer use up `max_tokens=100` on reasoning and return `content=None`. In prod, 9 of the first 12 enrichment calls failed this way; with the flag, a live test gave 4 valid replies out of 4.

When a call fails with invalid output but the reply still reports usage, the gateway now charges that usage. The charge is the larger of the cost priced from tokens and OpenRouter's `usage.cost`. Before this change those calls were recorded at $0, so the budget cap did not count them.

## Task 1 (856343e)
- `parse()` in `backend/ranker/llm/providers/openrouter.py` adds `"reasoning": {"enabled": False}` to the payload.
- A respx test checks the payload sent.

## Task 2 (7fbd9b6)
- `LLMOutputInvalid` carries optional fields: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cost_usd`.
- The OpenRouter provider reads usage before validating the content.
- The gateway's `_fail_billed` writes the `llm_calls` row and settles the budget at `max(token-priced cost, provider cost)`.
- The Anthropic and fake providers are unchanged.

## Verification
- `uv run pytest -q`: 920 passed, 2 deselected (llm-marked live tests).
- ruff, ruff format and pyright are clean on the touched files.

## Deferred
- The success path charges cached tokens twice (`prompt_tokens` already includes them). This is a small overcharge that predates this task.
- `usage.cost` is not used on successful calls.
