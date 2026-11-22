# ADR 0006: Jev as a second query-parse model, via OpenRouter

Status: accepted, behind a flag (Phase 3, plan 03-05). Date: 2026-11-22.

## Context

D-06 adds a model fallback to query parsing (rules first, a model only for what the rules left unset). D-07 asks that Jev, TypeSafe's router, be offered as a second parse model next to Claude Haiku, selected per request, so plan 03-14 can compare the two with real numbers. Public claims about Jev were unverified, so adoption is gated on owner-confirmed access.

## S4 Jev access (owner reply, verbatim)

> use jev using openrouter no

The owner chose to reach Jev through OpenRouter with the existing `OPENROUTER_API_KEY`, not through the TypeSafe console. Recorded outcome:

- access: yes, via OpenRouter
- waitlist: n/a
- credit: the shared OpenRouter balance
- pricing: differs from the plan's assumption. OpenRouter lists `typesafe/jev-router` with pricing -1 (variable).
- model: `typesafe/jev-router`

Two live calls on 2026-11-22 with a strict `json_schema` query-parse prompt both returned valid JSON. One was routed to `openai/gpt-6-luna` at $0.0000246 (81 prompt and 33 completion tokens), the other to `deepseek/deepseek-v4.1-flash-1017` at $0.0000296. The router picks the underlying model per request, so it is not a single "Jev decision model", and a measurement of "Jev" is a measurement of whatever the router chose at the time.

## Decision

- `JevProvider` is a thin subclass of the OpenRouter provider (same HTTP, retry, strict JSON-schema parse, `reasoning: {enabled: false}`) with model `typesafe/jev-router`. There is no TypeSafe base URL or key and no `jev-1.x` model pin, so the earlier `jev-latest` rejection rule does not apply. The pin is the router id itself, kept in `JEV_MODEL`.
- It is off by default (`JEV_ENABLED=false`) and also needs `OPENROUTER_API_KEY`. `GET /api/meta` lists `jev` in `parse_models` only when both hold. A request for `parse_model=jev` while Jev is unavailable uses Haiku and reports `parse_source: rules+haiku`.
- Budgeting for variable prices: the gateway reserves a fixed `JEV_RESERVE_USD` (default 0.001) per call and settles at OpenRouter's `usage.cost`, or at the reserve when `usage.cost` is missing. A reply with unusable output is settled the same way. No per-token price for the router is added to `llm_prices.yaml`; every other unpriced model still fails closed.
- Logging: the `llm_calls.model` column holds `typesafe/jev-router>{routed model}` (the reply's `model` field), so the routed model is visible per call. `cost_usd` keeps six decimals, so a $0.0000246 call is stored as $0.000025.
- The fallback schema is enum-only (`QueryParseFallback`) and shared by both providers; the model sees only the unparsed residue, wrapped as untrusted data.

## Consequences

- Jev results are not reproducible: the router may choose a different model for the same query. Cached parses (keyed by normalised model input, provider and model id) pin the first answer.
- Spend per call is bounded by the reserve, but the actual routed price may exceed it. Settlement records the real figure, so the monthly cap still sees it, one call late at worst.
- Production keeps `JEV_ENABLED=false` until plan 03-14 decides.

## Measurement

Plan 03-14 records the D-17 comparison here: parse accuracy against the labelled query set, latency p50/p95, cost per call, and the distribution of routed models for Jev versus Haiku.
