# Quick task: OpenRouter provider for the LLM gateway (2026-11-22)

Owner asked to run Phase 2 enrichment on free OpenRouter models until a Claude key exists.

## What changed (commit 3ba021d, merged in 485263b)
- `backend/ranker/llm/providers/openrouter.py`: async httpx provider behind the existing Provider protocol; strict `json_schema` response format from the Pydantic model; bad JSON or schema mismatch → `LLMOutputInvalid`; 429/5xx/transport retried twice (Retry-After capped at 10 s) → `LLMUnavailable`; 401/403 → new `LLMAuthError` (subclass of `LLMUnavailable`, not retried). Key only in the Authorization header; no key or prompt logging.
- Settings: `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY` (SecretStr), `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`; `.env.example` updated. No key → existing no-key deferral path.
- Cost: models ending in `:free` price at $0, so `llm_calls` rows are still written; budget/reservation logic unchanged.
- Tests: `backend/tests/test_llm_openrouter_provider.py` (respx; success, invalid JSON, schema mismatch, 429 + Retry-After, 5xx retry, transport error, 401, missing key, key canary, free pricing, wiring) plus an opt-in `@pytest.mark.llm` live test. Full suite 913 passed; ruff and pyright clean.

## Live validation (JobEnrichment schema, 2 synthetic texts per model)
| Model | Valid | Latency |
|---|---|---|
| nvidia/nemotron-3.5-super-vl:free | 2/2 | 3.4–4.4 s |
| baidu/ernie-5.5-flash-preview:free | 0/2 (429, schema-invalid) | 4.5–5.1 s |
| inclusionai/ling-3.2-flash:free | 0/2 (429) | 4.1–4.5 s |

Default model: `nvidia/nemotron-3.5-super-vl:free`. About 12 HTTP calls including retries.

## Caveats
- Schema-valid is not accurate: Nemotron labelled a "Berlin onsite, no visa sponsorship" job `remote_eligibility=india`. Enrichment only fills fields the rules left `unknown`; spot-check results before trusting them, and compare against the 02-17 eval.
- Free models may log prompts: use only for public job postings, never resumes or chat (Phase 4 uses a paid model).
- Free-tier rate limits (~20/min, low daily cap without credit) make this a slow trickle, not a backlog flush.
