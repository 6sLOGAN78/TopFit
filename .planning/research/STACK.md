# Stack Research

**Domain:** Conversational AI job matching / recommender (LLM agent + hybrid lexical/vector search + feature ranking + personalisation over a live jobs index), Python backend, Next.js frontend
**Researched:** 2026-11-21
**Confidence:** HIGH for library versions (checked live against PyPI, npm, Docker Hub and endoflife.date on 2026-11-21). MEDIUM for hosting and cost (vendor pricing pages, which change often). LOW where marked.

---

## Verdict on SPEC.md §16

The stack in SPEC §16 is right. It matches what the ecosystem uses for this kind of app in late 2026, and no core choice needs replacing. Below are the corrections, from most to least important, each verified against current sources:

| # | SPEC says | Change to | Why | Confidence |
|---|-----------|-----------|-----|------------|
| 1 | Vector CTE `ORDER BY embedding <=> qv LIMIT 100` (§8.1) | `SET LOCAL hnsw.ef_search = 200` (or iterative scan) inside the search transaction, and a non-materialised CTE | pgvector's default `hnsw.ef_search` is **40**, so an HNSW scan returns **at most 40 rows** whatever the `LIMIT` is. Filters cut that further. Also, Postgres materialises a CTE that is referenced twice (`base` is used by both `lex` and `vec`), so the vector leg would sort a materialised set and never use the HNSW index. | HIGH (pgvector README) |
| 2 | `sse-starlette` (implied by "SSE") | **FastAPI native SSE**: `from fastapi.sse import EventSourceResponse, ServerSentEvent` (added in 0.135.0) | Built in. Sends a 15 s keep-alive ping, sets `X-Accel-Buffering: no` and `Cache-Control: no-cache`, and validates events with Pydantic. One dependency fewer. | HIGH (FastAPI docs) |
| 3 | `MemoryOp.structured: dict \| None` (§9.4) | A typed Pydantic model (e.g. `StructuredPref` with optional `exclude_industry`, `avoid_stage`, `dislike_company` fields) | Anthropic structured outputs send a `dict` with empty `properties`, so **Claude can only return `{}`**. As written, the memory penalty path (RANK-06) would never get data. | HIGH (Anthropic structured-outputs docs) |
| 4 | Python 3.12 | **Python 3.14** (3.13 is fine too; never 3.15 yet) | 3.12 is security-only since April 2025. 3.14 is the only version still getting bug fixes, and all heavy dependencies ship cp314 wheels (onnxruntime 1.32, psycopg-binary 3.3.9, fastembed 0.9.1). 3.15 came out on 2026-10-09 and **onnxruntime has no cp315 wheels**. FastAPI now needs ≥ 3.11 and SQLAlchemy 2.1 needs ≥ 3.11, so 3.12 still works. | HIGH |
| 5 | PostgreSQL 16 + `pgvector/pgvector:pg16` | **PostgreSQL 18** with `pgvector/pgvector:0.8.7-pg18-trixie`. Use the same major version locally, in CI (testcontainers) and in prod. | PG16 is supported until 2028, but 18.7 is mature and Neon offers it. It adds async I/O and native `uuidv7()`. PG19 shipped 2026-11-19, which is too new. pgvector **≥ 0.8.3** is required, not just ≥ 0.8.0: 0.8.3 fixed "possible index corruption with HNSW vacuuming", and the `jobs` table churns heavily through expiry and updates. | HIGH |
| 6 | "TypeScript" (unpinned) | **Pin `typescript@~5.9.3`** | npm `latest` is now **TypeScript 7.0.2** (the Go-native compiler). `typescript-eslint` 8.73 requires `<6.1.0`, `openapi-typescript` 7.13 peers on `^5.x`, and Next.js 16 needs `experimental.useTypeScriptCli` to type-check with TS 7. A fresh `npm i typescript` will break lint and client generation. | HIGH (npm peerDependencies) |
| 7 | APScheduler (unpinned) | **`apscheduler>=3.11,<4`** | APScheduler 4 is still alpha (`4.0.0a6`) and its API is completely different. An unpinned install or an AI-suggested snippet may target 4.x. | HIGH |
| 8 | `slowapi` with in-memory store, or a Postgres token bucket (§14.4, OPEN-3) | **Postgres fixed-window counter table** (about 40 lines, `INSERT … ON CONFLICT … RETURNING`) | SPEC runs **2 Uvicorn workers**, so an in-memory store is per-process even on one machine and the limits come out doubled and inconsistent. `limits`/`slowapi` have no Postgres backend (only memory, Redis, Memcached, MongoDB). slowapi is still 0.1.x with about 120 open issues. A Postgres counter fits the "Postgres only" rule. | HIGH |
| 9 | Hosting: Neon/Supabase + Render/Railway (§15.2) | See "Hosting" below. Supabase Free and Render Free/Postgres are **ruled out**. | Supabase Free gives 500 MB of database and 500 MB RAM and pauses after a week idle. Render Free Postgres **expires after 30 days** and Render Free web services sleep after 15 min (about 1 min to wake). Neon Free scales to zero after 5 min and that can't be disabled. | MEDIUM (vendor pricing pages) |
| 10 | Model IDs from env | Defaults: `LLM_MODEL_FAST=claude-haiku-5-5`, `LLM_MODEL_AGENT=claude-sonnet-5-5` | These are the current models. Haiku 5.5 costs $0.10/$0.50 per MTok, so rerank costs about $0.002 per search. Sonnet 5.5 costs $2/$10. **Sonnet 5.5 rejects `thinking: {type: "disabled"}`**; use `thinking: {type: "between_tools"}` plus low effort to meet the 2.5 s first-token target. | HIGH (Anthropic models docs) |
| 11 | fastembed `bge-small-en-v1.5` | Keep it, but know two facts: fastembed serves a **quantised ONNX build** (`Qdrant/bge-small-en-v1.5-onnx-Q`, 67 MB), and `query_embed()` **does not add the BGE query instruction prefix**. Bake the model into the Docker image (`FASTEMBED_CACHE_PATH`) and set `threads=`. | The default cache is in `/tmp`, so every cold container downloads from Hugging Face. The quantised build means embeddings differ slightly from the PyTorch reference. That doesn't matter as long as every embedding comes from the same path. | HIGH (verified in fastembed 0.9.1 source) |
| 12 | `pypdf (or pdfplumber)` | **pypdf 7.x** as primary. Keep **pypdfium2** in reserve if the resume eval shows bad extraction. **Never PyMuPDF.** | pypdf is pure Python and BSD-licensed, so it runs cleanly in the sandboxed subprocess, and it detects encryption and page count. PyMuPDF is AGPL, which is a licensing problem for a public network service. | MEDIUM |

Everything else in §16 is current and correct: uv, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, psycopg 3, pgvector-python, httpx, tenacity, structlog, prometheus-client, python-docx, argon2-cffi, pytest, pytest-asyncio, testcontainers, respx, Next.js App Router, Tailwind, shadcn/ui, openapi-typescript, Vitest and Playwright.

---

## Recommended Stack

### Core Technologies (backend)

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Python | **3.14.x** (3.13 acceptable) | Runtime | The only release still getting bug fixes. All native dependencies have cp314 wheels. Avoid 3.15 (no onnxruntime wheels). | HIGH |
| uv | **0.13.x** (local machine has 0.9.18, so run `uv self update`) | Packaging, lockfile, venv, Python install | The current standard. `uv sync --frozen` in Docker and CI. `.python-version` pins the interpreter. | HIGH |
| FastAPI | **0.165.x** (`fastapi[standard]`) | HTTP API, native SSE, OpenAPI | Native SSE since 0.135. Requires Python ≥ 3.11 (since 0.162.6) and runs on Starlette 1.x. `fastapi-slim` is discontinued. Note 0.137's breaking change: `router.routes` is now a tree, so don't iterate it. | HIGH |
| Uvicorn | **0.54.x** | ASGI server | Default for FastAPI. Run `--workers 2` only if RAM allows; see the memory note under Hosting. | HIGH |
| Pydantic | **2.14.x** | Schemas, validation, LLM structured output models | FastAPI dropped `pydantic.v1`. The Anthropic SDK's `messages.parse(output_format=Model)` takes Pydantic models directly. | HIGH |
| pydantic-settings | **2.16.x** | Typed settings from env | Standard companion for `.env` handling. | HIGH |
| SQLAlchemy | **2.1.x** (`sqlalchemy[asyncio]` if you use async) | ORM / Core | 2.1 is GA (2026-09). Breaking changes that affect this project: **greenlet is no longer installed by default** (add the `[asyncio]` extra, or `AsyncSession` fails at import); the default PG driver is now psycopg 3; **autoflush runs before Core and `text()` statements too**; you can't set `default` and `insert_default` together. | HIGH |
| Alembic | **1.20.x** | Migrations | Works with SQLAlchemy ≥ 2.0. Write the HNSW, GIN and generated-`tsvector` DDL with `op.execute()`, because autogenerate won't render them correctly. | HIGH |
| psycopg | **3.3.x** (`psycopg[binary,pool]`) | Postgres driver (sync + async) | Native async, COPY for bulk upserts, and server-side binding. pgvector-python registers the `vector` type on it. | HIGH |
| PostgreSQL | **18.x** | Single datastore | See the verdict table. Extensions used: `vector`, `pg_trgm`, `citext` (all on Neon PG18, and in the pgvector image). | HIGH |
| pgvector (extension) | **0.8.7** (minimum 0.8.3) | HNSW vector index, iterative scans | 0.8.0 added iterative index scans. 0.8.3 fixed HNSW vacuum index corruption. 0.8.7 is on Neon and in the `pgvector/pgvector` images. 0.9.0 (unreleased) drops PG13/14 and adds async I/O for indexes. | HIGH |
| pgvector (Python) | **0.5.x** | SQLAlchemy `Vector` type, psycopg registration | 0.5.0 changed return types: **SQLAlchemy columns now return `list`** and psycopg returns a `Vector` object. NumPy is no longer a dependency. Convert explicitly to `np.asarray` in MMR and Rocchio code. | HIGH |
| fastembed | **0.9.x** | Local ONNX embeddings (`BAAI/bge-small-en-v1.5`, 384-d) and an optional cross-encoder | No PyTorch, small image, supports Python 3.10–3.14. Pulls in onnxruntime 1.32. | HIGH |
| anthropic (SDK) | **2.12.x** | Claude client behind `ranker/llm/` | v2 SDK. `messages.parse(output_format=PydanticModel)` gives GA structured outputs (no beta header; wire parameter is `output_config.format`). `strict: true` on tool definitions guarantees tool inputs match the schema. Supports streaming and async. | HIGH |

### Core Technologies (frontend)

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Node.js | **24 LTS** (26 LTS is also fine if your host supports it) | Runtime for Next.js | Next 16 needs ≥ 20.9. 24 is LTS through April 2028 and widely supported on Vercel and CI images. | MEDIUM |
| Next.js | **16.4.x** (App Router) | Web app | Current major. Peer React 19. | HIGH |
| React | **19.3.x** | UI | Ships with Next 16. | HIGH |
| TypeScript | **5.9.3, pinned** | Types | See verdict #6: TS 7 breaks typescript-eslint and openapi-typescript today. Pin with `"typescript": "~5.9.3"`. | HIGH |
| Tailwind CSS | **4.3.x** | Styling | v4 uses CSS-first config (`@import "tailwindcss"`, `@theme`). No `tailwind.config.js` by default, so old v3 tutorials don't apply. | HIGH |
| shadcn/ui (CLI `shadcn`) | **4.23.x** | Accessible component primitives | Supports Tailwind v4 and React 19. Components are copied into the repo, so there's no runtime dependency, and the Radix base helps with WCAG AA. | HIGH |

### Supporting Libraries (backend)

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| httpx | 0.28.x | Async HTTP for source adapters and tests | All outbound calls. Share one `AsyncClient` per adapter. | HIGH |
| tenacity | 9.2.x | Retry with exponential backoff and jitter on 429/5xx (ING-07) | Source fetches and LLM calls (honour `retry-after`). | HIGH |
| apscheduler | **3.11.x (`<4`)** | Cron in the `worker` process | Ingest schedules. **Never run it inside the API process**: with 2 Uvicorn workers every job would run twice. | HIGH |
| structlog | 26.1.x | JSON logs with request ID | OPS-05. Add a processor that drops PII keys. | HIGH |
| asgi-correlation-id | 5.0.x | Request-ID middleware | Wire it into structlog contextvars. | MEDIUM |
| prometheus-client | 0.26.x | `/metrics` | OPS-07. **Set `PROMETHEUS_MULTIPROC_DIR` with multiple Uvicorn workers**, or metrics come out per-worker and wrong. | HIGH |
| sentry-sdk | 2.75.x (`sentry-sdk[fastapi]`) | Error tracking | OPS-08. Set `send_default_pii=False` and scrub request bodies, since resumes are PII. | HIGH |
| pypdf | 7.1.x | PDF text extraction, page count, encryption detection | PROF-01, inside the sandboxed subprocess with a 20 s timeout (pypdf has had infinite-loop CVEs on crafted PDFs, which is why the timeout exists). | MEDIUM |
| pypdfium2 | 5.15.x | Fallback PDF text extraction | Only if the resume eval shows pypdf mangling multi-column layouts. Permissive license. | MEDIUM |
| python-docx | 1.2.x | DOCX text | PROF-01. Mature and rarely released, which is fine. | HIGH |
| (none) | — | Magic-byte checks | Check bytes yourself: PDF = `%PDF-`; DOCX = ZIP `PK\x03\x04` that contains `word/document.xml`. `filetype` is unmaintained (last release 2022), and `python-magic` needs libmagic in the image. | MEDIUM |
| argon2-cffi | 25.1.x | argon2id password hashing (AUTH-02) | Use `PasswordHasher()` defaults plus `check_needs_rehash`. (`pwdlib[argon2]`, which the FastAPI tutorial uses, wraps the same thing; either is fine.) | HIGH |
| itsdangerous | 2.2.x | Signing guest-session cookies | Better: store an opaque random session token hashed in a `sessions` table, so logout and account deletion actually revoke it. itsdangerous is only needed if you sign stateless values. | MEDIUM |
| nh3 | 0.3.x | HTML sanitising/stripping for `description_clean` (NORM-01) | Rust-backed, fast. Use `nh3.clean(html, tags=set())` to strip, plus whitespace normalisation. | MEDIUM |
| selectolax | 1.0.x | Fast HTML parsing if you need structure (e.g. HN comment `<p>` splitting) | Optional. BeautifulSoup 4.15 also works but is slower. | MEDIUM |
| rapidfuzz | 3.14.x | Fuzzy company/title matching for dedupe diagnostics and skill alias QA | Optional helper for NORM-06 and ING-10 reports. | MEDIUM |
| numpy | 2.5.x | MMR, Rocchio, metric computation | Already a transitive dependency of fastembed. | HIGH |
| typer | 0.29.x | `ranker` CLI (`ranker ingest remotive`, `ranker embed --missing`) | ING-11, EMB-05. | HIGH |
| orjson | 3.13.x | Fast JSON for logs, `search_logs.results` and the eval snapshot | Optional performance aid. | MEDIUM |

### Supporting Libraries (frontend)

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| openapi-typescript | 7.13.x | Generate TS types from `docs/openapi.json` | Run in CI so drift between API and UI fails the build. | HIGH |
| openapi-fetch | 0.17.x | Type-safe fetch client on the generated types | All JSON endpoints (not SSE). | HIGH |
| eventsource-parser | 4.1.x | Parse SSE from a `fetch()` `ReadableStream` | **Chat is a POST with a body and cookies.** Native `EventSource` supports only GET with no body, so use `fetch` + `eventsource-parser`. (`@microsoft/fetch-event-source` 2.0.1 also works.) | HIGH |
| @tanstack/react-query | 5.108.x | Server-state caching, optimistic save/skip | Feed, saved, profile pages. | MEDIUM |
| zod | 4.6.x | Form validation (profile edit, preferences) | Client-side forms only; the server stays authoritative via Pydantic. | MEDIUM |

### Development Tools

| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| ruff | 0.17.x | Lint and format | `ruff check` and `ruff format --check` in CI. |
| pyright | 1.1.414+ | Type checking (strict on `ranker/`) | Prefer it over mypy 2.x for speed and Pydantic/SQLAlchemy 2.1 typing. **Don't adopt Astral `ty` yet** (0.0.x, Development Status: Beta). |
| pytest | 9.2.x | Tests | |
| pytest-asyncio | 1.5.x | Async tests | In 1.x the `event_loop` fixture is gone. Use `asyncio_mode = "auto"` and loop-scope settings, and ignore pre-1.0 tutorials. |
| testcontainers | 4.15.x (`testcontainers[postgres]`) | Real Postgres + pgvector in integration tests | Use `PostgresContainer("pgvector/pgvector:0.8.7-pg18-trixie", driver="psycopg")`. **The default driver is `psycopg2`**, so pass `driver="psycopg"` or `None`. Use a session-scoped container and per-test transactions with rollback. |
| respx | 0.23.x | Mock httpx | Source adapter tests, no live network (SPEC testing rule). |
| time-machine | 3.6.x | Freeze time | Expiry, recency and Rocchio decay tests. Faster and better maintained than freezegun. |
| hypothesis | 6.170.x | Property tests | Salary parser, dedupe-key normaliser, RRF/MMR invariants. |
| pytest-cov | 7.1.x | Coverage gate (≥ 80% on ranking/search/normalize) | |
| pip-audit | 2.10.x | Dependency CVE scan (SEC-05) | `uv export --format requirements-txt \| pip-audit -r /dev/stdin`. |
| ranx | 0.3.21 | nDCG/MRR/Recall reference implementation | Optional, to cross-check your own metric code. Last release 2025-08, so treat it as a test oracle, not a runtime dependency. Writing nDCG yourself (about 30 lines) is also good signal for the role. |
| k6 | current | Load test (`/jobs/search` p95 at 20 rps) | Thresholds-as-code (`http_req_duration: ['p(95)<300']`) give a clean pass/fail. Locust 2.47 is the Python alternative. |
| Vitest | 5.1.x | Frontend unit tests | |
| Playwright | 1.65.x | E2E (onboarding, chat stream, save/skip) | |
| ESLint | 10.x + typescript-eslint 8.73 | Frontend lint | typescript-eslint caps TS at <6.1, which is the other reason to pin TS 5.9. |

---

## LLM layer specifics (Anthropic, verified 2026-11-21)

| Purpose (SPEC §11.5) | Model | Settings | Est. cost |
|---|---|---|---|
| Agent turns | `claude-sonnet-5-5` ($2/$10 per MTok) | `thinking: {type: "between_tools"}` (lowest setting Sonnet 5.5 accepts; `disabled` returns 400), effort `low` or `medium`, **prompt caching on system prompt + tool definitions** (cache reads cost 5% of base on Sonnet 5.5), `strict: true` tools | About $0.03–0.07 per turn uncached and much less cached. The $20/month cap covers several hundred turns. |
| Rerank, query parse, memory extraction, HN extraction | `claude-haiku-5-5` ($0.10/$0.50 per MTok up to 100k-token prompts) | `messages.parse(output_format=...)`, effort `low`. Thinking can be `disabled` at effort ≤ high, but the docs warn that with thinking off and JSON output requested, Haiku can skip a needed tool call. That doesn't matter for these no-tool calls. | Rerank of 20 jobs ≈ 9k in / 1.6k out ≈ **$0.002**. |
| HN backfill | `claude-haiku-5-5` via the **Message Batches API** | 50% discount, latency not important | negligible |
| LLM judge (evals) | `claude-sonnet-5-5` | Keep the judge model and version fixed and record them in `eval_runs.config` | eval-time only |

Gotchas the `ranker/llm/` abstraction must handle (HIGH, from the Anthropic docs):
- **Don't send `temperature`/`top_p`/`top_k`** to Haiku 5.5 (the docs say to omit them). The abstraction must not inject defaults.
- **Thinking and progress-update blocks must be passed back unchanged** in the tool loop. A "provider-agnostic" message format that flattens content blocks to strings breaks multi-round tool use. Store raw provider content blocks for the current turn.
- **Structured-output schema limits**: no `minimum`/`maximum`, `minLength`/`maxLength`, or `maxItems`, and `minItems` only 0 or 1. SPEC's `Field(max_length=3)` on lists and `Field(ge=0, le=50)` still validate client-side, because `parse()` moves the constraints into descriptions and re-validates. A violation then raises a validation error, so the PROF-02 "retry once with the error" path will actually run. No `oneOf`, no recursive schemas, and `dict` fields only produce `{}` (verdict #3).
- Haiku 5.5 uses the newer tokenizer (about 30% more tokens than Haiku 4.5 for the same text). Base budget maths on measured `usage`, not character counts.
- Model IDs are dateless pinned snapshots from the 4.6 generation on. Keep them in env (as SPEC says) and log `model` per call in `llm_calls`.

**Don't add LangChain or LlamaIndex.** The agent loop (tools, ≤ 6 rounds, parallel tool execution, grounding repair) is about 200 lines on the raw SDK. Writing it yourself is the skill Elip screens for. `pydantic-ai-slim` (2.71) is the only framework worth considering if a second provider becomes a real requirement, but it hides exactly the tool-use mechanics this portfolio should show.

---

## pgvector / search specifics (HIGH, pgvector README + changelog)

1. **ef_search caps results.** Inside the search transaction, run `SET LOCAL hnsw.ef_search = 200` (≥ the vector leg's `LIMIT`). Use `SET LOCAL`, not `SET`: it is transaction-scoped and safe behind PgBouncer transaction pooling.
2. **Filtered HNSW**: `SET LOCAL hnsw.iterative_scan = relaxed_order` plus `hnsw.max_scan_tuples` (default 20000). With `relaxed_order`, wrap the result in a `MATERIALIZED` CTE and re-sort by distance, as the README shows.
3. **CTE materialisation**: give the vector leg its own `WHERE` with the filters and `ORDER BY embedding <=> :qv LIMIT k` directly on `jobs` (or mark `base` as `NOT MATERIALIZED`), so the planner can match the partial HNSW index. The partial-index predicate `status='active' AND canonical_job_id IS NULL` must appear literally in the query.
4. **Exact search is a legitimate option at this scale.** At 10k–50k active rows × 384 dims, a filtered exact scan (btree/partial-index prefilter, then `<=>` sort) is probably tens of milliseconds, with perfect recall (LOW: not benchmarked; measure in Phase 3). Record the HNSW vs exact numbers in `docs/decisions/` as SPEC requires.
5. **`ts_rank_cd` is not BM25.** That's fine for v1. Note that **ParadeDB `pg_search` is not available on Neon** (Neon lists it as unavailable), so SRCH-09 (v2) needs self-hosted Postgres (ParadeDB image) or another host.
6. Optionally store `halfvec(384)` to halve index size. Not needed at 10k rows; v2 at most.

---

## Hosting (MEDIUM; vendor pricing as of 2026-11-21)

Hard constraints: ≤ $30/month total including LLM; always-on API for p95 < 300 ms; RAM for fastembed (onnxruntime + model ≈ 200–300 MB **per process**, so API with 2 workers plus the worker process ≈ 0.8–1 GB); India-based users; pgvector ≥ 0.8.3.

**Recommended: one small VPS for api + worker + Postgres, with web on Vercel.**
- Hetzner Cloud, **Singapore** region (no India region; Singapore is about 60–90 ms from India), shared "Regular Performance" from **€5.49/month**. Pick a plan with ≥ 4 GB RAM. (LOW–MEDIUM: check exact plan specs in the Hetzner console.)
- Docker Compose with `pgvector/pgvector:0.8.7-pg18-trixie`, `api`, `worker` and **Caddy** for automatic HTTPS.
- Backups: nightly `pg_dump -Fc` to Cloudflare R2 or Backblaze B2 (free tiers), plus a restore drill for OPS-09.
- Why: no scale-to-zero cold starts, full control of the pgvector version, `pg_search` possible for v2, a real Docker deployment (one of Elip's good-to-haves), and about ₹600/month, which leaves almost the whole budget for LLM spend.
- Cost: you own patching, backups and disk alerts. Add an uptime monitor (UptimeRobot or Better Stack free tier).

**Managed alternative (less ops, tighter budget):** Neon (region `aws-ap-southeast-1` Singapore; PG18 with pgvector 0.8.7) + Railway Hobby (api + worker containers) + Vercel.
- Neon Free: **1 GB storage per project**, **100 CU-hours/month**, autoscale to 2 CU, and **scale-to-zero after 5 min that can't be disabled**. Keeping 0.25 CU always on takes about 182 CU-hours/month, which exceeds Free. So Free means cold starts after idle periods, which hurts p95 for real users. Neon Launch is about $0.106/CU-hour, roughly $19/month to stay always-on at 0.25 CU.
- Railway Hobby: $5/month including $5 of usage, with RAM at about $10/GB-month, so about 1 GB across api + worker comes to roughly $10–15/month.
- Total about $15 (Neon Free, with cold starts) to about $35 (Neon Launch, always-on), which is at or over budget once LLM spend is added.
- If you go managed, use Neon's **direct (unpooled)** connection string from the long-running API with SQLAlchemy/psycopg's own pool. Only use the pooled (PgBouncer) endpoint together with `prepare_threshold=None` on psycopg (MEDIUM: psycopg auto-prepares after 5 executions, which conflicts with transaction pooling unless the pooler supports protocol-level prepared statements).

**Ruled out:** Supabase Free (500 MB DB, 500 MB RAM, pauses after 1 week idle, no backups), Render Free (web sleeps after 15 min with about 1 min wake; **free Postgres deleted after 30 + 14 days**), Fly.io (no free allowance; fine but no advantage over a VPS here).

**Web:** Vercel Hobby (non-commercial use, which fits a portfolio project).

**Domain is required, not optional (HIGH).** `*.vercel.app`, `*.onrender.com`, `*.up.railway.app` and similar are on the Public Suffix List, so `app.vercel.app` → `api.onrender.com` counts as **cross-site**. A `SameSite=Lax` session cookie won't be sent on `fetch` from the web app, and guest sessions, CSRF and auth all break. Buy a cheap domain (about ₹800–1,000/year) and serve `ranker.<tld>` (Vercel) plus `api.ranker.<tld>` (VPS). Those are same-site, so Lax cookies work with `credentials: "include"` and a strict CORS allow-list. This settles SPEC's OPEN "domain" question.

---

## Installation

```bash
# Backend (backend/pyproject.toml managed by uv; Python 3.14)
uv python pin 3.14
uv add "fastapi[standard]>=0.165" "uvicorn>=0.54" "pydantic>=2.14" "pydantic-settings>=2.16" \
       "sqlalchemy[asyncio]>=2.1" "alembic>=1.20" "psycopg[binary,pool]>=3.3" "pgvector>=0.5" \
       "fastembed>=0.9,<0.10" "anthropic>=2.12" "httpx>=0.28" "tenacity>=9.2" \
       "apscheduler>=3.11,<4" "structlog>=26.1" "asgi-correlation-id>=5" "prometheus-client>=0.26" \
       "sentry-sdk[fastapi]>=2.75" "pypdf>=7.1" "python-docx>=1.2" "argon2-cffi>=25.1" \
       "nh3>=0.3" "numpy>=2.3" "typer>=0.29" "orjson>=3.13"

uv add --dev "pytest>=9.2" "pytest-asyncio>=1.5" "pytest-cov>=7.1" "testcontainers[postgres]>=4.15" \
             "respx>=0.23" "time-machine>=3.6" "hypothesis>=6.170" "ruff>=0.17" "pyright>=1.1.414" \
             "pip-audit>=2.10"

# Bake the embedding model into the image (Dockerfile)
# ENV FASTEMBED_CACHE_PATH=/opt/models
# RUN uv run python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# Frontend (web/, Node 24 LTS)
npx create-next-app@16 web --ts --tailwind --app --eslint
cd web
npm i -E typescript@5.9.3          # pin: TS 7 breaks typescript-eslint + openapi-typescript
npx shadcn@latest init
npm i openapi-fetch @tanstack/react-query zod eventsource-parser
npm i -D openapi-typescript vitest @playwright/test
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastAPI native SSE | sse-starlette 3.7 | Only if you need features the native one lacks. Native SSE covers SPEC §11.4 fully. |
| Postgres counter table for rate limits | slowapi + Redis | Only if you add Redis for other reasons (SPEC forbids it in v1). |
| APScheduler 3.11 in a separate worker | **procrastinate** 3.12 (Postgres-backed task queue with periodic tasks, psycopg 3, async) | If async jobs (memory extraction, debounced Rocchio recompute, re-embed) must survive restarts. Fits "Postgres only" well, so it's the best v1.x upgrade path. For the MVP, FastAPI `BackgroundTasks` / `asyncio` tasks for memory extraction are acceptable because a lost extraction is harmless. |
| APScheduler worker | Platform cron calling `ranker ingest <source>` | On Railway or Render, where cron is a first-class feature and you don't want a long-running worker. On a VPS, use the worker (or systemd timers). |
| fastembed bge-small-en-v1.5 | `snowflake/snowflake-arctic-embed-s` or `-xs` (384-d, also in fastembed) | Ablation candidate. Same dimension, so no schema change. Swap only if the eval shows a gain. |
| LLM rerank (Haiku 5.5) | fastembed `TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2")` (80 MB) or `jinaai/jina-reranker-v1-tiny-en` (130 MB) | OPEN-5 / degraded-mode fallback: a free local rerank of the top 20–50 when the LLM is unavailable or over budget. Also a good ablation row. CPU latency on a shared vCPU is unmeasured (LOW), so benchmark it before putting it on the hot path. |
| pypdf | pypdfium2 | If the resume-eval field F1 is hurt by extraction quality (multi-column CVs). |
| Hetzner VPS | Neon + Railway | If you'd rather pay more and skip backups/patching. Accept cold starts on Neon Free. |
| Hetzner VPS | Oracle Cloud Always Free (ARM, Mumbai region) | Free and in India, but sign-up and capacity are unreliable (LOW). Don't plan the MVP around it. |
| pyright | mypy 2.4 | If you prefer mypy's plugin ecosystem. SQLAlchemy 2.1 typing no longer needs the plugin. |
| own agent loop on `anthropic` | pydantic-ai-slim 2.71 | Multi-provider agents. Not needed, and it hides the skill you want to show. |
| k6 | Locust 2.47 | If you want load scripts in Python. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `typescript@latest` (7.x) | Breaks typescript-eslint (<6.1) and openapi-typescript (^5) today; Next 16 needs an experimental flag. | `typescript@~5.9.3` |
| APScheduler 4.0 alphas | Pre-release, completely different API. | `apscheduler>=3.11,<4` |
| Scheduler inside the API process | Runs N times with N Uvicorn workers and dies with deploys. | Separate `worker` container |
| In-memory rate limiting (slowapi default) | Per-process: wrong with 2 workers, reset on deploy. | Postgres counter table |
| Python 3.15 | No onnxruntime wheels yet; fastembed won't install. | Python 3.14 |
| pgvector < 0.8.3 | HNSW-vacuum index corruption fixed in 0.8.3; iterative scans need ≥ 0.8.0. | pgvector 0.8.7 |
| Relying on the default `hnsw.ef_search` | Silently returns ≤ 40 neighbours. | `SET LOCAL hnsw.ef_search = 200` |
| `dict` fields in LLM output schemas | Claude can only return `{}`. | Nested typed Pydantic models |
| `temperature` on Claude 5.x calls | Haiku 5.5 docs say to omit it. | Rely on caching (`rerank_cache`) for reproducibility |
| LangChain / LlamaIndex | Heavy abstraction, version churn, hides tool-use mechanics. | Thin `ranker/llm/` over the `anthropic` SDK |
| A separate vector DB (Qdrant, Pinecone, Chroma) | Violates the single-datastore decision and adds ops; pgvector is plenty at 10k–50k rows. | pgvector |
| PyTorch / sentence-transformers | About 2 GB of images and slow cold starts on small hosts. | fastembed (ONNX) |
| PyMuPDF (fitz) | AGPL license, a problem for a public network service. | pypdf / pypdfium2 |
| `filetype`, `python-magic` | Unmaintained / needs libmagic. | Manual magic-byte checks (PDF, ZIP+`word/document.xml`) |
| Native browser `EventSource` for chat | GET only, no body, no custom headers. | `fetch` + `eventsource-parser` |
| Supabase Free / Render Free Postgres | 500 MB and pauses / **deleted after 30 days**. | VPS Postgres or Neon |
| Platform subdomains for web + API (`*.vercel.app` + `*.onrender.com`) | Cross-site, so session cookies are blocked. | Own domain with `api.` subdomain |
| `pytest-asyncio` pre-1.0 patterns (`event_loop` fixture) | Removed in 1.x. | `asyncio_mode="auto"`, loop-scope config |
| Astral `ty` as the CI type checker | Still 0.0.x beta. | pyright |

---

## Stack Patterns by Variant

**If you deploy on a single VPS (recommended):**
- Run Compose services `db`, `api`, `worker`, `caddy`, with the web app on Vercel.
- Run 1 Uvicorn worker first. Search is async and embedding runs in a threadpool (`anyio.to_thread.run_sync`), so one worker probably meets 20 rps at 10k jobs (LOW: verify with k6). Fewer workers means less RAM, no Prometheus multiprocess mode, and simpler rate limiting.
- Set `TextEmbedding(threads=2)` so onnxruntime doesn't grab every core.

**If you deploy managed (Neon + Railway):**
- Use the Neon direct connection string and keep `pool_size` small (5).
- Run an uptime ping on `/readyz` only if you're on Neon Launch; on Free it burns CU-hours.
- Use Railway cron for ingest instead of a long-running worker, to save RAM spend.

**If the LLM budget is exhausted or the provider is down:**
- Use rule-based query parsing (SRCH-08 fallback) plus feature ranking, with the optional fastembed cross-encoder as the reranker. `degraded: true`.

**If filtered HNSW recall measures poorly in Phase 3:**
- Use exact search with btree prefilters (likely fast enough at this scale), or iterative scan `relaxed_order` + `max_scan_tuples`, or over-fetch 300 and filter. Pick by measured nDCG/Recall@50 and latency.

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| fastapi 0.165 | Python ≥ 3.11, Starlette 1.x, Pydantic 2.x only | Python 3.10 dropped in 0.162.6 (2026-11-13). Native SSE since 0.135. |
| sqlalchemy 2.1 | Python ≥ 3.11, psycopg 3 (default PG driver), alembic 1.20 | `[asyncio]` extra needed for greenlet. |
| pgvector-python 0.5 | SQLAlchemy ≥ 2, Python ≥ 3.10 | `Vector` columns return `list` (changed in 0.5.0). |
| fastembed 0.9.1 | Python 3.10–3.14; onnxruntime ≥ 1.24.2 on 3.14; numpy ≥ 2.3 on 3.14 | No cp315 onnxruntime wheels yet. |
| onnxruntime 1.32 | cp311–cp314 | Determines your Python ceiling. |
| psycopg 3.3.9 | cp310–cp315 binary wheels | |
| pgvector ext 0.8.7 | PG 13–19 (0.9.0 will drop 13/14) | On Neon PG16–19, and in `pgvector/pgvector:0.8.7-pg18-trixie`. |
| testcontainers 4.15 `PostgresContainer` | Any image; default driver `psycopg2` | Pass `driver="psycopg"`. |
| next 16.4 | React 19, Node ≥ 20.9 | TS 7 only with `experimental.useTypeScriptCli`. |
| typescript-eslint 8.73 | `typescript >=4.8.4 <6.1.0`, ESLint 8.57–10 | Forces TS ≤ 6.0; pin 5.9.3 for openapi-typescript. |
| openapi-typescript 7.13 | `typescript ^5.x` | |
| tailwindcss 4.3 + shadcn 4.23 | React 19 / Next 16 | CSS-first config; no v3 `tailwind.config.js`. |
| postgres:18 Docker images | — | MEDIUM: the official PG18 images moved the default data dir and volume to `/var/lib/postgresql` (versioned subdir). Mount `/var/lib/postgresql`, not `/var/lib/postgresql/data`, or data may not persist as expected. Check the image docs when writing compose. |

---

## Sources

- PyPI JSON API (live, 2026-11-21): versions, upload dates and `requires_python` for fastapi, pydantic, pydantic-settings, sqlalchemy, alembic, psycopg, pgvector, fastembed, anthropic, httpx, tenacity, apscheduler, structlog, prometheus-client, pypdf, pdfplumber, pymupdf, pypdfium2, python-docx, argon2-cffi, pytest, pytest-asyncio, testcontainers, respx, slowapi, sse-starlette, uvicorn, ruff, pyright, mypy, sentry-sdk, limits, starlette, procrastinate, onnxruntime, locust, pip-audit, ty, ranx, pydantic-ai-slim, nh3, time-machine and others. HIGH.
- npm registry (live): next 16.4.6, react 19.3.1, typescript 7.0.2 (dist-tags; 5.9.3 and 6.0.3 available), tailwindcss 4.3.5, shadcn 4.23.0, openapi-typescript 7.13.0 (peer `typescript ^5.x`), typescript-eslint 8.73.0 (peer `<6.1.0`), vitest 5.1.1, @playwright/test 1.65.1, eventsource-parser 4.1.3. HIGH.
- https://raw.githubusercontent.com/pgvector/pgvector/master/CHANGELOG.md: 0.8.0 iterative scans, 0.8.3 HNSW vacuum corruption fix, 0.8.7 latest (2026-10-01), 0.9.0 unreleased. HIGH.
- https://github.com/pgvector/pgvector README: `hnsw.ef_search` default 40 limits results; iterative_scan, max_scan_tuples, `SET LOCAL`, partial indexing, halfvec. HIGH.
- Docker Hub `pgvector/pgvector` tags: `0.8.7-pg16…pg19`, `-trixie`/`-bookworm` variants. HIGH.
- https://raw.githubusercontent.com/pgvector/pgvector-python/master/CHANGELOG.md: 0.5.0 return-type changes. HIGH.
- https://fastapi.tiangolo.com/tutorial/server-sent-events/ and the FastAPI release notes (GitHub `docs/en/docs/release-notes.md`): native SSE in 0.135.0, Python 3.10 dropped in 0.162.6, 0.137.0 router tree change, `pydantic.v1` dropped, `fastapi-slim` discontinued. HIGH.
- https://docs.sqlalchemy.org/en/21/changelog/migration_21.html: greenlet extra, Python 3.11 minimum, psycopg default, autoflush change. HIGH.
- fastembed 0.9.1, inspected locally (`list_supported_models()` and source): bge-small-en-v1.5 = `Qdrant/bge-small-en-v1.5-onnx-Q`, 0.067 GB; no query prefix in `query_embed`; `FASTEMBED_CACHE_PATH` with `/tmp` default; `threads`, `local_files_only`; cross-encoders (ms-marco MiniLM L-6/L-12, jina tiny/turbo, bge-reranker-base, bge-reranker-v2-m3); sparse models (BM25, SPLADE++, miniCOIL). HIGH.
- https://platform.claude.com/docs/en/about-claude/models/overview: current models, IDs and prices. HIGH.
- https://platform.claude.com/docs/en/models/haiku-5-5/overview: tiered pricing, omit temperature, tokenizer note. HIGH.
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs: GA, `output_config.format`, `messages.parse`, schema limitations, `dict` returns `{}`. HIGH.
- https://platform.claude.com/docs/en/build-with-claude/thinking: Sonnet 5.5 rejects `disabled` (use `between_tools`); Haiku 5.5 disabled at effort ≤ high; pass thinking blocks back unchanged. HIGH.
- https://endoflife.date/api/python.json and /postgresql.json: 3.15 released 2026-10-09; 3.12 security-only; PG19 released 2026-11-19; PG16 EOL 2028-11. HIGH.
- https://neon.com/docs/extensions/pg-extensions: pgvector 0.8.7 on PG16–19; pg_search unavailable; citext available. HIGH.
- https://neon.com/pricing: Free 1 GB/project, 100 CU-h, scale-to-zero 5 min (can't disable); Launch $0.106/CU-h. MEDIUM (pricing changes).
- https://neon.com/docs/introduction/regions: Singapore available, no India. HIGH.
- https://supabase.com/pricing: Free 500 MB DB / 500 MB RAM, pauses after 1 week, no backups; Pro $25. MEDIUM.
- https://render.com/docs/free: free web sleeps after 15 min (about 1 min wake); free Postgres expires after 30 days. MEDIUM.
- https://railway.com/pricing: Hobby $5 including $5 usage; about $10/GB-RAM-month. MEDIUM.
- https://www.hetzner.com/cloud: Singapore location, Regular Performance from €5.49/month. LOW–MEDIUM (plan specs not shown on the page).
- https://testcontainers-python.readthedocs.io/en/latest/modules/postgres/README.html: `PostgresContainer(image, …, driver='psycopg2')`. MEDIUM.
- https://nextjs.org/blog/next-16-3 and https://nextjs.org/docs/app/api-reference/config/next-config-js/useTypeScriptCli (via search results): TS 7 support behind `experimental.useTypeScriptCli`. MEDIUM.
- nodejs.org/dist/index.json: Node 24 "Krypton" LTS, 26 "Lithium" LTS. HIGH.

---
*Stack research for: conversational AI job-matching / recommender (Ranker)*
*Researched: 2026-11-21*
