# Phase 3: Hybrid Search & Eval Baseline - Research

**Researched:** 2026-11-22
**Domain:** Postgres hybrid retrieval (FTS + pgvector + RRF), rule-based query parsing with an optional LLM/Jev fallback, offline IR evaluation (graded nDCG, bootstrap CIs, weighted kappa), typed API contract (OpenAPI → Zod + TanStack Query), Postgres rate limiting, Next.js search UI
**Confidence:** MEDIUM-HIGH. Core SQL behaviour was checked empirically today in the local `pgvector/pgvector:0.8.7-pg18-trixie` image. Jev facts come from TypeSafe's own docs, but access/credit status is still blog-only. Production latency numbers on the real VM are spikes the plan has to run.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Search page and job cards
- **D-01: Search-first hero.**
  - The landing page has a big search box, 3–4 clickable example queries, a one-line "how it works" and the live active-job count.
  - Results replace the hero after the first search.
  - The query and filters live in the URL, so a search can be shared or bookmarked.
- **D-02: Card contents.** Required on every card: title, company, source attribution, "posted Xd ago · checked Yh ago", link to the original. Plus:
  - **Location and work mode:** the city, or a badge "Remote · India OK" / "Remote · US only" / "Region not stated" (D-07 of Phase 2). Hybrid jobs also show on-site days per week when known.
  - **Salary in ₹ LPA:** converted using the fixed FX rates. Adzuna predictions are labelled "~ estimate" (Phase 2 D-13). An unknown salary reads "Salary not stated".
  - **Matched skills:** the top 4–6 skills, with skills from the query highlighted.
  - **"Why it matched":** one short rule-based line, e.g. "matches python, backend · Bengaluru · posted 2d ago". No LLM is used, so it works in degraded mode.
- **D-03: Ghost jobs are shown with a warning and ranked lower.** A small "may be stale" badge carries the reason from `ghost_reasons` (stale_60d, reposted_3x, no_apply_url). These jobs are never hidden.
- **D-04: 20 results plus "Load more".** No infinite scroll and no numbered pages. The page size matches the eval's top-K and keeps each request inside the p95 target.

#### Query parsing and filter chips
- **D-05: Hard filters only on clear signals.**
  - City, minimum salary, remote/hybrid/on-site and seniority become editable chips (hard filters).
  - Skills and role words ("backend", "python") stay as search terms that affect ranking, not hard filters. A strong job that lacks one word is not dropped.
- **D-06: Rules first, model fallback, user-selectable model.**
  - Rules parse the query first.
  - Only the parts the rules leave unparsed go to a model. Results are cached by (normalised query, provider).
  - Two providers are available for that fallback:
    - **Claude Haiku**, the default, through the existing `ranker/llm/` gateway.
    - **Jev** (TypeSafe AI's multiple-choice "decision model"), added as one more provider in `ranker/llm/providers/`, next to `anthropic.py`, `openrouter.py` and `fake.py`.
  - The **user chooses** in a **settings panel** ("Query parsing model: Claude Haiku / Jev").
  - There are no accounts until Phase 5, so for guests the choice is stored per browser (cookie/localStorage, sent with the search request). It moves to the account setting later.
  - The Jev option appears only when Jev is configured and enabled by the operator (env flag + key). Otherwise the panel shows Haiku only.
  - With no LLM key, or over budget, the fallback is skipped. The rules-only parse is used and the search is flagged `degraded`; search never fails.
- **D-07: Jev adoption gate.** Before any Jev code is relied on, research confirms:
  - API access (Jev is in early access)
  - pricing and whether a free tier exists
  - the output/typing contract
  - a **pinned version** (e.g. `jev-1.13.0`, never `jev-latest`)

  The public claims are mostly from TypeSafe AI and early blogs, so treat speed and cost claims as unconfirmed until measured. If access can't be confirmed, ship Haiku only, keep the provider stub behind the flag, and record that in an ADR. All tests use the fake provider or respx, never live Jev.
- **D-08: Default geography for India visitors**, when the query says nothing about location:
  - In: jobs in India, plus remote jobs marked worldwide/India/APAC.
  - Shown lower with a badge: "Region not stated" jobs.
  - Set aside and counted: US-only and EU-only remote jobs.
- **D-09: Salary filter.**
  - Unknown-salary jobs stay in but rank lower (SRCH-05).
  - Adzuna estimates satisfy the minimum and are shown as "~ estimate".
  - A chip toggle excludes estimates.
- **D-10: "Relax filter" suggests per-filter relaxations with counts.** When results are empty or few, offer clickable suggestions such as "Remove ₹10 LPA minimum (+34 jobs)", "Include Pune (+12)", "Any seniority (+8)". The counts come from the same per-filter set-aside numbers shown in the results (UI-06). Nothing is relaxed automatically.

#### Ranking before profiles
- **D-11: Guest ranking = RRF(lexical, vector), then four light signals:**
  1. a freshness boost that decays over about 2 weeks
  2. a ghost-job penalty (matches the D-03 badge)
  3. region certainty: confirmed India/APAC/worldwide rank above "region not stated"
  4. salary known: a slight boost when a salary filter is set
- **D-12: Weights are hand-set in config and tuned on the eval.**
  - Weights live in a YAML file (e.g. `config/ranking.yaml`), with a version recorded as `pipeline_version` in `search_logs`.
  - A few candidate settings are compared on the eval set, and the winner plus the numbers go in an ADR.
  - Learned weights and persona cross-validation wait for Phase 4.
- **D-13: Diversity cap of at most 2 jobs per company in the top 10.** The rest move lower, not out.

#### Eval set and grading
- **D-14: Claude drafts, the owner reviews a subset.**
  - Claude writes 10–15 personas: a mix of fresher/mid/senior; Bengaluru, other Indian cities and remote; backend, frontend, data, ML, DevOps.
  - Claude grades the pooled candidates 0–3.
  - The owner re-grades about 20% **blind** (without seeing Claude's grade).
  - Agreement (Cohen's kappa, weighted) is reported honestly in the eval doc.
  - Every grade records `graded_by` and `reviewed`. This mirrors the 02-17 labelling protocol.
- **D-15: The frozen snapshot is private; ids and labels are public.**
  - The deduplicated job-pool snapshot with precomputed embeddings is stored as a private artifact (e.g. a private GitHub release asset). CI fetches it with a token secret.
  - The public repo keeps personas, job ids, grades, the snapshot hash and the eval code.
  - Without the token (forks, local runs with no access), CI skips the eval step with a clear message rather than failing.
- **D-16: The eval uses exact vector search for determinism (EVAL-08).** CI fails if nDCG@10 drops more than 0.02 from the stored baseline.
- **D-17: Jev query-parse measurement in Phase 3.**
  - A small labelled query set of about 50 realistic queries, with gold city, salary, work mode, seniority and role.
  - Score three variants: **rules-only vs Haiku vs Jev**. Report parse accuracy per field, p50/p95 latency and cost per 1k queries.
  - Results go in `docs/evals/` and an ADR.
  - If Jev access isn't confirmed (D-07), the Jev row is marked "not run" with the reason.
  - This runs as an operator command with live calls, never in default CI.

### Claude's Discretion
- Exact vs HNSW for the vector leg (SRCH-03), decided by the benchmark the roadmap requires (~50k rows, `EXPLAIN ANALYZE`, full-K test) and recorded in an ADR.
- tsvector config details (the `simple` skills vector, OR-style matching), and synonym handling for SDE/SWE/C++/C#/.NET/Go.
- The OpenAPI → Zod + TanStack Query generator (Orval vs @hey-api/openapi-ts), the Scalar setup, and the Schemathesis config (OPS-10).
- The rate-limit counter table design (SEC-04: guest 60 searches/hour; Postgres fixed window).
- The `search_logs` schema details, and deploy migration + smoke wiring (OPS-03).
- Visual style, mobile layout, and loading/empty states, within shadcn/Tailwind. Follow the UI-SPEC if `/gsd-ui-phase 3` is run.
- The exact freshness decay, penalty sizes and candidate weight grids.

### Deferred Ideas (OUT OF SCOPE)
- **Jev in Phase 4.** Owner request, recorded here and in the ROADMAP Phase 4 notes:
  - Jev becomes an optional provider for the **dealbreaker set-aside** (yes/no plus confidence: "does this job break 'no fintech' / 'no 6-day on-site'?").
  - Jev also becomes an optional provider for the **fit label** (Strong / Good / Stretch).
  - Add an **ablation row: Jev vs Claude Haiku** on fit-label accuracy, latency and cost.
  - The same adoption gate as D-07 applies.
- Jev as a Phase 2 enrichment provider (remote / seniority / industry classification) is possible later. Phase 2 is not changed now.
- Account-backed settings (persist the parse-model choice) arrive with accounts in Phase 5.
- Learned ranking weights and persona cross-validation belong to Phase 4.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SRCH-02 | OR-style Postgres FTS for natural-language queries plus a `simple`-config skills vector | §Pattern 2 (OR tsquery from `plainto_tsquery`, verified today), new `skills_tsv` generated column (migration 0006), skill-token query rewrite reusing `SkillMatcher.token()` |
| SRCH-03 | Vector leg reliably returns top-K; exact vs HNSW chosen by benchmark; `EXPLAIN ANALYZE`; full-row-count test | §Pattern 3 and the benchmark evidence (HNSW at default ef_search returned 40 of 100, and 1 of 100 with a selective filter; exact returned 100 in ~17 ms at 50k rows). Spike S1 runs it on the prod VM. ADR 0004 |
| SRCH-04 | RRF with k=60 | §Pattern 3 SQL (fusion in SQL, `ORDER BY rrf DESC, id`) |
| SRCH-05 | Hard filters inside retrieval; unknown salary kept but penalised | §Pattern 4 (filter compiler with whitelisted clauses, D-08 geography, D-09 salary), set-aside counts |
| SRCH-06 | Guest search p95 < 300 ms at production index size; rules first, cached LLM fallback | §Pattern 5 (rules parser reusing Phase 2 normalisers), §Pattern 6 (fallback gate + timeout + cache in `llm_enrichment_cache`), spikes S1/S3 (50k synthetic rows, k6 at 20 rps) |
| SRCH-07 | `search_logs` row per search | §Pattern 8 schema (migration 0006) |
| EVAL-01 | Frozen deduplicated snapshot with precomputed embeddings, private if source terms need it, loads into an eval DB | §Pattern 10 (private repo release asset; the main repo is PUBLIC so its release assets would be public) |
| EVAL-02 | 10–15 personas, pooled candidates graded 0–3, human review subset, agreement | §Pattern 10 pooling + blind review CLI + weighted kappa code |
| EVAL-03 | nDCG@10, Recall@50, MRR, P@5 with bootstrap CIs | §Code Examples (metric definitions per SPEC §12.3, percentile bootstrap) |
| EVAL-08 | CI no-LLM eval on frozen snapshot with exact search; fail if nDCG@10 drops > 0.02 | §Pattern 10 CI job (skip without token), exact mode via `enable_indexscan = off` |
| UI-01 | Guest searches from the landing page without signup | §Pattern 12 (Next.js client search page, URL state, generated client) |
| UI-04 | Freshness line and link to original on every card | `posted_at`/`first_seen_at` + `last_seen_at`, `url`, `source` in the `JobResult` schema |
| UI-05 | Editable filter chips; relax-filter options on empty | `ParsedQuery.chips` + `auto_filters=false` round trip (§Pattern 5), relax counts (§Pattern 4) |
| UI-06 | Set-aside counts per filter | §Pattern 4 (`count(*) FILTER (...)` over a defined candidate universe) |
| OPS-03 | Migrations run on deploy; `/readyz` + one-search smoke | Already present: `deploy/deploy.sh` runs `alembic upgrade head` before `up`, and `smoke.sh` checks readyz. Add the search check (§Pattern 13) |
| SEC-04 | Guest 60 searches/hour via Postgres counter; 429 in the standard error shape | §Pattern 9 (UNLOGGED fixed-window table, `date_bin`, `ON CONFLICT … RETURNING`), error envelope handler |
| OPS-10 | OpenAPI export + staleness check; Zod + TanStack Query client; Scalar at `/api/docs`; Schemathesis | §Pattern 11: **@hey-api/openapi-ts 0.99.0** (zod v4 + `@tanstack/react-query` + SDK response validation), `scalar-fastapi` 1.10.1, `schemathesis` 4.33.4 CLI in a CI job |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- Stack pins: Python 3.14 + uv; FastAPI 0.165 (native SSE); Pydantic v2; SQLAlchemy 2.1 `[asyncio]`; psycopg 3.3; PostgreSQL 18 + pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg18-trixie` everywhere); fastembed bge-small (384-d, **no query prefix**); Next 16 + **TypeScript ~5.9.3 (never 7.x)** + Tailwind 4 + shadcn; APScheduler `<4`.
- **Verdict #1:** the vector leg must not silently return ≤ 40 rows (`hnsw.ef_search`), and a CTE referenced twice is materialised. Both confirmed again today (see Summary).
- **Verdict #3:** no `dict` fields in LLM output schemas. Use typed models and enums.
- **Verdict #8:** rate limits use a Postgres counter table. No slowapi, no in-memory store.
- No `temperature`/`top_p`/`top_k` sent to Haiku 5.5. All LLM calls go through `ranker/llm/`, with model IDs from env.
- Single datastore: Postgres only (no Redis, no vector DB).
- Reliability: LLM failure never breaks search (degraded mode).
- Privacy: no PII in logs or LLM call logs. Queries can contain PII, so they are never logged via structlog.
- Testing: no live network or real LLM calls in the default run. LLM tests use `@pytest.mark.llm`. respx for HTTP, fake provider, testcontainers PG18, `filterwarnings=error`.
- CI: ruff + pyright strict gate. Actions pinned to SHAs. Every setting goes in `.env.example` (a test enforces this).
- Data access: source attribution on every job card.
- GSD workflow: edits go through GSD commands.

## Summary

Most of this phase is SQL and contract plumbing on top of a Phase 2 schema that is already close. The `jobs` table already has a weighted `tsv` (english config), a 384-d `embedding`, a partial HNSW index, and every filter column the search needs (`remote_type`, `remote_eligibility`, `city`/`cities`, `seniority`, `employment_type`, `salary_*_inr_annual`, `salary_is_estimate`, `ghost_reasons`, `posted_at`/`first_seen_at`/`last_seen_at`, `source`, `url`). `skills_text` already holds search-safe tokens (`cplusplus csharp golang dotnet nodejs`). Two things are missing: a `simple`-config skills tsvector, and an OR-style query. The `english` config stems `nodejs`→`nodej` and `kubernetes`→`kubernet`, and it reduces raw `C++`/`C#` in a description or query to `'c'` (verified today).

Empirical checks today (local Docker, laptop CPU, synthetic random 384-d vectors, 50k rows, 42.5k active, one parallel worker or none):
- **Exact** filtered top-100 took 16–21 ms.
- **HNSW** (`m=16, ef_construction=64`, partial index) at default `ef_search=40` returned **40 rows** unfiltered and **1 row** with a ~3.6% selective filter. With `ef_search=200` it returned 100 unfiltered but **8** with the selective filter. Only `iterative_scan=relaxed_order` returned 100, in ~16 ms, the same as exact.
- A CTE referenced by both legs was materialised (`CTE Scan on base`).

The production index is about 2k active jobs today (1,838 canonical), so exact search is the obvious default: deterministic for the eval gate, perfect recall, and cheap. HNSW stays a config switch. Spike S1 re-measures on the Azure B2ls_v2 VM with realistic text at ~50k rows before the ADR is written.

On the open items:
- **Jev** is real and documented on TypeSafe's own docs: `POST https://api.typesafe.ai/v1/systemone`, bearer key, Choice/Score/Noul/Box primitives, pinned IDs `jev-1.13.0` and `jev-1.14.0`, $0.042 per million input tokens with free output. But the TypeSafe homepage still says "early access", and the "waitlist removed / $5 free credit" claims appear only on blogs. Treat Jev as **behind a flag, with access to be confirmed by the owner** (human checkpoint). Implement it with plain `httpx`, not the SDK (the SDK pulls in `httpx2`).
- **OPS-10:** use **@hey-api/openapi-ts**. One config produces Zod v4 schemas, TanStack Query hooks and an SDK that validates responses at runtime (`validator: { response: 'zod' }`, verified in the 0.99.0 type definitions).

**Primary recommendation:** Build one SQL round trip with exact vector search (`set_config('enable_indexscan','off', true)`), OR-style english tsquery plus a `simple` skills tsquery, RRF k=60 in SQL, filters compiled from a whitelist, four light signals plus the company cap in Python, and a `search_logs` row in the same connection. Gate everything with an eval CLI that runs the same code path against a private snapshot.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Query parsing (rules + fallback) | API / Backend (`ranker/search/parse.py`) | LLM gateway | Reuses Phase 2 normalisers. Must run in degraded mode |
| Query embedding | API / Backend (threadpool) | — | fastembed already loaded in `app.state.embedder` |
| Lexical + vector retrieval, RRF, filters, set-aside counts | Database | API (SQL composition) | Single round trip. Filters inside retrieval (SRCH-05) |
| Light signals, company cap, "why it matched" line | API / Backend (`ranker/search/rank.py`) | — | Pure Python, unit-testable, versioned by `ranking.yaml` |
| Rate limiting | Database (counter table) | API dependency | Verdict #8 |
| Search logging | Database | API | Same connection as the search |
| Chips, relax suggestions, cards, settings panel | Browser / Client | — | Client component with URL state. Same-origin `/api` via Caddy |
| Parse-model preference | Browser (localStorage) | API (query param) | No accounts until Phase 5 |
| API contract types | Build-time codegen (web) | API (OpenAPI export) | Drift fails `tsc` and the CI staleness check |
| Eval (snapshot load, pooling, metrics, gate) | Operator CLI / CI | Database (eval DB) | Same search code path as production |

## Standard Stack

### Core (already installed, reuse)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PostgreSQL + pgvector | 18 / 0.8.7 | FTS, vectors, counters, logs | Project single datastore [VERIFIED: compose.prod.yaml, local image] |
| FastAPI | 0.165.x | `/api/jobs/search`, OpenAPI | Already pinned in `backend/pyproject.toml` [VERIFIED: codebase] |
| SQLAlchemy async + psycopg 3 | 2.1.9 / 3.3.9 | `text()` SQL, transactions | Already used by Phase 2 [VERIFIED: codebase] |
| fastembed | 0.9.1 | Query embedding | `Embedder` protocol exists [VERIFIED: codebase] |
| anthropic | 2.12.x | Haiku fallback parse through `LLMGateway.structured` | Existing gateway [VERIFIED: codebase] |
| httpx + respx | 0.28 / 0.23 | Jev provider over plain HTTP; tests | Already pinned. Avoids the SDK's `httpx2` dependency [VERIFIED: PyPI typesafe-sdk 0.7.5 requires `httpx2>=2.0.0`] |
| Next.js / React / TS | 16.4.6 / 19.3.1 / ~5.9.3 | Search UI | Already in `web/package.json` [VERIFIED: codebase] |

### New (this phase)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | 2.5.x | Bootstrap CIs, kappa, metric maths | Already transitive via fastembed. **Declare explicitly** because eval code imports it [VERIFIED: PyPI 2.5.5, 2026-11-15] |
| scalar-fastapi | 1.10.1 | Scalar reference at `/api/docs` | Official Scalar integration (repo `scalar/scalar/integrations/fastapi`); deps are only fastapi/pydantic/typing-extensions [VERIFIED: PyPI 2026-11-19] |
| schemathesis (dev) | 4.33.4 | Property-based API fuzzing | OPS-10. Needs `hypothesis<7` and `pytest<10`, which are compatible with the current pins [VERIFIED: PyPI 2026-11-12] |
| @hey-api/openapi-ts (devDep, **exact pin**) | 0.99.0 | Generate types, Zod, SDK, TanStack Query hooks | Pre-1.0 and changes fast, so pin exactly. Peer `typescript >=5.5.3`. Node ≥ 22.18 (CI uses 24.22.3) [VERIFIED: npm] |
| zod | 4.6.x | Runtime response validation (generated) | hey-api zod plugin `compatibilityVersion: 3 \| 4 \| 'mini'` [VERIFIED: hey-api 0.99.0 d.ts] |
| @tanstack/react-query | 5.108.x | Query cache, "Load more" | Matches the CLAUDE.md stack [VERIFIED: npm] |
| k6 (tool, Docker `grafana/k6`) | pin a digest at spike time | 20 rps load test | Not installed locally. Run via Docker on the VM [ASSUMED tag] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| @hey-api/openapi-ts | Orval 8.49 | Orval is older (since 2020) and has a separate `zod` target, but runtime response validation of the react-query client needs a custom mutator. hey-api does it with one SDK option. Orval would be the pick if hey-api's churn bites. |
| Plain httpx for Jev | `typesafe-sdk` 0.7.5 | The SDK adds `httpx2`, `truststore` and its own retry layer next to tenacity. One POST endpoint isn't worth that. |
| Schemathesis CLI against uvicorn in CI | In-process `schemathesis.openapi.from_asgi` + pytest | In-process is attractive, but it's unclear whether the app lifespan (engine, embedder) runs [ASSUMED]. The CLI against a real uvicorn + service Postgres is unambiguous. |
| Exact vector search | HNSW + `ef_search=200` + `iterative_scan=relaxed_order` | Equal latency at 50k in today's test, but non-deterministic (bad for the CI gate) and needs a re-sort. Keep it as a config switch. |
| UNLOGGED counter table | Logged table | UNLOGGED skips WAL. Counters vanish after a crash, which is acceptable for rate limits. |

**Installation:**
```bash
cd backend && uv add "numpy>=2.5,<3" "scalar-fastapi>=1.10,<2" && uv add --dev "schemathesis>=4.33,<5"
cd web && npm i zod@^4.6 @tanstack/react-query@^5.108 && npm i -D --save-exact @hey-api/openapi-ts@0.99.0
```

## Package Legitimacy Audit

slopcheck could not be installed in this session (the environment blocked `pip install`). Per protocol, **every new package below is `[ASSUMED]`**, and the planner must gate each install behind a `checkpoint:human-verify`. The registry evidence was gathered read-only.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| numpy | PyPI | 15+ yrs | very high | github.com/numpy/numpy | not run | Approved-pending checkpoint (already transitive) |
| scalar-fastapi | PyPI | since 2024-04, 26 releases | not checked | github.com/scalar/scalar (integrations/fastapi) | not run | Checkpoint |
| schemathesis | PyPI | since 2019-08, 510 releases | not checked | github.com/schemathesis/schemathesis | not run | Checkpoint |
| @hey-api/openapi-ts | npm | since 2024-03 | not checked | github.com/hey-api/hey-api | not run | Checkpoint; no postinstall script |
| zod | npm | since 2020-03 | very high | github.com/colinhacks/zod | not run | Checkpoint; no postinstall |
| @tanstack/react-query | npm | since 2022-07 | very high | github.com/TanStack/query | not run | Checkpoint; no postinstall |
| typesafe-sdk | PyPI | since 2026-09 | — | github.com/typesafe-ai/typesafe-sdk-python | not run | **Not recommended** (use httpx) |

**Packages removed due to slopcheck [SLOP] verdict:** none (not run).
**Packages flagged as suspicious [SUS]:** none (not run). All are `[ASSUMED]` pending the human checkpoint.

## Architecture Patterns

### System Architecture Diagram

```
Browser (Next.js client page, URL = ?q=..&city=..&auto=0&parse_model=haiku)
   │  GET /api/jobs/search  (generated SDK, zod-validated response)
   ▼
Caddy ──/api/*──► FastAPI
   │
   ├─► rate_limit dependency ──► Postgres rate_limit_counters (INSERT..ON CONFLICT..RETURNING) ──► 429 envelope
   │
   ├─► QueryParser.rules(q) ── uses cities.yaml, salary/seniority normalisers, SkillMatcher, synonyms.yaml
   │        │ residue looks like a filter AND auto_filters AND provider available AND budget ok?
   │        ├─yes─► cache lookup llm_enrichment_cache(sha256(norm_q|provider|prompt_v))
   │        │          miss ─► LLMGateway.structured(provider=haiku|jev, timeout ≤ 1.2 s) ─► fill unset fields only
   │        └─no / error ─► rules-only, degraded=true (if the fallback was wanted but unavailable)
   │
   ├─► embed(query_text) in threadpool (LRU cache)
   │
   ├─► ONE transaction:
   │      set_config('enable_indexscan','off',true)        [exact mode]
   │      hybrid SQL: lex leg (OR tsquery ∪ skills tsquery) ∥ vec leg (exact) → RRF k=60 → top 100
   │      set-aside SQL: unfiltered universe → count(*) FILTER per filter
   │      hydrate rows (title, company, salary, …)
   │      INSERT search_logs … RETURNING id
   │
   ├─► rank.py: rrf_norm + freshness + region certainty + salary-known − ghost; company cap (≤2 in top 10)
   │
   └─► SearchResponse {items[20], next_cursor, search_log_id, parsed{chips,source}, set_aside, relax, degraded}
```

### Recommended Project Structure
```
backend/
├── config/ranking.yaml            # weights, k's, vector_mode, version (D-12)
├── config/query_synonyms.yaml     # sde→software engineer, swe, frontend/front-end, …
├── migrations/versions/0006_search.py   # skills_tsv + GIN, search_logs, rate_limit_counters
├── ranker/search/
│   ├── models.py      # SearchRequest/Filters/ParsedQuery/JobResult/SearchResponse (Pydantic = contract)
│   ├── parse.py       # rules parser + fallback orchestration + cache
│   ├── sql.py         # filter compiler + hybrid SQL + set-aside SQL
│   ├── rank.py        # signals, company cap, why-matched line
│   ├── service.py     # search() used by API, eval and smoke
│   └── ratelimit.py
├── ranker/api/search.py, ranker/api/errors.py (envelope handlers), ranker/api/meta.py (active count, parse models)
├── ranker/llm/providers/jev.py    # JSON-schema enum → Choice questions
├── ranker/evals/ranking_eval.py, metrics.py, snapshot.py, parse_eval.py
└── ranker/openapi_export.py       # writes docs/openapi.json
evals/ (repo root, public)
├── data/personas.jsonl, judgments.jsonl, snapshot.sha256, parse_queries.jsonl
├── baseline.json
└── results/<ts>_<sha>.json
web/src/lib/api/ (generated, committed) · web/openapi-ts.config.ts · web/src/app/page.tsx (search)
```

### Pattern 1: Skills vector (SRCH-02)
**What:** a separate generated column `skills_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', skills_text)) STORED` plus a GIN index. Adding a stored generated column rewrites the table, which takes seconds at 2k–50k rows under an ACCESS EXCLUSIVE lock. That's fine for the deploy-time migration.
**Query side:** run `SkillMatcher.extract(q)` on the query, map each canonical skill to `matcher.token()` (`cplusplus`, `csharp`, `golang`, `dotnet`, `nodejs`), and **remove the matched spans from the english lexical text**, so `C++` doesn't also become an OR-match on `'c'`. Validate tokens with `^[a-z0-9]+$` before building `to_tsquery('simple', 'cplusplus | golang')`.
**Go caveat:** `go` is `ambiguous` in `skills.yaml` with context regexes like `\bGo (?:developer|engineer…)`. Short queries ("remote go jobs") may not trigger it. Add a **query-mode** relaxation: in queries of 8 tokens or fewer, accept ambiguous aliases listed in an explicit `query_ok` allow-list (go, r, c, node). Test it.
**Synonyms:** a small YAML map applied at parse time (`sde|swe|sdet → "software engineer"`, `front-end → frontend`, `ml → "machine learning"`). Don't use a Postgres thesaurus dictionary: it needs files in the server's `tsearch_data`, which the stock pgvector image doesn't ship.

Verified tokenisation (PG18, today):
| Input | english | simple |
|---|---|---|
| `C++ developer` | `'c' 'develop'` | `'c' 'developer'` |
| `C# .NET engineer` | `'c' 'net' 'engin'` | `'c' 'net' 'engineer'` |
| `Go developer` | `'go' 'develop'` | `'go' 'developer'` |
| `Node.js backend` | `'node.js' 'backend'` | same |
| `NodeJS` | `'nodej'` | `'nodejs'` |
| `ASP.NET Core` | `'asp.net' 'core'` | same |
| skills_text tokens | `kubernet nextj nodej reactj` (stemmed) | unstemmed |

### Pattern 2: OR-style lexical query (SRCH-02)
`websearch_to_tsquery`/`plainto_tsquery` AND every term. Verified today: `'I want a senior backend role using python and kafka'` does **not** match "We need a senior backend developer" with AND, but does with OR. The safe OR conversion:
```sql
-- :lex_text is user-derived and always a bound parameter. plainto output only ever contains
-- quoted lexemes joined by ' & ' (and '<->' for phrases), so the text→tsquery cast cannot fail.
CASE WHEN numnode(plainto_tsquery('english', :lex_text)) = 0 THEN NULL
     ELSE replace(plainto_tsquery('english', :lex_text)::text, ' & ', ' | ')::tsquery END
```
Never pass user text to `to_tsquery`, because a syntax error becomes a 500. Strip filter words (city, salary, "remote ok", "LPA") from `lex_text` **before** this, because they are already hard filters and OR-matching `'remot' | 'ok' | '10'` floods the GIN bitmap. An empty query (stopwords only) gives `numnode = 0`, so use the vector leg only. Postgres emits a NOTICE here, not an error.

### Pattern 3: Hybrid SQL (SRCH-03/04)
**Rules:**
- No shared `base` CTE: each leg queries `jobs` directly.
- Every ORDER BY has an `id` tie-breaker.
- `row_number()` sits outside the LIMIT subquery, so an HNSW-mode plan can still use the index.
- Mode switch: `vector_mode: exact` in `ranking.yaml` runs `SELECT set_config('enable_indexscan','off', true)`. That disables plain index scans (HNSW is one) for this transaction only. GIN and btree **bitmap** scans stay enabled. `hnsw` mode runs `set_config('hnsw.ef_search','200',true)` and `set_config('hnsw.iterative_scan','relaxed_order',true)`.
- Use `set_config(...)`, not `SET LOCAL x = %s`. psycopg 3 binds server-side, and `SET` can't take parameters.
```sql
WITH q AS (
  SELECT CAST(:qvec AS vector(384)) AS qv, <or_tsquery_expr> AS tq,
         CASE WHEN cardinality(CAST(:skill_tokens AS text[])) = 0 THEN NULL
              ELSE to_tsquery('simple', array_to_string(CAST(:skill_tokens AS text[]), ' | ')) END AS sq
),
lex AS (
  SELECT id, row_number() OVER (ORDER BY score DESC, id) AS r FROM (
    SELECT j.id, ts_rank_cd(j.tsv, q.tq) + :w_skill * coalesce(ts_rank_cd(j.skills_tsv, q.sq), 0) AS score
    FROM jobs j, q
    WHERE j.status = 'active' AND j.canonical_job_id IS NULL
      AND (j.tsv @@ q.tq OR j.skills_tsv @@ q.sq)
      {filter_sql}
    ORDER BY score DESC, j.id LIMIT :lex_k) s
),
vec AS (
  SELECT id, dist, row_number() OVER (ORDER BY dist, id) AS r FROM (
    SELECT j.id, j.embedding <=> q.qv AS dist
    FROM jobs j, q
    WHERE j.status = 'active' AND j.canonical_job_id IS NULL AND j.embedding IS NOT NULL
      {filter_sql}
    ORDER BY dist, j.id LIMIT :vec_k) s
)
SELECT u.id, sum(1.0 / (:rrf_k + u.r)) AS rrf,
       min(u.r) FILTER (WHERE u.leg = 'lex') AS lex_rank,
       min(u.r) FILTER (WHERE u.leg = 'vec') AS vec_rank,
       min(u.dist) AS dist
FROM (SELECT id, r, 'lex' AS leg, NULL::float8 AS dist FROM lex
      UNION ALL SELECT id, r, 'vec', dist FROM vec) u
GROUP BY u.id ORDER BY rrf DESC, u.id LIMIT :fused_k;
```
`{filter_sql}` holds **compiled clauses from a whitelist** (Pattern 4). Their values are always bound and never interpolated. Don't use one static SQL with `(:x IS NULL OR …)` branches: psycopg auto-prepares after 5 executions, and a generic plan can't prune those branches [ASSUMED: plan-quality impact unmeasured. Check in spike S1].

**Full-K test (SRCH-03):** insert 300 matching active rows with FakeEmbedder vectors, request `vec_k=100` with and without a 10% filter, and assert `count(vec) == 100`. Also assert that `EXPLAIN (FORMAT JSON)` of the vector leg shows no `CTE Scan` and, in exact mode, no `Index Scan using jobs_embedding_hnsw`. In hnsw mode it must show that index.

### Pattern 4: Filters, geography and set-aside counts (SRCH-05, UI-05/06, D-08/09/10)
The filter compiler turns `Filters` into named predicates (each a SQL boolean expression):

| Filter | Predicate (bound params) |
|---|---|
| `geo` (default India, D-08) | `(j.country_code = 'IN' OR (j.remote_type = 'remote' AND j.remote_eligibility IN ('worldwide','india','apac','unknown')))`. `unknown` stays in but is penalised and badged |
| `city` (+ `remote_ok`) | `(j.city = ANY(:cities) OR j.cities && :cities [OR (j.remote_type='remote' AND j.remote_eligibility IN ('worldwide','india','apac'))])` |
| `work_mode` | `j.remote_type = ANY(:modes)` |
| `seniority` | `j.seniority = ANY(:sen || 'unknown')`. Recommend keeping `unknown` with a penalty; a strict-mode chip toggle drops it |
| `employment_type` | `j.employment_type = ANY(:types)` |
| `posted_within` | `coalesce(j.posted_at, j.first_seen_at) >= now() - make_interval(days => :days)` |
| `min_salary` | `(j.salary_max_inr_annual >= :min OR j.salary_min_inr_annual >= :min OR (j.salary_min_inr_annual IS NULL AND j.salary_max_inr_annual IS NULL))` plus `AND NOT j.salary_is_estimate` when the "exclude estimates" chip is on |

The US-only / EU-only / other-region remote jobs that D-08 says to count are simply the rows the `geo` predicate fails.

**Set-aside universe:** "relevant before filtering" = unfiltered lex top-200 ∪ unfiltered vec top-200. One query computes, per filter `f`, `set_aside[f] = count(*) FILTER (WHERE NOT f)` and `relax[f] = count(*) FILTER (WHERE NOT f AND all_other_filters)`. The second is the "+N jobs" if only that filter were removed (D-10). The cost is two extra unfiltered legs, which spike S1 measures. If it's too slow, run it only when results < 20 or lazily via `/api/jobs/search/relax`. Label the UI honestly: "Of the 200 most relevant jobs, 34 are below ₹10 LPA".

### Pattern 5: Rules parser and chips round trip (SRCH-06, UI-05)
- Reuse Phase 2 code: `ranker.ingest.normalize.location` (city index, aliases such as bangalore/blr; metro groups Mumbai/Navi Mumbai/Thane), `salary.parse_salary_text`/`to_inr_annual` (LPA, lakh, `10L+`, CTC), `seniority.infer_seniority` (fresher, junior, senior, lead, intern, "5+ years"), and `SkillMatcher`.
- Output `ParsedQuery{lex_text, embed_text, skills, filters, chips[{field, value, label, source: rules|haiku|jev|user}], residue[], parse_source, prompt_version}`.
- **Explicit beats parsed:** the request carries `q` plus optional explicit filter params and `auto_filters: bool = true`. After the user edits a chip, the client sends explicit filters with `auto_filters=false` (stored in the URL), so a removed chip never comes back.
- **Embedding text:** use the role/skill text without the filter words. bge has no query prefix (CLAUDE.md #11). Keep an in-process LRU (about 2k entries) keyed by `(model, embed_text)`.

### Pattern 6: Per-request provider and the fallback gate (D-06/D-07)
Today `build_gateway` binds one provider from `settings.llm_provider`. Minimal extension that keeps `cost.py` untouched:
```python
ParseProvider = Literal["haiku", "jev"]

class LLMGateway:
    def __init__(self, engine, settings, provider: Provider,
                 extra: Mapping[str, tuple[Provider, str]] | None = None): ...
        # extra = {"jev": (JevProvider(...), settings.jev_model)}

    def available(self, provider: str | None = None) -> bool: ...
    def providers(self) -> list[str]: ...    # ["haiku"] + (["jev"] if jev enabled & key)

    async def structured(self, *, ..., provider: str | None = None) -> T:
        prov, model = (self._provider, self._model_for(tier)) if provider in (None, "haiku") \
                      else self._extra[provider]
        # _reserve(model=...) / cost_usd(model, ...) unchanged → add jev prices to llm_prices.yaml
```
- Add `jev-1.13.0` and `jev-1.14.0` to `config/llm_prices.yaml` with `input: 0.042, output: 0` [CITED: docs.typesafe.ai/models]. The budget lock and `llm_calls` rows then work as-is, and `llm_calls.model` records the pinned ID.
- **Settings:** `JEV_ENABLED=false`, `TYPESAFE_API_KEY=`, `JEV_MODEL=jev-1.14.0`, `TYPESAFE_BASE_URL=https://api.typesafe.ai`. Add a validator that rejects `jev-latest`/`jev-preview` (D-07). All go into `.env.example`, which `test_env_example` enforces.
- **API:** `parse_model: Literal["haiku","jev"] = "haiku"` query param. If `jev` is requested but disabled, fall back to haiku and report `parsed.parse_source`. `GET /api/meta` returns `{active_jobs, parse_models: [...]}`, which drives the settings panel (Jev hidden unless enabled) and the hero count.
- **JevProvider.parse(schema):** build Choice questions from `schema.model_json_schema()`. Every property must be an `enum` (else raise at construction, so a test catches it): `criteria = {value: description}`, `instructions = field description`, `state = user message text`. Map `answers[field].choice` back into `schema(**...)`. `input_tokens = usage.input_tokens`, `output_tokens = 0`. Below a confidence threshold (config, e.g. 0.5), treat the answer as "unspecified". Choice allows ≤ 255 options [CITED: docs.typesafe.ai/primitives/choice.md], so the 26 cities in `cities.yaml` plus "none"/"other" fit.
- **Shared fallback schema** (one schema works for both Haiku structured outputs and Jev): fields `city` (enum from cities.yaml + `none` + `other`), `work_mode` (`remote|hybrid|onsite|remote_or_city|unspecified`), `seniority`, `employment_type`, `min_salary_lpa` (bucket enum `none,3,4,5,6,8,10,12,15,18,20,25,30,40,50`), `role_family` (D-17 measurement only). Build the enums with `Field(json_schema_extra={"enum": [...]})` plus a validator, since pyright strict can't type a dynamic Literal. **No dict fields** (verdict #3).
- **Gate:** call the fallback only if `auto_filters`, the residue contains a filter-ish token (unknown capitalised place word, unconsumed number or currency, "near/around/within/only/no"), the provider is available, and the budget allows. Use a hard `asyncio.timeout(1.2)` on the search path (smaller than `LLM_TIMEOUT_SECONDS=30`). Cache in `llm_enrichment_cache(content_hash=sha256(norm_q|provider|model), purpose='query_parse', prompt_version)`. That stores parsed filters only, never the query text. On timeout, budget or error: rules-only and `degraded=true`.

### Pattern 7: Light signals and company cap (D-11/12/13)
`config/ranking.yaml` (version → `pipeline_version = sha256(ranking.yaml bytes + parser prompt_version)[:12]`):
```yaml
version: 1
retrieval: {lexical_k: 100, vector_k: 100, rrf_k: 60, fused_k: 100, vector_mode: exact, skill_rank_weight: 0.5}
guest_weights: {rrf: 1.0, recency: 0.15, region_certain: 0.10, salary_known: 0.05, ghost_penalty: 0.25, unknown_seniority_penalty: 0.05}
recency_half_life_days: 10     # exp(-age/14) ≈ "about 2 weeks" — tune on eval
diversity: {max_per_company_top10: 2}
page_size: 20
```
- `rrf_norm = rrf / (2/(rrf_k+1))`. That's a fixed range, never min-max within the candidate set (PITFALLS #7).
- `recency = exp(-age_days/τ)`, with age from `coalesce(posted_at, first_seen_at)`.
- `region_certain = 1` if eligibility ∈ {india, apac, worldwide} or country IN, else 0.
- `salary_known = 1` only when a salary filter is set and the salary is non-null.
- `ghost = 1` if `ghost_reasons` is non-empty.
- **Company cap:** walk the sorted list. A third (or later) job from a company within the top 10 is deferred to just after position 10, keeping relative order. Stable and unit-tested.
- **Why it matched (rule-based):** matched query skills ∩ job skills (top 3), city or remote badge, "posted Nd ago". Never uses the LLM.

### Pattern 8: `search_logs` (SRCH-07)
`users`/`conversations` don't exist yet (Phase 5), so there are no FKs now. Add them later.
```sql
CREATE TABLE search_logs (
  id UUID PRIMARY KEY DEFAULT uuidv7(),          -- PG18 native
  guest_id UUID, user_id UUID,                    -- FK to users added in Phase 5
  surface TEXT NOT NULL DEFAULT 'search' CHECK (surface IN ('search','feed','chat')),
  query_text TEXT,                                -- capped at 500 chars by the API; never logged to structlog
  filters JSONB NOT NULL DEFAULT '{}',            -- effective filters + chip sources
  parse JSONB NOT NULL DEFAULT '{}',              -- {source, provider, model, cached, residue_count}
  pipeline_version TEXT NOT NULL,
  results JSONB NOT NULL,                         -- top 20 returned: [{job_id, rank, score, features{rrf,lex_rank,vec_rank,cos,recency,region,salary_known,ghost}}]
  ranked_ids BIGINT[] NOT NULL DEFAULT '{}',      -- top fused_k ids for "Load more" cursor
  set_aside JSONB NOT NULL DEFAULT '{}',
  lex_hits INT, vec_hits INT,
  degraded BOOLEAN NOT NULL DEFAULT false,
  latency_ms INT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX search_logs_created ON search_logs (created_at);
```
"Load more" cursor = signed `{search_log_id, offset}`. Page 2 hydrates from `ranked_ids[offset:offset+20]`, dropping ids no longer active, so ordering is stable and there's no second retrieval. Add 90-day retention to an existing worker purge job. Store per-feature scores for the returned items only, to keep storage small.

### Pattern 9: Rate limit (SEC-04)
```sql
CREATE UNLOGGED TABLE rate_limit_counters (
  bucket TEXT NOT NULL,                 -- 'search:g:<guest uuid>' | 'search:ip:<hmac(ip)>'
  window_start TIMESTAMPTZ NOT NULL,
  count INT NOT NULL,
  PRIMARY KEY (bucket, window_start)
);
-- per request (autocommit, its own short transaction):
INSERT INTO rate_limit_counters (bucket, window_start, count)
VALUES (:bucket, date_bin(:window, now(), TIMESTAMPTZ '2000-01-01'), 1)
ON CONFLICT (bucket, window_start) DO UPDATE SET count = rate_limit_counters.count + 1
RETURNING count, window_start;
```
- **Identity:** there are no users yet, so set an opaque random `rk_gid` cookie (itsdangerous-signed like `session.py`; `path=/api`, HttpOnly, Secure, SameSite=Lax) on the first search. Guest limit 60/hour. A secondary per-IP bucket (e.g. 300/hour) stops cookie-clearing bypass.
- **IP:** behind Caddy, uvicorn `--proxy-headers` + `FORWARDED_ALLOW_IPS=*` (already set) gives the real client IP. That's safe only because no service except Caddy publishes ports (`check-prod-ports.sh`). Store the HMAC of the IP, never the raw IP (privacy).
- **Response:** 429 with `Retry-After` = seconds to the window end, body `{"error":{"code":"rate_limited","message":"Too many requests","details":{"retry_after":N}}}`.
- "Load more" doesn't count, only new searches.
- Prune windows older than 2 days in the worker.
- `RATE_LIMIT_ENABLED` setting (default true) lets the bench stack and the Schemathesis job turn it off.

### Pattern 10: Eval snapshot, pooling, grading, CI gate (EVAL-01/02/03/08)
- **Snapshot privacy:** the repo `6sLOGAN78/TopFit` is **PUBLIC** (verified via `gh repo view`), and release assets of a public repo are downloadable by anyone. So D-15's "private release asset" must live in a **separate private repo** (e.g. `TopFit-eval-data`). CI reads it with a fine-grained PAT scoped to that repo (Contents: read-only), stored as secret `EVAL_DATA_TOKEN`.
- **Snapshot build:** `ranker eval snapshot export` selects active canonical jobs, dedupes by `dedupe_key` plus near-duplicate embedding (cos > 0.97), and writes `job_pool_snapshot.jsonl.gz` (search columns + `embedding` + `skills_text`). Commit its sha256 to `evals/data/snapshot.sha256`.
- **Load:** `ranker eval snapshot load --dsn …` runs `alembic upgrade head`, then a COPY/INSERT into `jobs`, verifying the hash first. `tsv`/`skills_tsv` regenerate automatically (generated columns).
- **Personas** (public `evals/data/personas.jsonl`): `{id, query, explicit_filters?, notes}`, guest-style because there are no profiles until Phase 4.
- **Pooling:** union of the top 20 from lexical-only, vector-only, hybrid RRF, and hybrid + signals, which gives about 30–50 jobs per persona.
- **Grading:** `ranker eval grade` (operator, live LLM through the gateway, a fixed model recorded in each row) writes `judgments.jsonl {persona_id, job_id, grade, graded_by, reviewed:false, rubric_version}`.
- **Owner review:** `ranker eval review --blind --fraction 0.2 --seed 7` is an interactive TTY that shows persona + job without the Claude grade and writes `human_grade`, `reviewed:true`. Agreement report: exact %, within-±1 %, **quadratic-weighted kappa** (primary) and linear-weighted kappa. Per D-14, the human grade wins where they differ.
- **Eval-time spend:** run grading with an eval-specific budget or key so it doesn't consume the production `LLM_MONTHLY_BUDGET_USD` (PITFALLS cost note).
- **CI job `eval-gate`:**
```yaml
eval-gate:
  runs-on: ubuntu-24.04
  services:
    db: { image: pgvector/pgvector:0.8.7-pg18-trixie, env: {POSTGRES_PASSWORD: ci}, ports: ["5432:5432"],
          options: --health-cmd "pg_isready -U postgres" --health-interval 5s --health-retries 20 }
  steps:
    - uses: actions/checkout@<sha>
    - name: Fetch private snapshot
      id: snap
      env: { GH_TOKEN: "${{ secrets.EVAL_DATA_TOKEN }}" }
      run: |
        if [ -z "$GH_TOKEN" ]; then
          echo "::notice::Ranking eval skipped: EVAL_DATA_TOKEN not available (fork or no access)"
          echo "skip=true" >> "$GITHUB_OUTPUT"; exit 0; fi
        gh release download snapshot-v1 -R <owner>/TopFit-eval-data -p 'job_pool_snapshot.jsonl.gz' -D evals/data
        (cd evals/data && sha256sum -c snapshot.sha256)
    - if: steps.snap.outputs.skip != 'true'
      run: uv run ranker eval snapshot load && uv run ranker eval ranking --mode no-llm --baseline ../evals/baseline.json --max-drop 0.02
```
- **Query embeddings:** the eval embeds persona queries with the real fastembed model. Cache `FASTEMBED_CACHE_PATH` with `actions/cache` keyed on the fastembed version. This is network use in a dedicated job, not the default pytest run.
- **Determinism:** exact mode, `id` tie-breakers, snapshot embeddings, pinned uv.lock. Run the gate twice on `main` and diff the JSON. ONNX on different CPU types can shift query vectors in the last float bits [ASSUMED]. The 0.02 margin absorbs this, but check that two runs on CI produce byte-identical metrics.

### Pattern 11: API contract (OPS-10)
- **Export:** `python -m ranker.openapi_export` writes `json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"` to `docs/openapi.json`. CI regenerates it and runs `git diff --exit-code docs/openapi.json`.
- **Error envelope:** add handlers for `HTTPException`, `RequestValidationError` and a `RateLimited` exception that return `ErrorResponse{error:{code,message,details}}`. **Declare `responses={422: …, 429: …}` with `ErrorResponse`** on the routers. Otherwise Schemathesis `status_code_conformance`/`response_schema_conformance` fail, because FastAPI documents 422 as `HTTPValidationError` by default. Check in the exported JSON that the declared 422 replaced FastAPI's default [ASSUMED behaviour].
- **Scalar:** set `docs_url=None` in `create_app()` (today it's `/api/docs` = Swagger, which would clash), then:
```python
from scalar_fastapi import get_scalar_api_reference
@app.get("/api/docs", include_in_schema=False)
async def scalar_docs():
    return get_scalar_api_reference(openapi_url=app.openapi_url, title="TopFit API",
        scalar_js_url="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.82.1")  # pin, default is unversioned CDN
```
- **Codegen (web):** the web Docker build context is `web/`, so `../docs/openapi.json` isn't visible during `docker build`. Therefore **commit the generated `web/src/lib/api/`**, and CI runs `npm run gen:api && git diff --exit-code src/lib/api`. A breaking API change then shows up as a type error in `npm run typecheck`/`build`.
```ts
// web/openapi-ts.config.ts
import { defineConfig } from "@hey-api/openapi-ts";
export default defineConfig({
  input: "../docs/openapi.json",
  output: { path: "src/lib/api" },
  plugins: [
    "@hey-api/client-fetch",
    { name: "zod", compatibilityVersion: 4 },
    { name: "@hey-api/sdk", validator: { response: "zod" } },
    "@tanstack/react-query",
  ],
});
```
Option names were verified against the 0.99.0 type definitions (`validator.response`, `ZodCompatibilityVersion = 3 | 4 | 'mini'`, plugin `'@tanstack/react-query'`, client `'@hey-api/client-fetch'`). Confirm the exact plugin object shape by running the generator in Wave 0.
- **Schemathesis job:** uvicorn with `EMBEDDER=fake`, `LLM_PROVIDER=fake`, `RATE_LIMIT_ENABLED=false`, and a service Postgres seeded with ~50 fixture jobs. Then `uvx schemathesis==4.33.4 run http://127.0.0.1:8000/api/openapi.json --checks not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance` [CITED: schemathesis.readthedocs.io/en/stable/reference/checks/]. Admin endpoints must declare their 401/403/503 responses.

### Pattern 12: Web search UI (UI-01/04/05/06)
- The page uses `useSearchParams`, so in Next 16 wrap it in `<Suspense>`, or the build fails to prerender. The generated TanStack hooks drive search; `useInfiniteQuery` (or cursor state) drives "Load more".
- Example-query buttons write to the URL. The parse-model setting lives in localStorage and is appended as `parse_model`.
- Dev uses `API_PROXY_TARGET` rewrites (already present). Prod is same-origin via Caddy, so the client base URL is `""`.
- Run `/gsd-ui-phase 3` (`ui_phase: true` in config) before planning the UI plan, if it hasn't been run yet.

### Pattern 13: OPS-03 smoke
`deploy.sh` already does migrate → `up --wait`, and `smoke.sh` already checks readyz. Append:
```bash
curl -fsS "https://$host/api/jobs/search?q=software%20engineer" -o "$tmp/search.json" || true
check "one search returns items" jq -e '(.items | length) > 0 and (.degraded | type == "boolean")' "$tmp/search.json"
```
That's one guest search per deploy, well under 60/hour. Add `search.json` to the cleanup list.

### Anti-Patterns to Avoid
- **Shared `base` CTE for both legs:** it gets materialised and HNSW is never used (verified).
- **`websearch_to_tsquery` on sentences:** all terms ANDed, so zero lexical hits.
- **`to_tsquery(user_text)`:** a syntax error becomes a 500, and Schemathesis will find it.
- **Min-max normalising RRF or cosine within the candidate set:** noise amplification.
- **Re-running retrieval for "Load more":** order drifts as the index changes.
- **Running k6 against the prod rate limiter:** 429 after 60 requests; the test measures nothing.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| City/salary/seniority parsing | New regexes | Phase 2 `normalize/location.py`, `salary.py`, `seniority.py`, `SkillMatcher` | Already tested against real postings. Query and job sides stay consistent |
| TS types + Zod + query hooks | Hand-written fetch wrappers | @hey-api/openapi-ts | Drift detection is the whole point of OPS-10 |
| API fuzzing | Ad-hoc bad-input tests | Schemathesis | Finds NUL bytes, huge ints, unicode edge cases |
| Budget / cost accounting for Jev | Separate counter | Existing `LLMGateway._reserve` + `llm_prices.yaml` entries | Advisory-lock budget already correct |
| Query-parse cache | New table | `llm_enrichment_cache` (content_hash, purpose, prompt_version) | Same shape, already migrated |
| API reference HTML | Custom docs page | scalar-fastapi | One function |
| zstd/gzip | Third-party compressors | stdlib `gzip` | Snapshot is a few MB |

**Key insight:** the subtle parts of this phase (tokenisation, HNSW truncation, cast safety, plan stability) all fail silently. Every one needs a test that asserts row counts or plan shape, not just "returns 200".

## Common Pitfalls

### Pitfall 1: NUL bytes and oversized input give 500s
**What goes wrong:** Postgres rejects `\x00` in text, so psycopg raises and the API returns a 500. Schemathesis will generate this.
**How to avoid:** set `q: str = Query(max_length=500)` and strip control characters including `\x00` in the request model (validator). Cap array params (≤ 10 cities).
**Warning signs:** Schemathesis `not_a_server_error` failures on `/api/jobs/search`.

### Pitfall 2: Filter words flood the OR query
**What goes wrong:** `'remot' | 'ok' | '10' | 'lpa'` matches thousands of descriptions. `ts_rank_cd` then detoasts every matching `tsv`, and the latency of the lex leg explodes at 50k.
**How to avoid:** remove the spans the parser consumed from `lex_text`. Measure the lex leg separately in spike S1. If it's still slow, rank on title+skills weights only or cap with a GIN-friendly prefilter.
**Warning signs:** `lex_hits` near `lex_k` on every query; lex leg > 100 ms in `EXPLAIN ANALYZE`.

### Pitfall 3: Generic plans with nullable-param filters
**What goes wrong:** psycopg auto-prepares after 5 executions [CITED: CLAUDE.md stack notes], and the generic plan keeps every `(:x IS NULL OR …)` branch.
**How to avoid:** compose SQL from a whitelist of clauses, so only the active filters appear.

### Pitfall 4: Rate limiter breaks the load test, Schemathesis and the smoke test
**How to avoid:** `RATE_LIMIT_ENABLED=false` in the bench and Schemathesis stacks. Smoke uses one request. Declare 429 in OpenAPI anyway.

### Pitfall 5: Burstable VM credits distort k6
**What goes wrong:** Azure Standard_B2ls_v2 has a 30% base CPU, 60 initial credits, earns 36 credits/hour and banks up to 864 [CITED: learn.microsoft.com/azure/virtual-machines/sizes/b-series-cpu-credit-model]. A long run drains credits and then throttles to the base level. Note the roadmap still says "GCP e2-medium", but ADR 0000 records the Azure host.
**How to avoid:** run k6 for 5 minutes at 20 rps with credits banked, and record the credit balance before and after. Also report sustainable throughput at base CPU honestly.

### Pitfall 6: k6 with a fixed query list measures caches
**How to avoid:** use 200+ varied queries (personas + permutations), and report the embedding-cache hit rate.

### Pitfall 7: Benchmarking on random vectors
**What goes wrong:** today's synthetic test used uniform random vectors, which are a worst case for HNSW and unrealistic for lexical selectivity.
**How to avoid:** in spike S1, clone the ~2k real jobs ×25 into a separate `ranker_bench` database. Use new `source_job_id`s, small Gaussian jitter on embeddings (renormalised), and real description text. Never write into the prod DB.

### Pitfall 8: Removed chips come back
**How to avoid:** send `auto_filters=false` after any chip edit (Pattern 5).

### Pitfall 9: Public release asset
**What goes wrong:** with the public repo, a "private" asset isn't private.
**How to avoid:** use a separate private repo plus a fine-grained PAT.

### Pitfall 10: Jev free-text expectations
**What goes wrong:** Choice can't extract arbitrary numbers or names [CITED: docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md].
**How to avoid:** use bucketed enums for salary and the city roster. Rules do the extraction; Jev only picks.

### Pitfall 11: `useSearchParams` without Suspense
Next 16 fails the static prerender of `/` [ASSUMED: Next 15+ behaviour carried into 16; confirm in the build].

### Pitfall 12: Scalar CDN unpinned
The default `scalar_js_url` is unversioned jsDelivr [CITED: scalar.com FastAPI integration]. Pin the version.

## Code Examples

### Metrics and bootstrap (SPEC §12.3)
```python
import numpy as np

def dcg(grades: list[int], k: int) -> float:
    g = np.asarray(grades[:k], dtype=float)
    return float(((2**g - 1) / np.log2(np.arange(2, g.size + 2))).sum())

def ndcg_at_k(ranked_ids, judged: dict[int, int], k=10) -> float:
    ideal = dcg(sorted(judged.values(), reverse=True), k)
    return 0.0 if ideal == 0 else dcg([judged.get(i, 0) for i in ranked_ids], k) / ideal   # unjudged = 0; report unjudged@10 rate

def recall_at_k(ranked_ids, judged, k=50):        # relevant = grade >= 2, retrieval stage
    rel = {i for i, g in judged.items() if g >= 2}
    return float("nan") if not rel else len(rel & set(ranked_ids[:k])) / len(rel)

def mrr(ranked_ids, judged):                     # first grade-3
    return next((1 / (r + 1) for r, i in enumerate(ranked_ids) if judged.get(i, 0) == 3), 0.0)

def p_at_5(ranked_ids, judged):
    return sum(judged.get(i, 0) >= 2 for i in ranked_ids[:5]) / 5

def bootstrap_ci(per_persona: list[float], b=10_000, seed=20261122, alpha=0.05):
    x = np.asarray([v for v in per_persona if not np.isnan(v)])
    idx = np.random.default_rng(seed).integers(0, x.size, size=(b, x.size))
    means = x[idx].mean(axis=1)
    return float(x.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))
```
With 10–15 personas the CIs will be wide. Say so in `docs/evals/ranking.md`.

### Weighted Cohen's kappa (grades 0–3)
```python
def weighted_kappa(a: list[int], b: list[int], k: int = 4, kind: str = "quadratic") -> float:
    o = np.zeros((k, k))
    for x, y in zip(a, b, strict=True):
        o[x, y] += 1
    o /= o.sum()
    e = np.outer(o.sum(1), o.sum(0))
    i, j = np.indices((k, k))
    w = ((i - j) ** 2 if kind == "quadratic" else np.abs(i - j)) / (k - 1) ** (2 if kind == "quadratic" else 1)
    denom = (w * e).sum()
    return 1.0 if denom == 0 else 1 - (w * o).sum() / denom
```
Test it against a hand-computed 2×2/4×4 fixture. scikit-learn's `cohen_kappa_score(weights=...)` is the reference formula, but don't add sklearn as a dependency.

### Jev request (plain httpx)
```python
# Source: docs.typesafe.ai/introduction/quickstart.md, /primitives/choice.md, /api/system-one.md
resp = await client.post(f"{base}/v1/systemone", headers={"Authorization": f"Bearer {key}"}, json={
    "model": "jev-1.14.0",                       # pinned, never jev-latest (D-07)
    "state": query_text,
    "questions": {"city": {"type": "choice", "instructions": "Which city does the searcher want?",
                           "criteria": {"Bengaluru": None, "Pune": None, "none": "No city stated"}}},
})
# → {"model": "jev-1.14.0", "answers": {"city": {"type":"choice","choice":"Bengaluru","confidence":0.97,
#     "probabilities": {...}}}, "usage": {"input_tokens": 120, "output_tokens": 8}}
```
Errors: 401, 413, 422, 429, 529 (overloaded). Retry 429/529 with tenacity backoff [CITED: docs.typesafe.ai/api/errors.md].

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| HNSW returns ≤ ef_search rows under filters | `hnsw.iterative_scan` | pgvector 0.8.0 | Only matters if HNSW mode is chosen |
| `gen_random_uuid()` keys | `uuidv7()` native | PostgreSQL 18 | Time-ordered `search_logs` ids |
| Swagger UI at `/docs` | Scalar via `scalar-fastapi` | — | Requirement OPS-10 |
| Orval/openapi-typescript + hand Zod | hey-api plugin pipeline with SDK validators | hey-api 0.8x–0.99 | One generator for types, zod, hooks |
| Python zstd via third-party | `compression.zstd` stdlib | Python 3.14 | Optional; gzip is enough |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Jev access is open to new accounts and gives free credit ($5) | Summary, Pattern 6 | Jev row "not run"; ship Haiku-only behind the flag (D-07 path) |
| A2 | Declaring `responses={422: ErrorResponse}` replaces FastAPI's default 422 schema | Pattern 11 | Schemathesis conformance failures; fall back to keeping FastAPI's 422 shape |
| A3 | Schemathesis in-process ASGI may not run the lifespan | Alternatives | Only affects the alternative; recommended CLI path unaffected |
| A4 | Static SQL with nullable branches gets poor generic plans | Pattern 3 / Pitfall 3 | Low; the whitelist compiler is cleaner anyway |
| A5 | ONNX float drift across CI CPU types is below the 0.02 gate | Pattern 10 | Flaky gate; mitigate by storing persona query vectors in the snapshot |
| A6 | `useSearchParams` needs Suspense in Next 16 | Pitfall 11 | Build error, quick fix |
| A7 | k6 Docker tag/digest | Standard Stack | None; pin at spike time |
| A8 | All new packages are legitimate (slopcheck not run) | Package audit | Human checkpoint before install |
| A9 | Exact search on the B2ls_v2 VM at 50k real rows stays well under 300 ms p95 | Summary | Laptop measured ~17–21 ms; VM is slower and burstable. Spike S1 decides; HNSW iterative mode is the fallback |

## Open Questions

1. **Exact vs HNSW on the real VM (SRCH-03) (RESOLVED: deferred to measurement in plan 03-12 Tasks 2–3).** Carried as **Spike S1**: bench DB `ranker_bench` on the VM, about 50k cloned real rows. Measure p50/p95 of each leg and the full query with `EXPLAIN (ANALYZE, BUFFERS)`, exact vs hnsw(ef=200, relaxed_order), with 0/1/3 filters. Write ADR `0004-vector-search-mode.md`. Default stays exact unless p95 of the full query exceeds ~150 ms.
2. **Tokenisation coverage.** **RESOLVED** by today's PG18 check (table in Pattern 1). The plan turns it into tests: C++, C#, .NET, Go, Node.js, ASP.NET, SDE/SWE queries each return the expected seeded job and not decoys (a C-only job for C++).
3. **k6 at 20 rps with 1 uvicorn worker (RESOLVED: deferred to measurement in plan 03-12 Tasks 2–3).** Carried as **Spike S3**: bench compose project on the VM pointing at `ranker_bench`, `RATE_LIMIT_ENABLED=false`, LLM fallback off (`LLM_PROVIDER=fake` or no key). Thresholds-as-code `http_req_duration: ['p(95)<300']`, 5 minutes, 200+ queries. Record CPU credit balance. Results go in ADR 0004.
4. **Jev access (D-07).** **Partially resolved.** Contract, endpoint, pinned versions (`jev-1.13.0`, `jev-1.14.0`) and price are confirmed on TypeSafe docs. Access and free credit are blog-only. Carried as a **human checkpoint (Spike S4)**: the owner signs in at console.typesafe.ai, creates a key, and records whether a waitlist or credit exists. If no access, set `JEV_ENABLED=false`, keep the stub plus respx tests, and record it in ADR `0006-jev-adoption.md`. D-17 then reports the Jev row as "not run".
5. **Route path.** **RESOLVED.** Use `GET /api/jobs/search` (SPEC §13.3 path under the existing `/api` prefix, which leaves room for `/api/jobs/{id}`), plus `GET /api/meta`. CONTEXT's "/api/search" is a description, not a decision.
6. **Seniority unknowns.** **RESOLVED as a recommendation:** keep `unknown` under a seniority filter with a small penalty and a chip toggle for strict mode. Rationale: Phase 2 seniority coverage is partial, and dropping unknowns would hide many valid jobs. If the planner prefers strict mode, it's one predicate change.
7. **Who grades (D-14).** **RESOLVED:** `ranker eval grade` through the gateway with a fixed Claude model recorded in `graded_by` (e.g. `claude-sonnet-5-5`). About 600 grades at Sonnet prices is a few dollars, run with an eval key or budget. Human blind review covers 20%.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker + pgvector image | tests, spikes | ✓ | `pgvector/pgvector:0.8.7-pg18-trixie` local | — |
| Disk | images, bench | ⚠ 92% used, 15 GB free | — | Don't pull new large images locally. Run bench/k6 on the VM |
| uv | backend | ✓ | 0.9.18 (CI pinned 0.9.18) | — |
| Node | web codegen | ✓ local 22.23.4; CI/Docker 24.22.3 | hey-api needs ≥ 22.18 ✓ | — |
| gh CLI | release asset upload | ✓ | 2.98.0 | — |
| k6 | Spike S3 | ✗ local | — | `docker run grafana/k6` on the VM |
| slopcheck | package audit | ✗ (install blocked) | — | Human checkpoint per package |
| TypeSafe API key | Jev provider, D-17 | unknown | — | Flag off; stub + respx |
| Anthropic API key | Haiku fallback, grading, D-17 | prod has key? (not checked) | — | Degraded rules-only; OpenRouter Haiku entry already priced |

**Missing dependencies with no fallback:** none blocking.
**Missing with fallback:** k6 (Docker on VM), slopcheck (human checkpoint), TypeSafe key (flag off).

## Validation Architecture

(`workflow.nyquist_validation` is `false` in config, but the orchestrator asked for this section.)

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.2 + pytest-asyncio 1.5 (auto) + testcontainers PG18/pgvector 0.8.7 + respx + hypothesis; web: tsc/eslint/next build |
| Config file | `backend/pyproject.toml` `[tool.pytest.ini_options]` (`-m 'not llm'`, `filterwarnings=error`) |
| Quick run command | `cd backend && uv run pytest -q tests/test_search_*.py tests/test_parse_*.py -x` |
| Full suite command | `cd backend && uv run pytest -q && uv run pyright && uv run ruff check . && cd ../web && npm run typecheck && npm run build` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SRCH-02 | Sentence query gets lexical hits; C++/C#/.NET/Go/Node.js match the right seeded job, not decoys | integration | `pytest tests/test_search_lexical.py -x` | ❌ Wave 0 |
| SRCH-03 | Vector leg returns full K (100) with and without filters; plan has no CTE Scan; exact mode avoids HNSW | integration | `pytest tests/test_search_vector.py -x` | ❌ |
| SRCH-04 | RRF k=60 ordering, deterministic tie-break | unit + integration | `pytest tests/test_search_rrf.py -x` | ❌ |
| SRCH-05 | Each filter predicate; unknown salary kept and ranked lower; estimates toggle; D-08 geography; set-aside/relax counts | integration | `pytest tests/test_search_filters.py -x` | ❌ |
| SRCH-06 | Rules parse of the demo query; fallback only on residue; cache hit; degraded on budget/timeout/no key; per-request provider | unit (fake provider, respx for Jev) | `pytest tests/test_parse_rules.py tests/test_parse_fallback.py tests/test_llm_jev_provider.py -x` | ❌ |
| SRCH-06 (perf) | p95 < 300 ms at ~50k rows, 20 rps | manual spike (k6 on VM) | `docker run grafana/k6 run bench/search.js` | ❌ spike |
| SRCH-07 | One `search_logs` row per search with filters, features, pipeline_version, latency, degraded | integration | `pytest tests/test_search_logs.py -x` | ❌ |
| SEC-04 | 61st guest search in the window → 429 envelope + Retry-After; IP bucket; disabled flag | integration (time-machine) | `pytest tests/test_rate_limit.py -x` | ❌ |
| EVAL-01 | Snapshot export → load round trip, hash check, dedupe | integration | `pytest tests/test_eval_snapshot.py -x` | ❌ |
| EVAL-02 | Pooling union; blind review hides grade; kappa on known fixture | unit | `pytest tests/test_eval_pooling.py tests/test_eval_kappa.py -x` | ❌ |
| EVAL-03 | nDCG/Recall/MRR/P@5 on hand-computed fixtures; bootstrap reproducible with seed | unit (+ hypothesis bounds 0..1) | `pytest tests/test_eval_metrics.py -x` | ❌ |
| EVAL-08 | Gate fails on > 0.02 drop, passes otherwise; skip message without token | unit + CI job | `pytest tests/test_eval_gate.py -x` | ❌ |
| UI-01/04/05/06 | Search page renders cards, chips, set-aside, relax; types match generated client | build + manual UAT | `cd web && npm run typecheck && npm run build` | ❌ |
| OPS-03 | Smoke includes one search | CI deploy step | `bash -n deploy/smoke.sh` + post-deploy run | partial |
| OPS-10 | openapi.json fresh; generated client fresh; Schemathesis clean | CI | `uv run python -m ranker.openapi_export --check`; `npm run gen:api && git diff --exit-code`; `schemathesis run …` | ❌ |

### Sampling Rate
- **Per task commit:** the quick command for the touched module.
- **Per wave merge:** full backend suite + web typecheck/build.
- **Phase gate:** full suite green, Schemathesis job green, eval-gate run (with token) green twice with identical output, spikes S1/S3 recorded in ADR 0004.

### Wave 0 Gaps
- [ ] `tests/fixtures/search/jobs_seed.py`: a builder for deterministic seeded jobs (decoys for C/C++/C#, a Go job, a Node job, salary known/unknown/estimate, every eligibility value, ghost, multi-company), with FakeEmbedder vectors
- [ ] `tests/conftest.py`: a per-test transactional `jobs` cleanup fixture (the session container is shared)
- [ ] Add a `pytest` marker `eval` (excluded by default like `llm`) if the eval gate is also exposed as a test
- [ ] Web: `gen:api` script + first generation committed. Check that the hey-api config shape compiles

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (guests only this phase) | — |
| V3 Session Management | partial | Signed `rk_gid` cookie (itsdangerous, HttpOnly, Secure, SameSite=Lax, path=/api) |
| V4 Access Control | yes (admin routes unchanged) | Existing `ADMIN_TOKEN`; eval CLI is operator-only |
| V5 Input Validation | yes | Pydantic request model: `max_length`, control-char strip, enum params, list caps. Bound SQL params; tsquery only via `plainto_tsquery` |
| V6 Cryptography | yes (light) | HMAC-SHA256 of IP with `SESSION_SECRET`; no custom crypto |
| V7 Error/Logging | yes | Error envelope with no stack traces. Never log `q` via structlog (PII). `search_logs.query_text` capped and retained 90 days |
| V11 Business Logic / abuse | yes | Postgres rate limits (guest + IP); LLM budget gate; fallback timeout |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via filters/q | Tampering | Whitelisted clause compiler; all values bound; no f-string user data |
| tsquery syntax DoS (500s) | DoS | `plainto_tsquery` + safe OR cast; Schemathesis |
| Rate-limit bypass by clearing cookies | DoS / cost | Secondary IP bucket |
| LLM cost abuse via unique queries | DoS (wallet) | Fallback gate (residue only), cache, budget, rate limit |
| Prompt injection in query text to the parse model | Tampering | Output is enum-only (Haiku structured output / Jev Choice), so it can't produce free text; values validated |
| Private snapshot leak | Information disclosure | Separate private repo; PAT scoped read-only; hash committed, data not |
| Scalar CDN supply chain | Tampering | Pinned versioned `scalar_js_url` |
| Forged X-Forwarded-For | Spoofing | Only Caddy reachable (no published api port); Caddy overwrites the client IP |

## Sources

### Primary (HIGH confidence)
- Local empirical checks, 2026-11-22, `pgvector/pgvector:0.8.7-pg18-trixie`: tokenisation table, OR-tsquery conversion, AND vs OR match, HNSW 40/1/8/100 row counts, exact 16–21 ms at 50k, CTE materialisation plan.
- Codebase: `backend/migrations/versions/0004_jobs_index.py`, `backend/ranker/llm/client.py`, `cost.py`, `providers/*`, `settings.py`, `embeddings/*`, `ingest/skills.py`, `config/skills.yaml`, `config/cities.yaml`, `deploy/*`, `.github/workflows/ci.yml`, `web/*`, `docs/decisions/0000-hosting-and-memory.md`, `.planning/research/PITFALLS.md`.
- [TypeSafe docs index](https://docs.typesafe.ai/llms.txt), [quickstart](https://docs.typesafe.ai/introduction/quickstart.md), [models and pricing](https://docs.typesafe.ai/models.md), [choice primitive](https://docs.typesafe.ai/primitives/choice.md), [system-one API](https://docs.typesafe.ai/api/system-one.md), [errors](https://docs.typesafe.ai/api/errors.md), [pre-parsed value extraction](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md), [typesafe.ai homepage](https://typesafe.ai) ("early access").
- PyPI JSON: typesafe-sdk 0.7.5 (deps incl. httpx2), scalar-fastapi 1.10.1, schemathesis 4.33.4, numpy 2.5.5.
- npm registry: @hey-api/openapi-ts 0.99.0 (inspected d.ts for `validator.response`, zod compat, plugin names), orval 8.49.0, zod 4.6.7, @tanstack/react-query 5.108.2, @scalar/api-reference 1.82.1.
- [Schemathesis checks reference](https://schemathesis.readthedocs.io/en/stable/reference/checks/), [Python apps guide](https://schemathesis.readthedocs.io/en/stable/guides/python-apps/).
- [Scalar FastAPI integration](https://scalar.com/products/api-references/integrations/fastapi).
- [Azure B-series CPU credit model](https://learn.microsoft.com/azure/virtual-machines/sizes/b-series-cpu-credit-model), [Bsv2 series](https://learn.microsoft.com/en-us/azure/virtual-machines/bsv2-series).

### Secondary (MEDIUM confidence)
- [OpenRouter: What is Jev](https://openrouter.ai/blog/insights/what-is-jev/), [MarkTechPost release coverage](https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/), [Cloudflare AI model page](https://developers.cloudflare.com/ai/models/typesafe/jev/): Jev is available via gateways too (not used).

### Tertiary (LOW confidence, unverified claims)
- Waitlist removal and $5 credit: [cryptobriefing](https://cryptobriefing.com/typesafe-jev-ai-public-access/), [rootdata](https://www.rootdata.com/news/764658), [flaviocopes](https://flaviocopes.com/jev-api-key/), [eesel pricing](https://www.eesel.ai/blog/typesafe-jev-pricing), [jevmodel.org](https://jevmodel.org/) (unofficial). Conflicting with the "early access" homepage wording.
- Orval/hey-api comparisons: [pkgpulse](https://www.pkgpulse.com/guides/orval-vs-openapi-typescript-vs-kubb-openapi-client-2026), [zenn](https://zenn.dev/dk_/articles/738f149be880b1?locale=en).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH for existing pins; MEDIUM for new packages (versions verified, slopcheck not run).
- Architecture / SQL: HIGH for behaviour verified today; MEDIUM for VM latency (spikes S1/S3 pending).
- Jev: HIGH for contract, versions and price (official docs); LOW for access and free credit.
- Pitfalls: HIGH (several reproduced today).

**Research date:** 2026-11-22
**Valid until:** 2026-12-06 for Jev and hey-api (fast-moving); 2026-12-22 for the rest.
