# Project Research Summary

**Project:** Ranker (conversational AI job matching for early-career engineers in India)
**Domain:** LLM agent + hybrid lexical/vector search + feature ranking + personalisation over a live jobs index. Python/FastAPI backend, Postgres+pgvector, Next.js frontend.
**Researched:** 2026-11-21
**Confidence:** MEDIUM-HIGH. Library versions, pgvector behaviour and the Remotive data problem were checked live or in a throwaway container (HIGH). Adzuna quota, hosting prices and competitor features are MEDIUM/LOW.

Detail lives in STACK.md, FEATURES.md, ARCHITECTURE.md and PITFALLS.md in this folder. This file reconciles them and states one recommendation wherever they disagree.

---

## Executive Summary

Ranker is a standard search/recommendation funnel (ingest → index → retrieve → score → rerank → diversify → log feedback) with an LLM agent on top that calls the funnel as a tool. All four reports agree the SPEC's architecture and stack are right in shape. Keep Postgres+pgvector as the only datastore, fastembed for local embeddings, a thin hand-written agent loop on the Anthropic SDK (no LangChain), and SSE for chat. The product bar comes from Elip itself: a few well-ranked, currently open jobs, each with honest reasons, a categorical fit label ("strong fit / stretch") and visible dealbreaker handling. The user-facing differentiators are relevance, freshness and transparency, not AI features. The portfolio differentiator is published eval numbers backed by an ablation table.

The research found about 40 SPEC defects. Four of them threaten the headline goals before any code is written:

1. **The data sources can't deliver the planned index.** Remotive returned 16 jobs in total on 2026-11-21, about half of them US-only. The SPEC's Adzuna fan-out (320 calls/run) exceeds the estimated ~250 calls/day free quota, and Adzuna only returns description snippets. ING-12 (≥ 10k active jobs) is unreachable as written.
2. **The hybrid SQL is silently wrong.** pgvector's `ef_search=40` caps the vector leg at 40 rows. The shared CTE materialises, so the HNSW index is never used. `english` tsvector turns C++/C# into `c`. `websearch_to_tsquery` ANDs every word of a sentence.
3. **The LLM sits on hot paths it can't serve.** LLM query parsing can't meet search p95 < 300 ms. A 20-item rerank that writes full reasons can't finish inside the 6 s timeout. The post-hoc grounding check runs after tokens have already streamed.
4. **Deploying in Phase 9 is too late.** Cross-site cookies on platform subdomains, ONNX RAM multiplied across uvicorn workers, and scheduler duplication would all surface at the last moment.

The recommended approach is to keep SPEC's phase order with targeted changes:

- Do a walking-skeleton deploy to a single Singapore VPS on a custom domain in Phase 0.
- Run a source-viability spike before Phase 1 code, and add a `remote_eligibility` column from the first migration.
- Build the LLM gateway (with a fake provider) and a Postgres `tasks` queue as Phase 2 infrastructure.
- Treat retrieval correctness as a Phase 3 gate: separate SQL legs, an explicit exact-vs-HNSW decision, a taxonomy-rewritten tsvector, and a deterministic eval.
- In ranking, score all 20 candidates cheaply and write reasons only for the top few, cached by a query-aware key.
- Ground the agent with `[[job:ID]]` markers filtered during streaming.
- For the MVP, add five cheap trust features (remote eligibility, filter chips, fit label, freshness line, minimal profile page) and drop MMR, Rocchio, memory supersession and passwords.

---

## Key Findings

### Recommended Stack

SPEC §16 is current. Nothing needs replacing, but several versions must be pinned and a few components swapped (STACK.md has every version, checked live against PyPI, npm and Docker Hub).

**Core technologies:**
- **Python 3.14** (not 3.12, which is security-only; never 3.15, which has no onnxruntime wheels): the only bug-fix release, and all native deps ship cp314 wheels.
- **FastAPI 0.165 with native SSE** (`fastapi.sse.EventSourceResponse`, replaces sse-starlette): keep-alive pings and anti-buffering headers built in.
- **SQLAlchemy 2.1 (`[asyncio]` extra) + Alembic 1.20 + psycopg 3.3**: 2.1 no longer installs greenlet and autoflushes before `text()`. Write HNSW, GIN and tsvector DDL with `op.execute()`.
- **PostgreSQL 18 + pgvector 0.8.7** (minimum 0.8.3, which fixed HNSW vacuum index corruption; image `pgvector/pgvector:0.8.7-pg18-trixie`): use the same major version in dev, CI and prod. PG18 images mount at `/var/lib/postgresql`.
- **fastembed 0.9 / bge-small-en-v1.5 (384-d, quantised ONNX)**: bake the model into the image (`FASTEMBED_CACHE_PATH`) and set `threads`. `query_embed()` does **not** add the BGE query prefix, so test that as an ablation row.
- **anthropic SDK 2.12**: `messages.parse(output_format=Model)` is GA with no beta header. Use `strict: true` tools. Defaults:
  - `claude-haiku-5-5` for rerank, parsing and extraction (about $0.002 per 20-job rerank).
  - `claude-sonnet-5-5` for the agent. It **rejects `thinking: disabled`**, so use `between_tools` with low effort.
  - Don't send `temperature` to Haiku 5.5. Pass thinking blocks back unchanged in the tool loop.
- **Frontend:** Next 16.4, React 19.3, Tailwind 4.3 (CSS-first config), shadcn 4.23, **TypeScript pinned `~5.9.3`** (npm `latest` is TS 7, which breaks typescript-eslint and openapi-typescript), openapi-typescript/openapi-fetch, `fetch` + `eventsource-parser` for chat (native `EventSource` can't POST).
- **Supporting:** `apscheduler>=3.11,<4` (4.x is alpha with a different API), pypdf 7 (pypdfium2 as fallback, **never PyMuPDF**, which is AGPL), nh3 for HTML stripping, tenacity, structlog, sentry-sdk with PII scrubbing, testcontainers with `driver="psycopg"`, respx, time-machine, hypothesis, pyright, k6.
- **Avoid:** LangChain/LlamaIndex, any separate vector DB, PyTorch/sentence-transformers, slowapi with an in-memory store, `dict` fields in LLM schemas, Supabase Free, Render Free.

### Expected Features

The market (Elip, Jobright, LinkedIn AI search, Indeed Career Scout, HiringCafe, Naukri) has made NL search, per-job fit explanations and skill gaps table stakes. Users mostly complain about irrelevant matches, fake-precise match percentages, stale or ghost listings, and signup walls.

**Must have (table stakes):**
- Instant search without signup (also effectively required by Remotive's terms)
- NL query → **editable filter chips** showing what was understood (gap in SPEC)
- Hard filters including internship and seniority, plus **remote eligibility** (gap in SPEC; about 44% of Remotive's jobs are US/NA-only)
- Job card: title, company, location, INR/LPA salary or an explicit "not listed", source badge, link-back, **freshness line** ("posted 3d ago · checked 6h ago"; gap in SPEC)
- "Why this matches" + missing skills + concerns, with rule-based fallback in degraded mode
- Resume upload → editable profile, plus a 4–5 field no-resume form
- Save/skip + saved list; dedupe; **per-company cap** (keep it in the MVP, about 15 lines)
- Helpful empty states with one-click "relax filter"
- Streaming chat that confirms applied preferences
- **Minimal profile page** to view/edit preferences, list/delete memories and delete my data. The MVP UI list omits it, but MEM-05/US-07 and the privacy page require it.

**Should have (differentiators):**
- **Fit label** (Strong / Good / Stretch) from rerank relevance 3/2/1, or from score buckets in degraded mode, instead of a percentage
- Grounded agent with cards streamed from tool results ("0 hallucinated IDs" as a portfolio claim)
- Visible, deletable cross-session memory
- **Skip-with-reason chips** that write deterministic preference patches (better early personalisation than Rocchio)
- "Why ranked here" panel (feature contributions), the cheap MVP stand-in for the job-detail match breakdown
- "Set aside" counts ("43 hidden by your dealbreakers"), post-MVP
- India-native normalisation (LPA, Cr, stipends, Bengaluru/Bangalore, NCR)
- Public `/evals` page with the ablation, post-MVP

**Defer (v1.x / v2+):** job detail page with full breakdown, HN source, Greenhouse/Lever boards (consider pulling forward, see the source crisis below), Rocchio + MMR, compare tool, email/password accounts, consultancy filter, "new since last visit", daily digest, learned ranker, OAuth.

**Anti-features (do not build):** auto-apply, resume rewriting/cover letters, precise match %, career coaching/interview prep, scraping LinkedIn/Naukri/Indeed, a signup wall before results, infinite-scroll feeds, WebSockets.

### Architecture Approach

Keep SPEC's api / worker / db / web split, but make three boundaries explicit:

1. **A `services/` layer** (SearchService, FeedService, ProfileService, InteractionService) that both HTTP routers and agent tools call. Tools become 10-line adapters with `user_id` taken from the session, never from model args.
2. **A pure, DB-free `rank(candidates, ctx, config, llm)`** with injected `RankingConfig`. The eval harness, the weight tuner and the ablation then call production code with different configs, and ablation rows become config permutations.
3. **A single LLM gateway** that is the degradation boundary: budget reservation, timeout, circuit breaker, `llm_calls` logging and a fake provider, with callers catching one `LLMUnavailable`.

Postgres also serves as the queue (`tasks` with SKIP LOCKED), cache (`rerank_cache`, `query_parse_cache`), rate limiter and log.

**Major components:**
1. **Ingest (worker):** source adapters → normalise → skills → hash/dedupe key → upsert → embed changed → per-source expiry → canonical recompute → `ingest_runs`, under a per-source advisory lock.
2. **Retrieval (`search/`):** one `SearchFilters` → SQL fragment shared by both legs. Lexical leg on parsed, taxonomy-rewritten text. Vector leg written directly against `jobs`. RRF. Skipped/saved anti-join in SQL.
3. **Ranking (`ranking/`):** features → weighted score → top 50 → rerank top 20 (scores for all, reasons for the top few) → blend within top 20 → append 21–50 → company cap → explanations/fit label.
4. **LLM gateway (`llm/`):** `structured()`, `run_tools(stream=True)`, budget, breaker, logging, fake provider. Preserves raw provider content blocks within a turn.
5. **Agent (`agent/`):** context assembly, ≤ 6-round tool loop, streaming `[[job:ID]]` marker filter, SSE events (`tool_status | token | jobs | done | error`), enqueues memory tasks.
6. **Tasks (`tasks/`):** Postgres `tasks` table (`dedupe_key` unique-where-pending, `run_after` for debounce) consumed by the worker. Handles memory extraction, summaries and later Rocchio.
7. **Eval harness (`evals/`):** imports SearchService/`rank()` against a snapshot DB. Never re-implements the pipeline.
8. **Web:** Next.js App Router, generated client, SSE reducer, optimistic save/skip, batched impressions with position.

### Critical Pitfalls

1. **Source volume crisis** (Remotive 16 jobs, Adzuna quota about 250/day, snippet-only descriptions).
   - Run a 1-hour viability spike before Phase 1 code: real Adzuna key, read the quota in the dashboard, count unique IDs, `salary_is_predicted` rate, description length. Record it in ADR `0001-sources.md`.
   - Set a crawl budget ≤ 0.8 × quota with (term, city) slice rotation and `max_days_old`/`sort_by=date`.
   - Treat Remotive as attribution-only. Restate ING-12 honestly, and bring Greenhouse/Lever (ING-13) into v1.
2. **Silently broken hybrid retrieval** (`ef_search=40` cap, materialised CTE, C++/C# → `c`, sentence AND-collapse).
   - Write the legs separately and select only `id`.
   - Whenever HNSW is used, add `SET LOCAL hnsw.ef_search=200` + `iterative_scan=relaxed_order`.
   - Add canonical skills to the tsvector with the `simple` config at weight A, using a shared token rewrite (`c++→cplusplus`, `c#→csharp`, `.net→dotnet`, `go/golang→golang`).
   - Build an OR tsquery from parsed text only.
   - Assert that the vector leg returns `vector_k` rows and that the `lex_hits=0` rate is under 30%.
3. **Absence-based expiry wipes live Adzuna jobs.** Use per-source rules:
   - Full-feed sources (Remotive, Greenhouse/Lever) expire after 2 missed *complete* runs.
   - Adzuna uses a TTL (`created + 30–45 days`, refreshed when seen again).
   - Add a proportional guard: no expiry if fetched < 0.5 × the median of the last 5 runs, and partial runs expire nothing.
   - Promote another copy when a canonical job expires.
4. **LLM latency and correctness on hot paths** (query parse, rerank, grounding).
   - Parse rules-first: LLM only on the residue, cached, behind a flag, and skipped entirely in chat.
   - Rerank score-only in parallel chunks for cache misses, with reasons for the top 5–10.
   - "Cards from tools, text from model", with streaming marker filtering plus deterministic salary/company checks against tool payloads.
5. **Eval leakage makes the ablation dishonest** (same model family labels and reranks, weights tuned on the test personas, near-duplicate fold leakage, pool bias, flaky CI gate).
   - Human-label 10 personas before any rerank work and report that subset separately.
   - Use a different model tier/prompt for labels than for rerank, and report κ.
   - Use 5-fold persona CV for weight tuning, and dedupe the snapshot before folds.
   - Run the CI gate on exact search with stored embeddings and `ORDER BY score DESC, id` tie-breakers.
   - Keep the snapshot out of public git (source terms).

Also high:
- `dict` structured-output fields always return `{}`.
- Memory deletion doesn't undo its preference patch.
- Prompt injection through job text into rerank and memory.
- Cost blowups from unlimited guest sessions.
- PII leaking through Sentry local variables.

---

## SPEC Corrections (consolidated, deduplicated)

Severity: **Critical** = silently breaks a headline goal or blocks launch. **High** = breaks a requirement or acceptance criterion. **Medium** = quality/cost/correctness issue with a known fix. **Low** = hygiene. Phase numbers follow SPEC §17 (0–10). Sources: S=STACK, F=FEATURES, A=ARCHITECTURE, P=PITFALLS.

| # | Correction | SPEC ref | Sev | Phase | Src |
|---|---|---|---|---|---|
| 1 | **Source volume:** Remotive has 16 jobs (about half US-only); Adzuna 8×8×5 = 320 calls/run exceeds the estimated ~250/day and ~2,500/month quota; Adzuna returns snippets. Do a viability spike + ADR before code; crawl budget ≤ 0.8 × quota with slice rotation; Remotive becomes attribution-only; restate ING-12 (MVP: honest count, e.g. ≥ 3k India-relevant; v1: 10k contingent on Greenhouse/Lever); Phase 2's "≥ 3k from all three sources" becomes "from active sources". Phase 3's p95 load test at 10k may use synthetic rows if the real index is smaller. | ING-12, §7, §18, §17 P2/P3 | Critical | 1 (spike), 2 | F, P |
| 2 | **Add remote eligibility:** `remote_eligibility` enum (`worldwide \| india \| apac \| restricted \| unknown`) as the filter column, with optional `remote_regions TEXT[]` for display. Parse Remotive `candidate_required_location`, HN "(US only)", timezone constraints. Hide `restricted`-excluding-India by default for India users; `unknown` gets a reduced `f_location`, not 1.0; add "US-only remote" to rerank concerns. Put it in the first `jobs` migration; retrofitting needs re-normalisation. | NORM-02, SRCH-04, `jobs`, §10 f_location | Critical | 1 (column), 2 (parser) | F, P |
| 3 | **Per-source expiry:** absence-based for full-feed sources only (2 missed *complete* runs); Adzuna uses TTL `created + 30–45d` refreshed on sighting; proportional fetch guard (not just zero-fetch); `partial` runs never expire; per-source `pg_advisory_xact_lock`. | ING-08, §7 | Critical | 2 | A, P |
| 4 | **`hnsw.ef_search` defaults to 40**, so the vector leg returns ≤ 40 rows (31 with a loose filter, verified). Whenever HNSW is used: `SET LOCAL hnsw.ef_search = 200` + `SET LOCAL hnsw.iterative_scan = relaxed_order` in the search transaction; test that `vector_k` rows come back; metric when short. | §8.1, SRCH-02/03 | Critical | 3 | S, A, P |
| 5 | **Shared `base` CTE materialises** (referenced twice), so HNSW is never used and `SELECT j.*` copies descriptions/embeddings. Drop the shared CTE; write each leg against `jobs` with the filter fragment spliced in from `search/filters.py`; partial-index predicate written literally; `ORDER BY embedding <=> :qv LIMIT :k` unwrapped; select `id` only. | §8.1, SRCH-07 | High | 3 | S, A, P |
| 6 | **Exact vs HNSW decision:** replace Phase 3's "EXPLAIN shows index use for both legs" with "measured exact-vs-HNSW decision recorded in an ADR". **Recommendation: exact filtered scan as the v1 default** (see Disagreements). | §17 P3 acceptance, §8.1 | High | 3 | S, A, P |
| 7 | **Lexical leg tokenisation:** `english` config maps C++/C# → `c`, .NET → `net`, and stems/stops "Go"; `websearch_to_tsquery` on a sentence ANDs every word (verified no-match). Add canonical skills via the `simple` config at weight A (immutable wrapper or `skills_text` maintained at ingest); shared taxonomy token rewrite on index and query (`cplusplus`, `csharp`, `dotnet`, `golang`); build an OR tsquery from parsed `query_text` with filter words removed; log `lex_hits`. | SRCH-01, D2 | High | 2 (taxonomy aliases), 3 | A, P |
| 8 | **Query parse must not be an LLM call on the search path:** rules first (cities.yaml, LPA/Cr/stipend regex, remote/seniority words); LLM only on unparsed constraint-like residue, cached in `query_parse_cache` by normalised query (never including user prefs), behind a flag; **skip parsing in chat** (tool args are the parse). | SRCH-07/08 | High | 3 (rules), 5 (LLM fallback) | A, F, P |
| 9 | **Rerank cache key omits the query; guests share results.** Key = `(context_hash, job_id, content_hash, prompt_version, model)` with `context_hash = sha256(profile_hash \| normalised_query \| effective_filters)`. Keep memory penalties out of the key (they apply in features). | RANK-03 | High | 5 | A, P |
| 10 | **Rerank latency:** 20 items with full reasons ≈ 1.5–2.5k output tokens, which won't fit in 6 s, so search would be degraded all the time. **Score all 20** (relevance + reason code, ~10 tokens/item) in parallel chunks (e.g. 4×5) sending only cache misses; generate **reasons/missing skills/concerns for the top 5–10 only** (or lazily on expand). Measure p50/p95 before fixing the timeout; track the degraded rate (< 20%). | RANK-03, §10 | High | 5 | A, P |
| 11 | **Grounding during streaming:** the post-hoc check runs after tokens have streamed. Cards (`jobs` SSE) are built only from tool results; the model refers to jobs only as `[[job:ID]]`; `agent/grounding.py` buffers inside markers and drops IDs outside the conversation's allowed set; deterministic check that salary/company strings in prose appear in tool payloads; persist `seen_job_ids` so they survive summary compression; the post-hoc check becomes an audit metric/eval assertion (repair only in non-streamed evals). | §11.1 steps 5–6, AGENT-03 | Critical | 6 | A, P |
| 12 | **Typed structured outputs:** `MemoryOp.structured: dict` always returns `{}` (Anthropic sends empty `properties`), so RANK-06 penalties never get data. Use nested typed models (e.g. `StructuredPref{exclude_industry, avoid_stage, dislike_company}`). Also: replace `max_length`/`ge`/`le` with clamping `AfterValidator`s (they otherwise fail client-side and burn the retry); add `unknown` to Literals; check `stop_reason`; validate rerank `job_id` set equals input (drop unknown, missing → feature score). Gateway convention, enforced in every schema. | §9.4, §9 all schemas | Critical (silent) | 2 (gateway), 4, 5, 6 | S, P |
| 13 | **Async work channel undefined:** add a Postgres `tasks` table (`kind, user_id, payload, run_after, status, attempts, dedupe_key unique-where-pending`) consumed with `FOR UPDATE SKIP LOCKED`; debounce via `dedupe_key` + `run_after`. Not `BackgroundTasks`/`create_task` (lost on restart, duplicated across workers). | §11.1 step 8, §10.4, MEM-02 | High | 2 | A (S offers procrastinate) |
| 14 | **LLM gateway built once, early:** budget reservation (pessimistic pre-call, settle after), per-purpose budgets (user/ingest/eval, eval on a separate key), timeout, circuit breaker, logging, fake provider, no injected `temperature`, raw content blocks preserved in the tool loop, Sonnet `thinking: between_tools`. | §11.5, §14.4, §19 | High | 2 | S, A, P |
| 15 | **Walking-skeleton deploy in Phase 0**, not first contact in Phase 9: api `/readyz` with the model loaded + Postgres with pgvector version checked, on the real host and domain; measure RSS. Phase 9 becomes "turn on scheduler + observability". | §17 P0/P9, OPEN-1 | Critical | 0 | A, P |
| 16 | **Custom domain is required:** `*.vercel.app`, `*.onrender.com`, `*.up.railway.app` are on the Public Suffix List, so web→api is cross-site and `SameSite=Lax` cookies aren't sent. Serve `ranker.<tld>` + `api.ranker.<tld>`. Resolves OPEN-6. | OPEN-6, §15, AUTH-01 | Critical | 0 | S, P |
| 17 | **One uvicorn worker**, not 2: each process holds a ~200–300 MB ONNX copy, Prometheus needs multiproc mode, in-memory state diverges. Scale by instance. Scheduler only in the separate worker (or cron), never in the API. | §6.2 | High | 0 | S, A, P |
| 18 | **Hosting (OPEN-1):** single VPS (Hetzner Singapore, ≥ 4 GB) running Compose (db, api, worker, Caddy) + Vercel for web; nightly `pg_dump` to R2/B2. Supabase Free and Render Free ruled out (Render Free Postgres is deleted after 30 days). | OPEN-1, §15.2 | High | 0 | S, P |
| 19 | **Version pins:** Python 3.12 → 3.14; PG16 → PG18 with pgvector ≥ 0.8.3 (use 0.8.7); image `pgvector/pgvector:0.8.7-pg18-trixie` everywhere incl. testcontainers (`driver="psycopg"`); TypeScript `~5.9.3`; `apscheduler>=3.11,<4`; `sqlalchemy[asyncio]`. Update PROJECT.md constraints. | §16, §15, §19 | High | 0 | S |
| 20 | **Model baked into the image:** `FASTEMBED_CACHE_PATH` inside the image (default is `/tmp`, re-downloaded on cold start); `TextEmbedding(threads=1–2)`; embed in a threadpool. | §15.4 | Medium | 0 | S, P |
| 21 | **Eval leakage:** human-label 10 personas first; label with a different model tier/prompt than rerank and report κ; report the rerank gain on the human-only subset; 5-fold persona CV for RANK-09 weight tuning (held-out metrics only); dedupe the snapshot (dedupe_key / cos > 0.97) before folds; track "unjudged in top 10" and re-pool in Phase 5 when > 10%; judge gets full tool results. | §12, RANK-09, EVAL-03/06 | High | 3, 5, 6 | P, A |
| 22 | **Deterministic CI gate:** snapshot stores embeddings + `embedding_model` + skills + company fields; pinned fastembed/onnxruntime; exact search in the eval DB; `ORDER BY score DESC, id` tie-breakers; run twice on `main` to confirm identical output. | EVAL-07 | Medium | 3 | P, A |
| 23 | **Snapshot not in public git:** a full-text job snapshot republishes Adzuna/Remotive data. Use a private artifact fetched in CI, or commit IDs/embeddings/derived fields only. | EVAL-01 | High | 3 | P |
| 24 | **Blend definition:** blend `α·r/3 + (1−α)·s_norm` within the reranked top 20 only, append feature-ranked 21–50 below, then company cap. Normalise `f_sem` against a fixed range (`(cos−0.5)/0.5` clipped) not min-max; guard zero range; `f_lex` as binary/rank-decay, not RRF magnitude; calibrate unknown-salary 0.5 against the real unknown rate. | RANK-01, §10 | Medium | 5 | A, P |
| 25 | **Exclusions in SQL:** push skipped (and saved, for the feed) into retrieval as an anti-join so rerank budget isn't spent on hidden jobs. | RANK-08 | Medium | 3/5 | A |
| 26 | **Feedback double-count:** retrieval and `f_sem` use `u₀` (profile or query embedding); Rocchio enters only through `f_feedback`. Cap drift (cos(u, u₀) ≥ 0.7). | RANK-05 | Medium | post-MVP (Rocchio) | A, P |
| 27 | **Dedupe fixes:** keep a seniority bucket in `dedupe_key` (otherwise junior roles merge into senior ones); recompute canonical per touched key at the end of each run (promote a live duplicate when the canonical expires); cap the recency boost by `first_seen_at` per key (reposts); exclude placeholder companies ("Confidential") from dedupe and the company cap. | ING-10, §7.5, RANK-07 | Medium | 2 | A, P |
| 28 | **Adzuna data flags:** `salary_is_estimated` from `salary_is_predicted=1` (show "Est.", never use for the hard floor); `description_truncated` (reranker must not claim "JD doesn't mention X"); salary filter uses `salary_max ≥ floor`; outbound link = `redirect_url`; normalise location from `location.area`. | NORM-03, NORM-02 | Medium | 1 (columns), 2 | P |
| 29 | **Tool schema units:** the agent tool takes `min_salary_lpa: float` (and USD-k), with examples in descriptions; canonicalise cities/skills server-side (NCR → Delhi/Gurugram/Noida); `strict: true`; echo applied filters in the tool result (feeds the chips). | §11.2 | High | 6 | P |
| 30 | **Memory/preference coupling:** explicit preferences go through `update_preferences` **synchronously in the same turn**; async extraction only for soft memories, from **user messages only** (injection), with existing memories + IDs passed in to avoid duplicates; each memory records which preference fields it set and deleting it reverts them; serialise extraction per user. MVP: add-only + `preference_patch`, no supersession chains. | MEM-02/04/05 | High | 6 | P, F |
| 31 | **Prompt injection beyond chat:** wrap job text in `<job>` tags in the rerank prompt; flag instruction-like lines; injected-job fixture in the ranking eval; render chat as plain text + whitelisted cards (no markdown images, links only to stored job URLs). | §14.3 | High | 5, 6, 7 | P |
| 32 | **Guest abuse and cost:** rate-limit guest creation per IP and chat per IP+cookie; prompt caching on system prompt + tools + memories; trim tool payload job text to ~300 chars; alert at 50% budget by day 10. | §14.4 | High | 6, 8 | P |
| 33 | **Rate-limit store (OPEN-3):** Postgres fixed-window counter table (`INSERT … ON CONFLICT … RETURNING`), not slowapi (no Postgres backend; in-memory is per-process). | OPEN-3 | Medium | 8 | S, A, P |
| 34 | **SSE details:** FastAPI native SSE replaces sse-starlette; client uses `fetch` + `eventsource-parser` (POST with body); disable gzip on `/chat`, `X-Accel-Buffering: no`, 15 s heartbeat; prod smoke test that tokens stream through Caddy. | AGENT-02, §11.4 | Medium | 6, 7 | S, P |
| 35 | **PII:** Sentry `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`, `before_send` scrubber; structlog processor drops `resume_text/message/content/email`; regex redaction on `search_logs.query_text`; fake-transport test with a forced resume-parse exception. | §14, OPS-05/08 | High | 0, 4, 9 | S, P |
| 36 | **Resume parsing:** pypdf primary (pypdfium2 fallback, never PyMuPDF); detect scanned PDFs (< 200 chars → clear error); compute years of experience **deterministically** from dates (LLM value only as fallback); manual magic-byte checks. | PROF-01/02 | Medium | 4 | S, P |
| 37 | **Pagination:** cache the final ranked ID list per `search_log_id` and page through it; don't re-run the pipeline per page. | §13 cursors | Medium | 5 | A |
| 38 | **Storage hygiene:** `search_logs` stores the top 20 with compact features (sample full logs); null `description_raw` for expired jobs after ~30 days; DB size on the admin dashboard. | §8, OPS | Low | 3, 9 | P, A |
| 39 | **Remotive terms:** one call per run, ≤ 4/day, ≤ 2/min; attribution + link-back on every card; never gate Remotive jobs behind signup; its 24 h delay is not staleness. | ING-01 | Medium | 1 | F, P |
| 40 | **Skills taxonomy precision:** ambiguous tokens (Go, R, C, Swift, Spring, React, Express, Excel…) need case-sensitive matching / second-signal rules and longest-match; measure precision per skill. | NORM-05 | Medium | 2 | P |

---

## Disagreements Resolved

| Topic | Positions | Recommendation and reasoning |
|---|---|---|
| **HNSW vs exact vector search** | ARCHITECTURE: fix the SQL so HNSW is used, with `ef_search` + iterative scan. STACK: fix `ef_search` + CTE, exact is "legitimate". PITFALLS: verified the SPEC query already runs exact in ~16 ms at 20k rows; use exact. | **Default to exact filtered search for v1, and keep HNSW as a measured, config-switchable path.** At 10k–50k rows exact search has perfect recall, no `ef_search`/filter interaction, deterministic CI, and measured latency far under the 300 ms budget. Still fix the SQL shape (#5), because a clean per-leg query is what lets either path work. Build the HNSW index after bulk load, implement the `SET LOCAL` path, benchmark both at 50k rows with real descriptions in Phase 3, and record the ADR. Revisit at ~100k rows. The eval/CI DB always uses exact. |
| **Hosting** | STACK: Hetzner Singapore VPS + Vercel (managed Neon+Railway as alternative). PITFALLS: free tiers fail on RAM, sleep, cookies; budget a paid always-on instance. ARCHITECTURE: assumes managed Postgres, deploy early. | **Single VPS (Compose: pgvector PG18, api, worker, Caddy) + Vercel + custom domain.** About €5.49 + ~₹80/month domain leaves ~$20+ for LLM within the ₹2,500 cap. No scale-to-zero cold starts (Neon Free can't disable it), full control of the pgvector version, the worker/scheduler is always on, and enough RAM for the model. It's also a real Docker deploy, which Elip lists as a good-to-have. Cost: you own backups and patching (nightly `pg_dump` to R2/B2 + a restore drill + uptime monitor). Fallback: Neon Launch (always-on, ~$19) + Railway, which lands at or over budget. Verify the Hetzner plan's RAM in Phase 0 (LOW-MEDIUM). |
| **Uvicorn workers** | SPEC: 2. STACK: 2 only if RAM allows, start at 1. ARCHITECTURE/PITFALLS: 1. | **1 worker.** Search is async and I/O-bound, embedding runs in a threadpool, and onnxruntime releases the GIL. Verify 20 rps with k6. |
| **Async task transport** | ARCHITECTURE: hand-rolled Postgres `tasks` table. STACK: `BackgroundTasks` acceptable in the MVP, procrastinate as the upgrade. | **Hand-rolled `tasks` table in Phase 2** (~100 lines). It gives debounce, restart safety, admin visibility and per-user serialisation, and shows SQL skill. procrastinate is an acceptable swap if the planner prefers a library. Don't use `BackgroundTasks`. |
| **Structured outputs beta header** | ARCHITECTURE: unconfirmed. STACK: GA, `output_config.format`, no beta header (HIGH, docs checked). | **GA, no beta header.** Confirm once when building the gateway. |
| **Neon pgvector version** | ARCHITECTURE: not stated by Neon. STACK: 0.8.7 on PG16–19 (extension page). | Moot under the VPS recommendation. If managed, verify in Phase 0. |
| **Token rewrite spelling** | ARCHITECTURE: `c++→cpp`. PITFALLS: `cplusplus`. | `cplusplus`, `csharp`, `dotnet`, `golang`. These are unambiguous lexemes. One alias table for index and query. |
| **Remote field shape** | FEATURES: `remote_eligibility` enum. PITFALLS: `remote_regions TEXT[]`. | Both: the enum drives filters and scoring, and the array is kept for display/debug. |

---

## MVP Scope Adjustments (vs SPEC §18)

**Add (all LOW complexity, ~1 day total):**
- Remote eligibility column + default filter (#2)
- Filter chips showing the parsed query, editable, with "relax filter" on empty results
- Fit label (Strong / Good / Stretch) and freshness line on every card
- Minimal profile page: preferences edit, memory list/delete, delete my data
- Per-company cap (keep it; it was optional alongside MMR)
- Skip-with-reason chips → deterministic preference patch (reuses the `update_preferences` merge function) as the MVP personalisation feature

**Trim / defer:**
- MMR (defer; the company cap covers the visible problem)
- Rocchio (defer from "optional" to post-MVP together with the "+ personalisation" ablation row)
- Memory supersession / `update`/`delete` ops (MEM-04): add-only + manual delete on the profile page
- Passwords/accounts (already guest-only in §18; say "saved in this browser" in the UI)
- LLM query parsing on the search box: rules + chips by default, LLM behind a flag

**Keep in MVP despite the time pressure:** walking-skeleton deploy (Phase 0), LLM gateway with fake provider, `tasks` table, grounding marker filter, human labels on 10 personas.

---

## Implications for Roadmap

Keep SPEC's 0–10 numbering (requirements and research reference it). Changes are described per phase. MVP ≈ Phases 0–9 at their MVP slice, then v1 completion.

### Phase 0: Foundations + walking-skeleton deploy
**Rationale:** Hosting, cookies, RAM and the pgvector version are cheap to fix on day 1 and expensive on day 10 (#15–#20).
**Delivers:**
- Monorepo with pinned versions (#19).
- FastAPI `/healthz` + `/readyz` (model loaded, `vector` extension version asserted ≥ 0.8.3).
- Dockerfile with baked model; Compose (pgvector PG18, api with 1 worker, Caddy).
- Deployed to the VPS on `api.ranker.<tld>`, with the Next shell on `ranker.<tld>` (Vercel); cookie round-trip smoke test.
- structlog with a PII-dropping processor.
- CI (ruff, pyright, pytest, testcontainers on the same image).

**Avoids:** Pitfall 19 (free-tier traps, cross-site cookies), late OOM, double schedulers.

### Phase 1: Source viability + schema + Remotive
**Rationale:** Every downstream number depends on what the sources actually deliver (#1).
**Delivers:**
- 1-hour Adzuna/Remotive spike → `docs/decisions/0001-sources.md` (quota, unique IDs, snippet length, predicted-salary rate, terms).
- Migrations with `remote_eligibility`, `remote_regions`, `salary_is_estimated`, `description_truncated`, `expires_at`, `first_seen_at`.
- SourceAdapter protocol; Remotive adapter (one call/run, attribution); idempotent upsert; CLI + admin trigger.

**Addresses:** ING-01/04–07/11, NORM-01/06. **Avoids:** Pitfalls 1 and 14; correction #39.

### Phase 2: Normalisation, skills, Adzuna, expiry, dedupe + platform infra
**Rationale:** Normalised columns (eligibility, salary, seniority, skills aliases) must exist before retrieval and filters. The gateway and tasks queue are prerequisites for Phases 4–6 and should be built once.
**Delivers:**
- India-aware location/salary/seniority normalisers (≥ 50 cases each, incl. Cr, stipends, Indian digit grouping).
- Skills taxonomy with ambiguity rules and the token-rewrite alias table.
- Adzuna adapter with crawl budget + slice rotation.
- Per-source expiry + proportional guard + advisory lock.
- Dedupe with seniority bucket + canonical promotion.
- **LLM gateway v1**: structured, budget reservation, breaker, logging, fake provider, typed-schema conventions.
- **`tasks` queue**.
- HN adapter is post-MVP.

**Avoids:** Pitfalls 2, 3, 6 (taxonomy half), 10; corrections #3, #7, #12–14, #27–28, #40.

### Phase 3: Embeddings + hybrid retrieval + eval harness
**Rationale:** Retrieval recall caps everything downstream, so verify it before ranking. The eval belongs here so every later stage lands with a number.
**Delivers:**
- Embedder (threadpool, passage vs query).
- Per-leg hybrid SQL via `search/filters.py`; exact-vs-HNSW benchmark + ADR (default exact).
- tsvector with `simple` skills at weight A + OR tsquery.
- Rules-first query parser; skipped/saved anti-join.
- `GET /jobs/search`; `search_logs` (compact).
- Private snapshot with embeddings; 10 human-labelled personas.
- `ranking_eval.py` importing production code; deterministic CI gate.
- RSS measurement under load.

**Avoids:** Pitfalls 4–6, 12 (labelling design), 13; corrections #4–8, #21–23, #25.
**Parallel:** Phase 7a frontend (landing/search, job card with chips, freshness, source badge) starts against the real endpoint.

### Phase 4: Profiles + resume parsing + guest session
**Rationale:** Needs the gateway (Phase 2) and the embedder (Phase 3). Guest session is the identity primitive for everything user-scoped.
**Delivers:**
- Guest sessions (opaque token hashed in `sessions`, per-IP creation limit).
- Sandboxed pypdf/python-docx extraction.
- `CandidateProfile` with typed fields + `unknown` literals; deterministic YoE.
- Profile embedding; `/profile` endpoints; quick-form path.
- Resume eval is post-MVP.

**Avoids:** Pitfalls 10, 20; corrections #35–36. Standard patterns.

### Phase 5: Ranking + personalisation (services/ layer)
**Rationale:** Depends on profiles and retrieval. Establishes `services/` and pure `rank()` so Phase 6 tools are thin adapters.
**Delivers:**
- `services/`; `rank()` with injected `RankingConfig`.
- Features with fixed-range normalisation.
- Rerank = score-all-20 in parallel chunks (misses only) + reasons for the top 5–10, `<job>`-delimited, query-aware cache key.
- Blend within top 20; company cap.
- Fit label + rule-based reasons in degraded mode.
- Skip-with-reason → preference patch.
- Ranked-list pagination cache.
- Ablation with 5-fold CV on 10–15 personas (human subset reported).
- LLM query-parse fallback behind a flag. Rocchio/MMR post-MVP.

**Avoids:** Pitfalls 7–9, 12, 15; corrections #8–10, #24, #26, #31, #37.

### Phase 6: Agent + memory
**Rationale:** Calls the Phase 5 services. Hardest LLM work, so it builds on a proven gateway.
**Delivers:**
- `run_tools(stream=True)` loop (≤ 6 rounds, parallel tools, raw content blocks preserved, prompt caching).
- Tools: `search_jobs` (LPA units, server-side canonicalisation, applied-filter echo), `get_job_details`, `update_preferences` (sync), `save_job`.
- Streaming `[[job:ID]]` filter + prose salary/company check + persisted `seen_job_ids`.
- Native SSE.
- Memory extraction via `tasks` (user messages only, add-only, linked preference reversal).
- 10 scenarios incl. injection and LPA/NCR on the fake provider.

**Avoids:** Pitfalls 10, 11, 15–18; corrections #11, #29–32, #34.

### Phase 7: Frontend (7a from Phase 3, 7b after Phase 6)
**Delivers:**
- Landing/search with chips; onboarding.
- Feed with fit label, freshness, "why ranked here" and skip reasons.
- Chat (fetch + eventsource-parser, markers rendered as chips, plain text only).
- Saved; **minimal profile page**.
- Attribution on every surface; degraded/empty states.
- Job detail page and `/evals` page post-MVP.

**Avoids:** Pitfall 15 (rendering), UX pitfalls. Standard patterns.

### Phase 8: Hardening (guest-only for MVP)
**Delivers:**
- Postgres rate-limit counters (per IP + cookie); per-purpose budget enforcement.
- CSRF; security headers.
- Delete-my-data (all user tables); privacy page.
- pip-audit/npm audit.
- Email/password accounts post-MVP.

**Avoids:** Pitfalls 18, 20; correction #33. Standard patterns.

### Phase 9: Scheduler + observability go-live
**Rationale:** Infra already exists from Phase 0, so this phase is "switch on", not "first deploy".
**Delivers:**
- Worker with APScheduler 3.x (advisory locks).
- Sentry with scrubbing; uptime monitor; `/metrics`.
- Backups + restore drill.
- Schedule warm-up for structured-output grammars.
- Prod SSE/cookie smoke tests.
- Freshness acceptance (≥ 95% `last_seen_at` within 48 h) must be **per-source aware**: Adzuna TTL jobs aren't re-seen every run.

**Avoids:** Pitfall 19 remnants.

### Phase 10: Launch + docs
README with honest numbers (human-subset and CV-held-out metrics, real index size, source limitations), ADRs, demo video, ≥ 20 users. Then v1 completion: HN, Greenhouse/Lever (ING-13), Rocchio + MMR, full evals (30 personas, 25 scenarios, judge calibration), accounts, compare tool, `/evals` page, admin dashboard.

### Phase Ordering Rationale
- Deploy and source viability go first because they are the two facts most likely to invalidate the plan, and both are cheap to learn on day 1.
- Schema-level decisions (remote eligibility, estimated/truncated flags, TTL expiry, seniority in the dedupe key) land in Phases 1–2 because retrofitting them needs migrations plus re-normalisation.
- The gateway and tasks queue move to Phase 2 because four later phases consume them, and building them piecemeal causes rewrites.
- Retrieval correctness and the eval harness gate ranking (Phase 3 before 5): ranking can't recover candidates that retrieval dropped, and every stage should land with a number.
- `services/` + pure `rank()` in Phase 5 makes Phase 6 tools trivial and the ablation a config sweep.
- Frontend starts at Phase 3 against real endpoints. That overlap is what makes the ~10–12-day MVP plausible solo.

### Research Flags

Phases likely needing `/gsd:plan-phase --research-phase <N>`:
- **Phase 1:** Adzuna quota, `results_per_page` maximum, `max_days_old`/`sort_by` params, ToS on storing descriptions; these must be read from the live dashboard and activedocs. Also evaluate Greenhouse/Lever board APIs as a volume source in case the spike comes back poor.
- **Phase 3:** empirical rather than documentary: exact vs HNSW benchmark at 50k rows, tsvector tokenisation confirmation, k6 at 20 rps with 1 worker. Plan explicit spike tasks.
- **Phase 5:** measure rerank latency/throughput on Haiku 5.5 with structured outputs (grammar compile cost, chunk sizing); local cross-encoder comparison (OPEN-5) if time allows.
- **Phase 6:** Anthropic streaming + strict tool use + `between_tools` thinking + prompt caching in one request; check the exact request shapes against live docs.

Standard patterns (skip research): Phase 0 (except confirming the Hetzner plan's RAM), Phase 2 normalisers, Phase 4, Phase 7, Phase 8, Phase 9, Phase 10.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions and compatibility checked live against PyPI/npm/Docker Hub/endoflife.date; Anthropic model behaviour from official docs. Hosting prices MEDIUM, Hetzner plan specs LOW-MEDIUM. |
| Features | MEDIUM | Competitor features from vendor pages and press (MEDIUM/LOW). Remotive data problem HIGH (live call). Adzuna India dup/agency density unverified. |
| Architecture | HIGH (structure) / MEDIUM-HIGH (defects) | Funnel, services layer, Postgres-as-queue are standard patterns. pgvector/CTE defects confirmed empirically by PITFALLS in a pgvector 0.8.7 container. RAM figures MEDIUM. |
| Pitfalls | MEDIUM-HIGH | Key claims verified empirically (Remotive count, ef_search cap, CTE plan, tokenisation). Adzuna quota figures LOW-MEDIUM (secondary sources). LLM output speeds MEDIUM. |

**Overall confidence:** MEDIUM-HIGH. The plan-changing findings are the best-verified ones. The least certain inputs (Adzuna quota, VPS specs, rerank latency) each have an early, cheap verification step built into the phases above.

### Gaps to Address
- **Adzuna real quota and yield:** unknown until the Phase 1 spike. It decides whether Greenhouse/Lever moves into the MVP and what ING-12 says.
- **Real active-index size under TTL expiry:** with slice rotation and a 30–45 day TTL the active pool accumulates across days. Estimate it in the spike rather than assuming 10k.
- **Rerank latency on Haiku 5.5:** unmeasured. Chunk size, top-k for reasons and the timeout are set from Phase 5 measurements.
- **Exact-search latency at 50k rows with real descriptions:** the 16 ms figure was at 20k synthetic rows. Benchmark in Phase 3.
- **Single-worker throughput at 20 rps on a shared vCPU:** k6 in Phase 3 or 9.
- **Company industry/stage data (OPEN-4):** unresolved by research. Without it `exclude_industries`/`avoid_stage` only match job text. Decide in Phase 5 (manual top-300 tagging is the safer default).
- **Labelling model choice:** a "different family" judge isn't available under single-provider constraints. At minimum use a different tier/prompt and lean on the human subset.
- **Adzuna storage/caching ToS** for keeping descriptions 180 days: check in the Phase 1 spike.

---

## Sources

### Primary (HIGH confidence)
- Live `GET https://remotive.com/api/remote-jobs` (2026-11-21): 16 jobs, location breakdown, legal notice
- Throwaway `pgvector/pgvector` container (pgvector 0.8.7, 20k rows):
  - `LIMIT 100` → 40 rows; 31 with filter; 100 with `ef_search=200`/iterative scan.
  - SPEC §8.1 plan materialises the CTE (~16 ms).
  - `C++`/`C#` → `'c'`; sentence `websearch_to_tsquery` AND-collapse.
- pgvector README + CHANGELOG (ef_search, iterative scans, 0.8.3 vacuum fix, index-use rules)
- PyPI / npm / Docker Hub / endoflife.date live checks (all versions in STACK.md)
- Anthropic docs: models overview, Haiku 5.5, structured outputs (`dict` → `{}`, unsupported constraints, GA), thinking (Sonnet 5.5 rejects disabled), strict tool use
- FastAPI SSE docs and release notes; SQLAlchemy 2.1 migration guide; fastembed 0.9.1 source; Remotive API README; Adzuna search docs (snippets, `salary_is_predicted`); HN Algolia API limits

### Secondary (MEDIUM confidence)
- Vendor pricing: Neon, Railway, Supabase, Render free-tier docs
- Elip product page (tryelip.ai), Jobright, LinkedIn, Indeed Career Scout pages and press
- Postgres CTE materialisation write-ups (consistent with observed plans)
- testcontainers Postgres module docs; Next.js TS 7 flag

### Tertiary (LOW confidence, verify)
- Adzuna quota figures (25/min, 250/day, 2,500/month) from jobspipe.dev
- Remotive feed shrinkage history (Apify scraper notes)
- Hetzner Singapore plan RAM at €5.49
- Review-site complaints (G2, Boston.com, Fortune), Jobright accuracy estimate
- LLM output-token speeds for rerank latency estimates

---
*Research completed: 2026-11-21*
*Ready for roadmap: yes*
