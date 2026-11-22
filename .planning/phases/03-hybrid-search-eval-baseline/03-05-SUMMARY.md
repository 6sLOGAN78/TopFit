---
phase: 03-hybrid-search-eval-baseline
plan: 05
subsystem: search, llm
tags: [query-parse, jev, openrouter, gateway, budget]
requires: [03-02, 03-03]
provides:
  - per-request provider selection in LLMGateway (haiku, jev)
  - QueryParseFallback enum-only schema
  - gated, cached, time-boxed model fallback in ranker/search/parse.py
affects: [03-08, 03-14]
key-files:
  created:
    - backend/ranker/llm/providers/jev.py
    - backend/tests/test_llm_gateway_providers.py
    - backend/tests/test_llm_jev_provider.py
    - backend/tests/test_parse_fallback.py
    - docs/decisions/0006-jev-adoption.md
  modified:
    - backend/ranker/llm/client.py
    - backend/ranker/llm/schemas.py
    - backend/ranker/llm/providers/__init__.py
    - backend/ranker/llm/providers/openrouter.py
    - backend/ranker/search/parse.py
    - backend/ranker/settings.py
    - backend/ranker/observability.py
    - .env.example
metrics:
  completed: 2026-11-22
---

# Phase 3 Plan 05: Query-parse model fallback and Jev via OpenRouter Summary

Rules-first query parsing now has a model fallback that fills only still-unset filters, picked per request between Claude Haiku and Jev (`typesafe/jev-router` through OpenRouter). It is gated on the residue, cached, limited to 1.2 s, and any failure returns the rules-only parse with `degraded=true`.

## S4 Jev access

Owner reply, verbatim: "use jev using openrouter no"

- access = yes via OpenRouter
- waitlist = n/a
- credit = the shared OpenRouter balance
- pricing = differs: OpenRouter lists `typesafe/jev-router` with pricing -1 (variable)
- model = `typesafe/jev-router`

The orchestrator measured two live calls on 2026-11-22 (strict json_schema query-parse prompt). Both returned valid JSON. One was routed to `openai/gpt-6-luna` at $0.0000246 (81 prompt, 33 completion tokens), the other to `deepseek/deepseek-v4.1-flash-1017` at $0.0000296. The router picks the underlying model per request, so it is not a single decision model.

## Commits

- 485e57c: per-request providers, QueryParseFallback, JevProvider, settings, variable-price budgeting
- 89e1858: fallback in parse.py, tests, ADR 0006

## What changed

- `LLMGateway.structured(provider=...)`, `available(provider)`, `providers()`, `model_for_provider()`. `ExtraProvider(provider, model, reserve_usd)` registers extra providers; `build_gateway` adds `jev` only when `JEV_ENABLED` and `OPENROUTER_API_KEY` are set, so `/api/meta` `parse_models` lists jev only then.
- `JevProvider` subclasses `OpenRouterProvider`; it keeps `reasoning: {enabled: false}` and sends no temperature/top_p.
- Variable pricing: the gateway reserves `JEV_RESERVE_USD` (default 0.001) and settles at `usage.cost` (`ParsedResult.cost_usd`, new optional field, read by the OpenRouter provider), or at the reserve when missing. Invalid-output replies are settled the same way. Nothing was added to `llm_prices.yaml`; other unpriced models still fail closed (tested).
- Logging: `llm_calls.model` is `typesafe/jev-router>{routed model}` for Jev calls. The reservation row's model is updated at settlement.
- `QueryParseFallback`: six string properties, each a closed enum (cities from cities.yaml plus none/other, work mode, seniority, employment type, salary buckets plus none, role family). `lint_schema` is clean.
- Fallback gate: residue non-empty, some filter group still unset, and either a capitalised non-leading residue word, a trigger word (near, around, within, only, no, except, ...) in the query, or a number/currency token with no salary parsed. Only the residue (plus number tokens) is sent, inside `<query>` tags.
- Cache: `llm_enrichment_cache`, key sha256(normalised model input | provider | model), stores the enum answer only (no query text).
- `SALARY_BUCKETS_LPA` moved to `ranker/llm/schemas.py` and is re-exported from `ranker.search.parse`.

## Deviations from Plan

1. **[Owner decision] Jev through OpenRouter.** No `/v1/systemone` Choice-question client, no `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`, `jev-1.13.0/1.14.0` prices, or `jev-latest` validator. Settings are `JEV_ENABLED`, `JEV_MODEL`, `JEV_RESERVE_USD`, `PARSE_FALLBACK_TIMEOUT_SECONDS`. `REDACT_KEYS` gained `openrouter_api_key`, `q`, `query_text`.
2. **[Rule 1/2] Cache key uses the model input, not the whole query.** The model only sees the residue, so keying on it gives the same answer for the same input and a better hit rate.
3. **[Rule 1] Broad exception catch in the fallback.** The keyless fake gateway raises `LookupError` (not an `LLMError`) when it has no response; search must not fail, so any non-cancellation exception degrades.
4. **[Rule 1] Trigger words checked on the raw query.** "near" and similar are stopwords and never reach the residue.
5. **Cost precision.** `llm_calls.cost_usd` stores 6 decimals, so $0.0000246 is recorded as $0.000025. Left as is; widening the column needs a migration outside this plan.
6. Files `backend/ranker/api/*` were not changed: `meta.py` already read `gateway.providers()`. `docs/openapi.json` and the web client are unchanged (no API model changes; `openapi_export --check` is clean).

## Known Stubs

None.

## Verification

- `uv run pytest -q`: 1064 passed, 2 deselected (llm-marked). New tests: 9 gateway-provider, 9 Jev-provider/schema/settings, 14 parse-fallback.
- `ruff check`, `ruff format --check`, `pyright`: clean. `python -m ranker.openapi_export --check`: clean.
- No live network or LLM calls (respx and fake providers only).

## Threat Flags

None beyond the plan's threat model.
