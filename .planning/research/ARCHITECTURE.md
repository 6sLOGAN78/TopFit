# Architecture Research

**Domain:** Conversational job matching. An ingest pipeline feeds a Postgres+pgvector index. Hybrid retrieval and multi-stage ranking (with an LLM rerank) sit on top, then a tool-calling agent with memory, then a Next.js UI.
**Researched:** 2026-11-21
**Confidence:** HIGH for overall structure and component boundaries. MEDIUM-HIGH for the specific SPEC defects found (pgvector and Postgres behaviour was checked against official sources). MEDIUM for hosting and runtime memory figures.

---

## Verdict on SPEC.md §6–§11

The SPEC follows the standard structure that production search and recommendation systems converge on:

- an offline/async **ingest + index** plane;
- an online **retrieve, then score, then rerank, then diversify** funnel;
- a **feedback/logging** loop;
- an **orchestration** layer (here, an LLM agent) that calls the funnel as a tool.

Keep it. The component split (api / worker / db / web, with LLM and embeddings behind abstractions) fits a solo builder working to a deadline.

That said, review found **12 concrete defects or gaps** that should change the plan before coding starts. Four of them would quietly break the headline goals (recall, first-token latency, the p95 search target, and grounding). Details are in "SPEC Findings" below. Short list:

| # | Finding | Severity | Affects |
|---|---------|----------|---------|
| F1 | `hnsw.ef_search` defaults to 40, so the "vector top 100" leg returns at most ~40 rows. Filters cut that further. | **Critical** | SRCH-02/03, Recall@50 |
| F2 | The `base` CTE in §8.1 is referenced twice, so Postgres materializes it and the HNSW index is never used | **Critical** (silent) | SRCH-07, EMB-03 |
| F3 | The grounding post-check (§11.1 step 5) runs *after* tokens have already streamed (step 6 / AGENT-02) | **Critical** | AGENT-03, chat latency |
| F4 | An LLM query parse on the `/jobs/search` hot path cannot meet p95 < 300 ms | High | SRCH-07/08 |
| F5 | The rerank cache key omits the query, so different searches by the same user return wrong cached relevance | High | RANK-03 |
| F6 | Blending for ranks 21–50 (which get no rerank score) is undefined | Medium | RANK-01 |
| F7 | Feed double-counts feedback: `q = user_vector` (the Rocchio vector) and `f_feedback` measure the same thing | Medium | RANK-05, ablation validity |
| F8 | Skipped and saved jobs are excluded *after* rerank, which spends rerank budget on jobs that get thrown away | Medium | RANK-08, cost |
| F9 | The transport for async work (memory extraction, Rocchio recompute, summaries) is not specified | High | MEM-02, RANK-05 |
| F10 | When a canonical job expires, its duplicates are never promoted, so the job vanishes even though other sources still list it | Medium | ING-10 |
| F11 | The `english` tsvector config mangles exact tech tokens ("Go", "C++", "C#", ".NET"), which are the terms lexical search exists to catch | Medium | SRCH-01, D2 rationale |
| F12 | 2 uvicorn workers + worker process = 3 copies of the ONNX model, which risks OOM on free/low tiers. Deploying last (Phase 9) finds this too late. | High | OPS-02, timeline |

---

## Standard Architecture

### System Overview

```
┌──────────────────────────────── EXTERNAL ────────────────────────────────────┐
│  Remotive API     Adzuna API (in)     HN Algolia API      Anthropic API      │
└──────┬─────────────────┬──────────────────┬──────────────────────▲───────────┘
       │                 │                  │                      │
┌──────▼─────────────────▼──────────────────▼───────┐              │
│ WORKER PROCESS (APScheduler)                      │              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │              │
│  │ SourceAdapter│→ │ Normalize +  │→ │ Upsert / │ │              │
│  │  plugins     │  │ skills+dedupe│  │ embed /  │ │              │
│  └──────────────┘  └──────────────┘  │ expire   │ │              │
│                                      └────┬─────┘ │              │
│  ┌──────────────────────────────────┐     │       │              │
│  │ Task consumer (SKIP LOCKED):     │     │       │              │
│  │ memory_extract, rocchio, summary │     │       │              │
│  └──────────────┬───────────────────┘     │       │              │
└─────────────────┼─────────────────────────┼───────┘              │
                  │                         │                      │
┌─────────────────▼─────────────────────────▼──────────────────┐   │
│ POSTGRES 16 + pgvector  (single datastore)                   │   │
│  Index plane:  jobs (tsv GIN, HNSW partial), companies,      │   │
│                skills, ingest_runs, hn_extraction_cache      │   │
│  User plane:   users, user_profiles, user_memories,          │   │
│                conversations, messages                       │   │
│  Signal plane: search_logs, interactions, llm_calls          │   │
│  Cache/queue:  rerank_cache, query_parse_cache, tasks,       │   │
│                rate_limit buckets                            │   │
└─────────────────▲────────────────────────────────────────────┘   │
                  │ SQLAlchemy (async, psycopg3)                   │
┌─────────────────┴──────────────────────────────────────────────┐ │
│ API PROCESS (FastAPI)                                          │ │
│  ┌────────────┐   routers: auth, profile, jobs, feed,          │ │
│  │ HTTP layer │   interactions, chat(SSE), memory, admin       │ │
│  └─────┬──────┘                                                │ │
│        │ calls services (never HTTP self-calls)                │ │
│  ┌─────▼──────────────────────────────────────────────────┐    │ │
│  │ SERVICE LAYER                                          │    │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │    │ │
│  │  │ Agent loop   │─▶│ Tool registry│─▶│ SearchService│  │    │ │
│  │  │ + grounding  │  │ (user_id from│  │ (= ranking   │  │    │ │
│  │  │ + SSE        │  │  session)    │  │  pipeline)   │  │    │ │
│  │  └──────┬───────┘  └──────────────┘  └──────┬───────┘  │    │ │
│  │         │      ProfileService · MemoryService│         │    │ │
│  └─────────┼────────────────────────────────────┼─────────┘    │ │
│  ┌─────────▼────────────────────────────────────▼─────────┐    │ │
│  │ PLATFORM: LLM gateway (budget, timeout, breaker, log) ─┼────┼─┘
│  │           Embedder (fastembed, threadpool)             │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────────────▲──────────────────────────────────────────────┘
                  │ JSON + SSE (OpenAPI-generated TS client)
┌─────────────────┴──────────────────────────────────────────────┐
│ NEXT.JS WEB (App Router): search, onboarding, feed, chat,      │
│ job detail, saved, profile/memory, evals, admin                │
└────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────┐
        │ EVAL HARNESS (offline, same Python package)  │
        │ snapshot DB ← job_pool_snapshot.jsonl        │
        │ imports ranking pipeline with injected config│
        └──────────────────────────────────────────────┘
```

Two additions to SPEC §6.1 are shown above: an explicit **service layer** that both the agent tools and the HTTP routes call, and an explicit **Postgres `tasks` table** as the api→worker channel (F9).

### Component Responsibilities

| Component | Owns | Does NOT own | Typical implementation |
|-----------|------|--------------|------------------------|
| **SourceAdapter plugins** | Fetching and paging one source; mapping to `RawJob`; source rate limits | Normalisation, DB writes | `httpx.AsyncClient` + `tenacity`; one file per source |
| **Ingest pipeline** | Normalise → skills → hash → dedupe key → upsert → embed changed → expire → canonical recompute → run record | Fetching specifics | Pure functions + one set-based SQL phase per step |
| **Embedder** | Loading the model once; `passage_embed` (jobs) vs `query_embed` (queries/profiles); batching | Choosing what text to embed | fastembed `TextEmbedding`, called through `run_in_threadpool` so ONNX doesn't block the event loop |
| **Retrieval (search/)** | Hard filters, lexical leg, vector leg, RRF, exclusions (skipped/saved) | Scoring beyond RRF | Raw SQL via SQLAlchemy `text()`, one round trip |
| **Ranking (ranking/)** | Features, weighted score, rerank orchestration, blend, MMR, company cap, explanations | SQL, HTTP | Pure Python over a `Candidate` list. Config is *injected*, not read globally |
| **LLM gateway (llm/)** | Provider calls, structured output, tool calls, streaming, timeouts, budget check, circuit breaker, `llm_calls` logging, fake provider for tests | Prompts (callers own prompts) | `complete()`, `structured(schema)`, `run_tools()`; raises typed `LLMUnavailable` |
| **Agent (agent/)** | Context assembly, tool loop (≤ 6 rounds), grounded streaming, SSE events, persisting messages | Search logic (calls SearchService) | async generator → `StreamingResponse` / `EventSourceResponse` |
| **Memory (memory/)** | Extracting memory ops, applying supersession, preference patch merge, summary compression | Deciding ranking penalties (ranking reads structured memories) | Worker task consuming `tasks` rows |
| **Profiles (profiles/)** | Text extraction in a sandboxed subprocess, `CandidateProfile` parse + retry, canonicalisation, profile embedding, `profile_hash` | Storing files (never) | `subprocess` with timeout; Pydantic validation |
| **Signals (logging)** | `search_logs`, `interactions`, `impressions` | Interpretation (eval/admin read these) | Insert-only tables |
| **Eval harness (evals/)** | Snapshot loading, pooling, labeling workflow, metrics, ablation, CI gate | Production code paths. It *imports* them and must not re-implement them | CLI scripts on a separate DB |
| **Web** | Presentation, SSE parsing, impression tracking, optimistic save/skip | Ranking logic | Next.js App Router, generated client |

---

## SPEC Findings (detail and fixes)

### F1: Vector leg is silently capped at ~40 rows (CRITICAL)
pgvector's HNSW scan returns at most `hnsw.ef_search` candidates (default **40**), and filters are applied *after* the index scan. The README's example: a filter matching 10% of rows yields about 4 results with defaults. So `LIMIT 100` in the vector CTE returns ≤ 40 rows before filtering, and far fewer with city/remote/seniority filters. Recall@50 is then capped by construction.
**Fix:** In the retrieval transaction, run `SET LOCAL hnsw.ef_search = 200` (≥ 2× vector_k), plus `SET LOCAL hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8.0; latest is 0.8.7). Keep `hnsw.max_scan_tuples` at its default of 20,000 unless measurements say otherwise. Add a test asserting the vector leg returns `vector_k` rows on the fixture DB with a 10%-selective filter. **Verify the managed host's pgvector version at Phase 0, not Phase 9.** Neon's docs don't state which version it currently ships.

### F2: CTE materialization defeats the HNSW index (CRITICAL, silent)
Since Postgres 12, a CTE referenced more than once is **materialized** by default. In §8.1, `base` feeds both `lex` and `vec`, so Postgres computes `SELECT j.*` for every filtered active job (including `description_raw` and the embeddings), then sorts by distance in memory. That is an exact scan. Results stay correct and at 10k rows it may even be fast enough, so the problem stays hidden until the index grows or the instance is small.
**Fix:** Drop the shared `base` CTE. Write each leg directly against `jobs` with the filter predicate repeated, and use `WHERE status='active' AND canonical_job_id IS NULL` *literally*, because the partial HNSW index is only usable when the query implies its predicate. Use `ORDER BY embedding <=> :qv LIMIT :k` with no expression wrapping (pgvector only uses the index for `ORDER BY <distance-op> ASC LIMIT n`). Select only `id` in each leg. Generate the filter SQL fragment once in Python and splice it into both legs as bound parameters. Then confirm with `EXPLAIN (ANALYZE, BUFFERS)` that the plan shows an `Index Scan using jobs_embedding_hnsw`. Also note that at 10k–50k rows with highly selective filters, an exact scan can legitimately beat HNSW. Measure both and record an ADR (SPEC already asks for this).

### F3: Streaming and the grounding check are in conflict (CRITICAL)
§11.1 checks grounding after the full reply is produced, but AGENT-02 and the "first token p95 < 2.5 s" target need tokens streamed as they arrive. Once a hallucinated job title has streamed to the user, nothing can be repaired.
**Fix (standard pattern, "cards from tools, text from model"):**
1. Job cards (the `jobs` SSE event) are built **only** from tool results on the server, never from model text.
2. The system prompt requires the model to refer to jobs only by marker, e.g. `[[job:123]]`, never by free-text title/company.
3. A streaming filter in `agent/grounding.py` buffers only while inside a `[[…]]` marker and checks each ID against the conversation's allowed set (the IDs returned by tools so far, persisted in `messages.tool_calls`). Unknown IDs are dropped and counted.
4. The frontend renders markers as inline chips linked to cards.
5. The post-hoc check (SPEC step 5) stays as an **audit metric** and an eval assertion, not as a repair step on the live path. A repair call is only acceptable for non-streamed contexts (evals).

This keeps "0 hallucinated IDs" enforceable *and* keeps streaming. A residual risk remains: the model could still name a company in prose. The faithfulness judge covers that.

### F4: LLM query parse on the search hot path (HIGH)
A fast-model call costs ~400–1500 ms, which is incompatible with p95 < 300 ms (SRCH-07).
**Fix:** `query_parse` is **rules first, synchronously** (cities.yaml, salary regex such as `10L+`/`LPA`, remote keywords, seniority words). The LLM is used only when the rules leave unparsed residue that looks like a constraint, and the result is cached in a `query_parse_cache` table keyed by normalised query. Measure p95 with the LLM path excluded, as SRCH-07 says. **In chat, skip query parsing entirely**: the agent's `search_jobs(query, filters)` tool arguments already *are* the structured parse, and that's what the agent eval's filter-extraction metric measures.

### F5: Rerank cache key is missing the query (HIGH)
`(profile_hash, job_id, content_hash, prompt_version)` is right for the feed, but for `/jobs/search?q=…` the relevance depends on the query too ("ML intern" and "backend" against the same job get different grades). Guests may have no profile at all.
**Fix:** The key becomes `(context_hash, job_id, content_hash, prompt_version)` where `context_hash = sha256(profile_hash | normalised_query | hard_filters)`. Rerank in **parallel chunks** (e.g. 4 calls × 5 jobs) and send **only cache misses**, so a partial hit costs less and latency is bounded by the slowest chunk rather than one 20-item generation.

### F6: Blend is undefined for ranks 21–50
**Fix:** Apply the blend `α·r/3 + (1−α)·s_norm` within the reranked top 20 only, then append feature-ranked 21–50 below them. Then run MMR + company cap. Document this in `ranking/pipeline.py`. In degraded mode, the order is simply feature-ranked.

### F7: Feedback double-counted in the feed
§10.1 step 2 uses `q = user_vector` (the Rocchio vector from §10.4) for retrieval and `f_sem`, and `f_feedback = cos(feedback_vector, job)` repeats the same signal. That inflates the effective weight of feedback and muddies the "+ personalisation" ablation row.
**Fix:** Retrieval and `f_sem` use `u₀` (profile embedding, or the query embedding when there is a query). Feedback enters **only** through `f_feedback`, so the ablation toggles exactly one thing. Optionally add a third vector-retrieval leg on the feedback vector for the feed (RRF handles three lists natively). Defer that to post-MVP.

### F8: Exclusions applied too late
**Fix:** Push "skipped by this user" (and "saved", for the feed) into retrieval SQL as an anti-join (`AND NOT EXISTS (SELECT 1 FROM interactions i WHERE i.user_id=:uid AND i.job_id=j.id AND i.action IN (...))`), using the existing `interactions_user_action` index. The rerank budget then goes only to jobs that can actually be shown.

### F9: No defined channel for async work (HIGH)
"Enqueue async" appears in §11.1 step 8 and §10.4 (debounced Rocchio), but nothing connects api to worker. FastAPI `BackgroundTasks` and bare `asyncio.create_task` lose work on deploy restarts and duplicate it across uvicorn workers.
**Fix:** A `tasks` table (`id, kind, user_id, payload jsonb, run_after timestamptz, status, attempts, dedupe_key unique-where-pending`) consumed by the worker with `SELECT … FOR UPDATE SKIP LOCKED`. The unique `dedupe_key` (e.g. `rocchio:<user_id>`) together with `run_after = now()+30s` gives the debounce for free. This keeps the "single datastore" constraint (no Redis) and makes background work observable on the admin page. About 100 lines of code. `procrastinate` (a Postgres-based task queue) is the off-the-shelf alternative; the stack researcher should decide.

### F10: Canonical promotion on expiry
§7.5 picks a canonical per `dedupe_key`, but if the canonical (e.g. the Adzuna copy) expires while the Remotive copy stays active, the remaining copy keeps `canonical_job_id` pointing at an expired row and stays hidden.
**Fix:** End every ingest run with a set-based recompute over affected `dedupe_key`s: among active rows, choose canonical by (has salary, description length, posted_at). Set `canonical_job_id = NULL` on the winner and point the others at it. Add an integration test: expire the canonical, and the duplicate becomes visible.

### F11: `english` tsvector config and tech tokens
Stemming and stop-word handling in the `english` config hurt exactly the terms D2 cites: "Go" can collide with the common word "go", and "C++"/"C#"/".NET" lose their punctuation in the parser. The lexical leg then fails at the job it was added for.
**Fix:** Add the canonical skills array to the tsvector using the `simple` config at weight A, e.g. `setweight(to_tsvector('simple', array_to_string(skills,' ')),'A')`. Generated columns can't call non-immutable functions, so write a small immutable SQL wrapper or maintain `skills_text` at ingest. Map query tokens through the skills taxonomy before building the tsquery (`golang`/`go` → `go`; `c++` → `cpp`), using the same alias table for both. Add a lexical eval case for each of these tokens. **Confidence: MEDIUM.** The exact tokenisation should be confirmed with `SELECT to_tsvector('english','C++ Go C# .NET developer')` at Phase 3.

### F12: Model memory multiplied, and deployment found too late (HIGH)
The bge-small ONNX model plus runtime is roughly 150–300 MB RSS per process (SPEC's own estimate, MEDIUM confidence). SPEC §6.2 runs the api with 2 uvicorn workers plus a worker process: three copies, which won't fit a 512 MB tier. With deployment in Phase 9, this surfaces at the worst moment.
**Fix:** Run **1 async uvicorn worker** per instance (the work is I/O-bound apart from embedding, which goes to a threadpool; onnxruntime releases the GIL). Deploy a walking skeleton (api `/readyz` with the model loaded, plus db with pgvector) to the chosen host **in Phase 0**, and resolve OPEN-1 then. Run the worker as a separate service only if memory allows. Otherwise use platform cron calling `ranker ingest <source>` (a short-lived process).

### Smaller notes (not blocking)
- **Adzuna expiry vs pagination:** Adzuna queries are bounded by `max_pages_per_query`, so a still-open job can drop out of the fetched window and be expired by the "missed 2 runs" rule. Consider an age-based rule for sources with partial coverage (e.g. not seen for 7 days, or past Adzuna's `created` + N days). Raise it in PITFALLS.
- **Memory race:** Extraction is async, so a preference stated in turn N may not be in long-term memory by turn N+1. That's fine, because short-term context still holds the raw message. The "conversation A → B" test must wait for the task to finish (poll the `tasks` status).
- **`profile_hash` scope:** Memories with ranking effect (penalties) apply in features, not in rerank, so they correctly stay out of the rerank cache key. Keep it that way, or every memory change will invalidate the cache.
- **Eval snapshot completeness:** `job_pool_snapshot.jsonl` must include `embedding`, `embedding_model`, `skills`, and the company `industry/stage`, or a re-embed/model change silently changes "frozen" results.
- **Pool bias:** Phase 3 pools from lexical/vector/hybrid only. Phase 5's full pipeline will surface unlabeled jobs. Plan an incremental re-pool and label pass in Phase 5, and treat unlabeled jobs as grade 0 only with a stated caveat.
- **`search_logs.results` JSONB size:** At 50 results × 10 features per search, rows are a few KB each. Fine at launch scale. Add 90-day retention for guests as SPEC already says.

---

## Recommended Project Structure

SPEC §16 is sound. Changes, marked `(+)`:

```
backend/ranker/
├── main.py, settings.py, cli.py, worker.py
├── db/                    # engine, session, models
├── api/                   # thin routers: parse request → call service → shape response
├── services/        (+)   # SearchService, FeedService, ProfileService, InteractionService
│                          #   the ONLY entry points used by routers AND agent tools
├── ingest/
│   ├── sources/{base,remotive,adzuna,hn}.py
│   ├── normalize/{html,location,salary,seniority,company}.py
│   ├── skills.py, dedupe.py (+ canonical recompute), expiry.py (+), pipeline.py
├── embeddings/            # Embedder protocol + fastembed impl (passage vs query embed)
├── search/
│   ├── query_parse.py     # rules first; LLM fallback + cache
│   ├── filters.py         # SearchFilters → SQL fragment + params (single source)
│   └── retrieval.py       # lexical, vector, hybrid SQL; SET LOCAL ef_search/iterative_scan
├── ranking/
│   ├── types.py     (+)   # Candidate, RankingConfig, RankedResult (dataclasses)
│   ├── features.py, scorer.py, rerank.py, blend.py (+), diversity.py,
│   ├── personalization.py, explain.py (+)
│   └── pipeline.py        # rank(candidates, ctx, config, llm) -> RankedResult; no DB access
├── profiles/, memory/
├── agent/
│   ├── loop.py, tools.py, context.py (+), grounding.py (streaming marker filter), sse.py
│   └── prompts/*.md       # versioned; prompt_version = file hash
├── llm/
│   ├── client.py, providers/{anthropic,fake}.py (+fake), budget.py (+), cost.py, logging.py
├── tasks/           (+)   # Postgres SKIP LOCKED queue: enqueue(), consume(), handlers registry
├── ratelimit.py, observability.py
```

### Structure Rationale

- **services/:** Without this layer, agent tools either call HTTP routes (slow, loses session context) or duplicate route logic. Both are common in agent apps and both rot. A tool is then a 10-line adapter: validate the args, inject `user_id` from the session context, call the service, trim the payload.
- **ranking/ has no DB access:** The eval harness, the weight tuner and the ablation all call `rank()` with an injected `RankingConfig` and a precomputed candidate list. That is what makes the ablation table cheap to produce and the CI gate deterministic.
- **search/filters.py as single source:** The same `SearchFilters` object feeds the lexical leg, the vector leg, the agent tool schema (JSON schema exported from Pydantic), and the eval scenario assertions.
- **llm/providers/fake.py:** SPEC §19 needs this for offline agent tests. Build it together with the real provider, not afterwards.

---

## Architectural Patterns

### Pattern 1: Retrieval funnel with shrinking candidate sets
**What:** Cheap, high-recall stages over many items; expensive, high-precision stages over few. Here: SQL (10k → 100) → features (100 → 50) → LLM (20) → MMR (N).
**When to use:** Always, in search and recommendation. It is the canonical industry structure (candidate generation → ranking → re-ranking/policy).
**Trade-offs:** Each stage's recall caps the next stage's quality. Measure Recall@50 at retrieval separately (SPEC does this) so ranking regressions aren't blamed on retrieval or the other way round.

### Pattern 2: One pipeline, many callers (config-injected)
**What:** `/jobs/search`, `/recommendations`, the agent's `search_jobs`/`get_recommendations` tools, `tune_weights.py` and `ranking_eval.py` all call the same `SearchService.run(ctx, query, filters, config)`. Only the config and the input differ.
```python
@dataclass(frozen=True)
class RankingConfig:
    weights: dict[str, float]
    retrieval: RetrievalCfg
    rerank: RerankCfg            # enabled=False for --no-llm / degraded
    diversity: DiversityCfg
    def version(self) -> str: ...  # hash → search_logs.pipeline_version

async def run(ctx: UserCtx, q: str | None, f: SearchFilters,
              cfg: RankingConfig, llm: LLMGateway) -> RankedResult: ...
```
**Trade-offs:** Requires discipline (no `load_yaml()` deep inside features). The payoff is that ablation rows become config permutations, not code branches.

### Pattern 3: Versioned cache-aside for LLM outputs
**What:** Every LLM-derived artefact (rerank, HN extraction, query parse, resume parse) is keyed by *content hash + prompt version + model*. A prompt edit invalidates the cache automatically. Nothing is TTL-based.
**Trade-offs:** Prompt churn during development costs re-calls. Accept it. Add `model` to keys if model IDs change in env.

### Pattern 4: LLM gateway as the degradation boundary
**What:** All LLM calls go through one gateway, which enforces the budget check (sum of `llm_calls` for the month, cached for 60 s), a per-call timeout, a simple circuit breaker (e.g. 5 failures in 60 s → open for 60 s), and logging. Callers catch one `LLMUnavailable` type and choose their fallback: rerank → feature order + `degraded:true`; chat → `error` SSE event + plain-search offer; query parse → rules only.
**Trade-offs:** The breaker adds a bit of state (in-process is fine with 1 worker per instance). It stops every request from paying the 6 s timeout during an outage, which matters for the "LLM failure never breaks search" goal.

### Pattern 5: Grounded streaming ("cards from tools, text from model")
See F3. The UI's source of truth for jobs is structured tool output. Model text can only reference IDs, and the server filters those as it streams.

### Pattern 6: Postgres as queue, cache, rate limiter and log
**What:** `tasks` (SKIP LOCKED), `rerank_cache`, `query_parse_cache`, token-bucket rows, and append-only `search_logs`/`interactions`/`llm_calls`.
**When to use:** Single-node scale (≤ 1k users, ~20 rps), which is exactly this project.
**Trade-offs:** Extra write load on one DB. At this scale it's negligible. It shows SQL skill, which is a stated hiring signal.

### Pattern 7: Explicit beats implicit in personalisation
**What:** Hard filters (explicit) apply in SQL. Structured preferences and penalties apply as features. Implicit feedback (Rocchio) only reorders. The agent turns stated preferences into `update_preferences` patches (structured) *and* free-text memories (nuance). SPEC already gets this right (D5, §10.4). Keep it.

---

## Data Flow

### 1. Ingest flow (worker, scheduled per source)
```
APScheduler tick ─▶ ingest_runs(running)
  ─▶ adapter.fetch() ─(RawJob stream)─▶ [HN: LLM extract via gateway, cached]
  ─▶ normalize ─▶ skills ─▶ content_hash, dedupe_key
  ─▶ UPSERT (source, source_job_id)   new/changed → needs_embedding
  ─▶ embed batch(64) via Embedder.passage_embed
  ─▶ zero-fetch guard? ─no─▶ expire stale (missed_runs≥2)
  ─▶ recompute canonical per touched dedupe_key            (F10)
  ─▶ ingest_runs(succeeded|partial|failed, counts)
```
Direction: external → worker → DB. Nothing in the API writes jobs except the admin trigger, which enqueues a `tasks` row or invokes the CLI.

### 2. Search / feed request flow (api, synchronous, target p95 < 300 ms without rerank)
```
GET /jobs/search ─▶ rate limit ─▶ session→UserCtx (profile, prefs, structured memories)
  ─▶ query_parse (rules; LLM only on residue, cached)               (F4)
  ─▶ merge filters (explicit query wins over stored prefs)
  ─▶ Embedder.query_embed(q)  | feed: u₀ = profile embedding        (F7)
  ─▶ ONE SQL round trip: lexical ∥ vector (ef_search, iterative) ─▶ RRF
        + anti-join skipped/saved                                   (F8)
  ─▶ load candidate rows (id, title, skills, salary, embedding…)
  ─▶ features ─▶ weighted score ─▶ top 50
  ─▶ [rerank top 20: cache lookup ─▶ parallel chunked misses ─▶ timeout 6s]
        on LLMUnavailable → degraded=true                           (F5, F6)
  ─▶ blend top 20 ─▶ append 21–50 ─▶ MMR + company cap ─▶ top N
  ─▶ explanations (LLM reasons or rule-based)
  ─▶ INSERT search_logs ─▶ JSON {items, next_cursor, search_log_id, degraded}
```
**Pagination note:** For cursor pagination over a reranked and diversified list, cache the final ranked ID list per `search_log_id` (in `search_logs.results`) and page through it. Don't re-run the pipeline per page, or ordering will shift between pages.

### 3. Chat turn flow (api, streaming)
```
POST /chat ─▶ persist user msg
  ─▶ context: system prompt + profile summary + prefs + ≤30 memories
              + conversation summary + recent msgs (≤6k tokens)
  ─▶ gateway.run_tools(stream=True) ⇄ tool registry ─▶ services (search, details…)
        tool_status SSE ─▶ client; tool results add IDs to allowed set
  ─▶ token stream ─▶ grounding marker filter ─▶ token SSE            (F3)
  ─▶ jobs SSE (cards built from tool results only)
  ─▶ persist assistant msg (cited_job_ids, tool_calls)
  ─▶ enqueue tasks: memory_extract, maybe summarize                  (F9)
  ─▶ done SSE {message_id, degraded}
```

### 4. Feedback loop (async)
```
UI save/skip ─▶ POST /interactions ─▶ INSERT interactions
  ─▶ enqueue tasks(kind=rocchio, dedupe_key=user, run_after=+30s)
worker ─▶ recompute feedback_vector ─▶ UPDATE user_profiles
next feed ─▶ f_feedback reflects it; skipped jobs excluded in SQL
```

### 5. Memory loop (async)
```
worker ─▶ memory_extract(task) ─▶ gateway.structured(MemoryExtraction)
  ─▶ apply ops (add/update→supersede/delete) ─▶ user_memories
  ─▶ merge preference_patch ─▶ user_profiles.preferences, recompute profile_hash
next turn/session ─▶ context assembly reads active memories; ranking reads structured ones
```

### 6. Evaluation flow (offline)
```
prod DB ─snapshot_job_pool.py─▶ job_pool_snapshot.jsonl (with embeddings)
  ─▶ load into eval DB ─▶ for each persona × config: SearchService.run(...)
  ─▶ metrics vs judgments.jsonl ─▶ results/<ts>_<sha>.json ─▶ /evals page, CI gate
```
The eval harness reads **production code** with a different DB URL and an injected config. It must not contain its own retrieval or ranking implementation.

### State Management (frontend)
Server state through the generated client plus a fetch cache (React Query or plain RSC fetching, which the stack researcher will decide). Chat state is a local reducer over SSE events (`tool_status | token | jobs | done | error`). Save/skip are optimistic updates that reconcile on response. Impressions are batched with IntersectionObserver and sent with `search_log_id`.

---

## Build Order (dependency graph)

```
                 ┌────────────────────────────┐
                 │ P0 Foundations             │
                 │ + walking-skeleton DEPLOY  │ (F12: retire OPEN-1 + RAM risk now)
                 └─────────────┬──────────────┘
                               ▼
                 ┌────────────────────────────┐
                 │ P1 Schema + Remotive       │
                 └─────────────┬──────────────┘
                               ▼
     ┌─────────────────────────────────────────────────────┐
     │ P2 Normalise, skills, Adzuna, expiry, dedupe        │
     │   + LLM gateway v1 (budget, log, fake provider)     │ ← build once, used by P2(HN)/P4/P5/P6
     │   + tasks queue (tiny; needed by P5/P6)             │
     └─────────────┬───────────────────────────────────────┘
                   ▼
     ┌─────────────────────────────────────────────┐
     │ P3 Embeddings + hybrid retrieval + EVAL     │ ← F1/F2/F11 verified here w/ EXPLAIN
     │    harness skeleton + CI gate               │
     └──────┬───────────────────────────┬──────────┘
            ▼                           ▼
 ┌────────────────────┐      ┌───────────────────────────┐
 │ P4 Profiles+resume │      │ P7a Frontend: search page │ (real API exists now; no mocks)
 │    + guest session │      │     + job card + detail   │
 └─────────┬──────────┘      └────────────┬──────────────┘
           ▼                              │
 ┌──────────────────────────────┐         │
 │ P5 Ranking + personalisation │         │
 │  (services/ layer, rerank,   │         │
 │   ablation)                  │         │
 └─────────┬────────────────────┘         │
           ▼                              ▼
 ┌──────────────────────────────┐  ┌──────────────────────────┐
 │ P6 Agent + memory            │─▶│ P7b Frontend: onboarding,│
 │  (tools call services/)      │  │  feed, chat SSE, saved   │
 └─────────┬────────────────────┘  └────────────┬─────────────┘
           └──────────────┬───────────────────────┘
                          ▼
              P8 Hardening ─▶ P9 Prod ops (scheduler, Sentry, dashboard) ─▶ P10 Launch
```

**Build-order implications for the roadmap:**

1. **Deploy in Phase 0, not Phase 9.** A walking skeleton (FastAPI `/readyz` with fastembed loaded, plus Postgres with `vector` extension version checked) on the target host resolves OPEN-1, model-memory fit (F12), cold-start behaviour, and the pgvector ≥ 0.8 requirement (F1) while they're still cheap to fix. Phase 9 then becomes "turn on the scheduler + observability", not "first contact with prod". For an MVP on a ~10-day clock, this is the single highest-leverage reorder.
2. **The LLM gateway is foundational and built once.** It is a prerequisite for HN extraction (P2), resume parsing (P4), rerank (P5) and the agent (P6). Since the MVP drops HN, the gateway would otherwise appear first in P4 in a minimal form and be rebuilt in P5/P6. Build budget, logging, timeout, breaker and the fake provider in one go, in P2 or at the start of P4.
3. **Retrieval correctness gates everything downstream.** F1/F2/F11 must be verified in P3 with `EXPLAIN` and a recall assertion *before* ranking work, because ranking and rerank cannot recover candidates that retrieval never returned.
4. **The eval harness belongs with retrieval (P3), as SPEC says.** Keep it there. Every later stage then lands with a number. Design `rank()` as config-injected from day one, or the ablation will force a refactor in P5.
5. **The services/ layer should exist by the end of P5.** P6 tools are thin adapters over it. If P5 puts logic in routers, P6 duplicates it.
6. **Frontend can start at P3**, against the *real* search endpoint (landing + job cards + detail). Only chat needs to wait for P6. That overlap is what makes the 10–12 day MVP plausible for a solo builder.
7. **Accounts (email/password) are genuinely late-bindable.** The guest session (P4) is the identity primitive. Registration just flips `is_guest`, so the user-plane tables don't depend on P8.
8. **The tasks queue is tiny but blocks two features** (memory extraction, Rocchio). Put it in P2/P3 infrastructure, not inside P6.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Launch (≤ 1k users, 10–50k jobs, ~20 rps) | The SPEC design as-is: one API instance with 1 uvicorn worker, one worker or cron, managed Postgres. An exact vector scan may even be competitive with HNSW. |
| 1k–100k users | Add API instances (stateless; the circuit breaker stays per-instance). Move rate limiting to Postgres buckets (OPEN-3 → Postgres, because in-memory breaks across instances). Partition `search_logs`/`interactions` by month. Add a read replica for eval/admin queries. |
| 100k+ users / 1M+ jobs | Dedicated vector/search engine or ParadeDB BM25. Precompute feed candidates offline per user. A learned ranker (v2 LightGBM) on accumulated logs. Hosted embeddings. |

### Scaling Priorities
1. **First bottleneck: LLM latency and cost**, not the DB. A cold rerank is ~1–4 s per search, and chat dominates spend. Mitigations: chunked parallel rerank with partial-hit caching (F5), budget tiers, fast model for bulk work.
2. **Second: API memory and CPU from the in-process embedder** (F12). Embedding a query is cheap, but each process holds a model copy. Scale by instance, not by uvicorn workers, or move to hosted embeddings (EMB-06).
3. **Third: write amplification on `search_logs`** (JSONB with per-result features). Add retention and partitioning before 1M rows.

---

## Anti-Patterns

### Anti-Pattern 1: Agent tools calling your own HTTP API
**What people do:** The `search_jobs` tool does `httpx.get("/api/v1/jobs/search")`.
**Why it's wrong:** Doubles latency, loses typed results, and risks taking `user_id` from model arguments (an IDOR vector).
**Do this instead:** Tools call `services/` directly with `UserCtx` from the session (SPEC §11.2 already forbids model-supplied `user_id`).

### Anti-Pattern 2: Letting model text be the source of job data
**What people do:** Render job listings by parsing the assistant's markdown.
**Why it's wrong:** Hallucinated titles, salaries and links reach the user. A post-hoc check is too late once tokens have streamed.
**Do this instead:** Build structured cards from tool results and have the text reference job-ID markers (F3).

### Anti-Pattern 3: Eval code that re-implements the pipeline
**What people do:** `ranking_eval.py` contains its own scoring loop "for speed".
**Why it's wrong:** The eval measures something other than production, and the CI gate becomes theatre.
**Do this instead:** The eval imports `SearchService`/`rank()` with injected config and a snapshot DB.

### Anti-Pattern 4: Synchronous LLM calls on the default search path
**What people do:** Always parse the query and rerank before returning.
**Why it's wrong:** p95 becomes LLM p95. Outages become search outages.
**Do this instead:** Rules-first parsing, rerank with a hard timeout and cache, the gateway breaker, and the `degraded` flag. Consider streaming search results: return feature-ranked results fast, then a rerank refinement (UI swaps reasons in). That one is post-MVP.

### Anti-Pattern 5: Shared filter CTEs and wrapped distance expressions in vector SQL
**What people do:** Factor filters into a CTE used by both legs, or `ORDER BY 1 - (embedding <=> q) DESC`.
**Why it's wrong:** A CTE referenced twice materializes (index lost). Expression ordering bypasses the index (pgvector requires `ORDER BY <op> ASC LIMIT`).
**Do this instead:** Write each leg directly against `jobs` and convert distance to similarity *after* the limit (F2).

### Anti-Pattern 6: Hard-deleting or overwriting jobs
**What people do:** Delete expired jobs, or replace rows on re-ingest.
**Why it's wrong:** Breaks `interactions`/`search_logs` references and eval reproducibility.
**Do this instead:** Use status transitions plus `content_hash` change detection (SPEC already does this; keep it).

### Anti-Pattern 7: Feedback learning that overrides explicit constraints
**What people do:** Let a strong Rocchio vector pull in onsite jobs for a "remote only" user.
**Why it's wrong:** Users read it as the system ignoring them, which is the top complaint about recommenders.
**Do this instead:** Explicit filters in SQL, feedback only in ordering (SPEC §10.4, keep it).

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Remotive | Polled REST, whole feed | Attribution and link-back required. Check the terms on how often polling is allowed (SPEC says verify; some sources ask for very infrequent polling). |
| Adzuna (in) | Polled REST, terms × cities × pages | Partial coverage per query, so be careful with run-based expiry (see notes). Free key quota limits fan-out. Track calls per run. |
| HN Algolia | Find thread + fetch items; LLM extraction | Cache by comment id+hash+prompt_version (SPEC does this). Post-MVP. |
| Anthropic | Gateway: `structured()` via native JSON outputs (`output_config.format`, Pydantic-parsed in the Python SDK) and `strict: true` tool definitions for the agent | Native structured outputs and strict tool use exist and can be combined in one request. Whether a beta header is still required is unconfirmed (MEDIUM). Check live docs in the phase that implements the gateway. Prefer native schemas over "please return JSON" plus retries; keep SPEC's single-retry-on-validation-error as a backstop. |
| fastembed | In-process ONNX | Use `query_embed` for queries/profiles and `passage_embed` for jobs (bge models expect a query instruction). Bake the model into the image (SPEC §15.4). `TextCrossEncoder` rerankers exist (e.g. `BAAI/bge-reranker-base`, MIT; Jina v1 Apache-2.0; avoid Jina v2 multilingual, which is CC-BY-NC) for OPEN-5. Check `list_supported_models()` in the installed version. |
| Managed Postgres | psycopg3 async, pool sized for 1 worker | Confirm the pgvector version (≥ 0.8.0 for iterative scans) and that `SET LOCAL hnsw.*` is permitted on the provider at P0. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| api ↔ worker | Postgres `tasks` table (SKIP LOCKED) + shared tables | No direct RPC. The worker is horizontally safe. |
| routers ↔ services | Direct async calls with `UserCtx` | Routers do no business logic. |
| agent tools ↔ services | Direct calls; payload trimmed for the model; full objects kept for cards | `user_id` only from session. |
| services ↔ ranking | `rank(candidates, ctx, cfg, llm)`, pure | No DB inside ranking. |
| everything ↔ LLM | `llm/` gateway only | Typed `LLMUnavailable`; all calls logged; budget enforced centrally. |
| everything ↔ embeddings | `Embedder` protocol | Model name and dimension from settings; `embedding_model` stored per row. |
| api ↔ web | OpenAPI → `openapi-typescript`; SSE event union | Commit `docs/openapi.json` in CI so drift fails the build. |
| production ↔ evals | Same package, different DB URL and config | The snapshot includes embeddings. |

---

## Sources

- pgvector README (v0.8.7): HNSW `ef_search` default 40, post-index filtering, iterative scans (`hnsw.iterative_scan`, `hnsw.max_scan_tuples`=20,000), index use requires `ORDER BY <op> ASC LIMIT`, hybrid search with RRF or cross-encoder. https://github.com/pgvector/pgvector (HIGH)
- pgvector 0.8.0 release announcement. https://www.postgresql.org/about/news/pgvector-080-released-2952/ (HIGH)
- PostgreSQL CTE materialization (multi-reference CTEs materialize by default since PG12; `NOT MATERIALIZED` override): https://boringsql.com/posts/good-cte-bad-cte/ and pgsql-performance thread https://www.postgresql.org/message-id/348393.1743517738%40sss.pgh.pa.us (MEDIUM-HIGH; matches the PG docs from training knowledge)
- Neon pgvector extension docs (version policy; current version not stated). https://neon.tech/docs/extensions/pgvector (MEDIUM; verify at P0)
- Claude structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs ; strict tool use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use (MEDIUM on the exact request shape and beta-header status)
- FastEmbed rerankers (`TextCrossEncoder`, supported models, licences). https://qdrant.tech/documentation/fastembed/fastembed-rerankers/ (MEDIUM)
- Funnel structure (candidate generation → ranking → re-ranking), RRF, Rocchio, MMR, IR pooling: standard IR/recsys literature, consistent with SPEC §10 and §12 (HIGH as patterns; training knowledge)
- Postgres-as-queue with `FOR UPDATE SKIP LOCKED`: standard Postgres pattern (HIGH; training knowledge)

---
*Architecture research for: conversational job matching / hybrid-search recommender with LLM agent*
*Researched: 2026-11-21*
