# Phase 2: Live Jobs Index - Research

**Researched:** 2026-11-21
**Domain:** Job-source ingestion (Remotive, Adzuna, Greenhouse/Lever), normalisation, local embeddings, a Postgres task queue, an LLM gateway
**Confidence:** MEDIUM-HIGH. Source APIs and PG18 behaviour were verified live. The Adzuna quota and India volume can only be measured with keys (the ING-00 human checkpoint).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Job target and sources**
- **D-01: Conditional Greenhouse/Lever.** ING-00 runs first and measures real volume, quota and terms per source. If Remotive + Adzuna can't reach the target, **ING-13 (Greenhouse/Lever public boards) is pulled into this phase** as a third adapter. ADR `docs/decisions/0001-sources.md` records the measurement and the decision.
- **D-02: Target is ≥ 10k active tech jobs, with an honest floor.** If all sources together can't reach 10k, ADR 0001 records the real achievable number and the reason. Never pad the index with off-scope jobs to hit the number (see D-05/D-06).
- **D-03: Greenhouse/Lever boards are auto-discovered, with guardrails:**
  - Start from a seed list of about 30 known boards in config (Indian startups and remote-first product companies).
  - Add a discovered board slug (for example from job URLs seen in other sources) only if it has at least N in-scope tech jobs located in India or remote-open-to-India. N is a config value; Claude picks the default.
  - Log every board that was added or rejected, and make the list visible in a CLI report and in `ingest_runs` errors/notes.
  - Re-check discovered boards periodically and drop boards that go empty.
- **D-04: Adzuna credentials don't exist yet.** Phase 2 starts with a **human checkpoint**: sign up at developer.adzuna.com and put `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` into local `.env` and `/srv/ranker/.env` on the server. The ING-00 spike needs these keys. Code and tests must not need real keys (respx mocks).

**Which jobs count**
- **D-05: Tech scope = engineering + data.** That means software/backend/frontend/full-stack, mobile, DevOps/SRE/platform, data engineering, data science/ML/AI, QA/SDET and security. It **excludes** product management, design, tech support and IT admin. Classify with rules on title and category; the planner/researcher picks the mechanism. Out-of-scope jobs are not stored (or are stored inactive and don't count toward the target; Claude's choice, documented).
- **D-06: Geography = India + remote.** That covers on-site/hybrid jobs in Indian cities plus remote jobs from anywhere. Every job gets `remote_eligibility` (worldwide / India / APAC / US-only / EU-only / other-region / unknown). Don't drop US/EU-only remote jobs at ingest; ranking filters them for India users later (NORM-03 default).
- **D-07: `remote_eligibility = unknown` is shown to India users, flagged.** It ranks below jobs confirmed as India, APAC or worldwide, and the UI later shows a "region not stated" badge. Phase 2 only stores the value correctly. Ranking and UI consume it in later phases, so capture this as a decision for them.
- **D-08: Region detection = rules first, LLM fallback.** Use source fields (e.g. Remotive `candidate_required_location`) and regex phrases ("US only", "must reside in", "EU timezone", "IST overlap", "anywhere"). Call the LLM (Haiku via the gateway, typed output) **only when the rules return unknown**, and cache by `content_hash`. The NORM-05 seniority/employment-type fallback uses the same pattern.

**LLM gateway, key and budget**
- **D-09: There's no Anthropic API key yet.** Build and test the whole gateway against the **fake provider**. Getting a key (prepaid credit at console.anthropic.com, set as `ANTHROPIC_API_KEY` in `.env` and `/srv/ranker/.env`) is a **human checkpoint before the production backfill**. Without a key the system still runs fully: fallbacks are skipped and fields stay `unknown`.
- **D-10: The monthly LLM spend cap starts at $5.** It is env-configurable (e.g. `LLM_MONTHLY_BUDGET_USD=5`) and enforced in the gateway from `llm_calls` cost totals. Over budget, calls are skipped (not errored) and logged.
- **D-11: Degraded mode = save now, enrich later.** If the budget is exhausted or the API fails, the job is saved with the field set to `unknown`, and an enrichment task goes into the `tasks` table. The worker retries it when budget or the API is back (with backoff and a max-attempts limit). **Ingestion never blocks on the LLM.** This is the first real consumer of FND-07.

**Salary and currency**
- **D-12: FX = fixed rates in config.** `config/fx.yaml` holds rates per currency to INR and an `as_of` date, updated by hand. Conversion is deterministic and testable, with no outside FX dependency. Store the rate date used, or make it derivable, so the UI can later say "converted at ₹X/$ as of <date>".
- **D-13: Adzuna predicted salaries are stored and shown as estimates.** They carry the flag `salary_is_estimate = true`. Later phases show them as "~₹X LPA (estimate)" and use them in salary filters only if the user allows estimates. Phase 2 stores the flag correctly.

### Claude's Discretion
- Exact schedule times (Remotive ≤ 4×/day per ING-01; Adzuna daily within the measured quota, with times in IST). Stagger sources so they don't overlap.
- How the admin ingest endpoint (ING-11) is protected before accounts exist (Phase 5). Expected: a static admin token from env via a header, constant-time compare, and disabled when unset.
- How the ≥ 400-skill taxonomy (`config/skills.yaml`) is built. Use permissively licensed or hand-curated sources, and record provenance. Handle C++, C#, Go, .NET and Node.js correctly (NORM-06).
- The default N for discovered-board relevance, the seed-board list, and the board re-check cadence.
- Tables and migration structure, following SPEC §8 (`jobs`, `companies`, `ingest_runs`, `llm_calls`, `tasks`, …), including the HNSW index DDL via `op.execute()`.
- Whether out-of-scope jobs are skipped or stored inactive (D-05).
- Expiry thresholds within ING-08 bounds (Adzuna 30–45 days), and how "normal volume" is computed for the 50% no-expiry guard.
- Dedupe: the simple rule is enough in this phase (same `dedupe_key` → keep newest), per SPEC's MVP note. Full ING-10 comes later.

### Deferred Ideas (OUT OF SCOPE)
- A live FX rate API: revisit only if fixed rates prove too stale.
- UI badge "region not stated" and "estimate" salary display belong to the search/feed UI phases (decisions D-07 and D-13 tell those phases what to do).
- HN "Who is hiring" ingestion (ING-03) and full cross-source dedupe (ING-10) stay in their roadmap phases.
- Product rename beyond display name (package, images, DB names stays `ranker`). Not planned.
</user_constraints>

<phase_requirements>
## Phase Requirements

> **ID warning:** REQUIREMENTS.md and SPEC.md §4.2 number the NORM requirements differently. In SPEC, NORM-03 is salary, NORM-05 is skills and NORM-07 is LLM skill extraction (v2). In REQUIREMENTS.md, NORM-03 is `remote_eligibility`, NORM-04 is salary, NORM-05 is seniority, NORM-06 is skills and NORM-07 is companies. **Plans must use the REQUIREMENTS.md IDs below** and read SPEC only for detail.

| ID | Description (REQUIREMENTS.md) | Research Support |
|----|-------------|------------------|
| ING-00 | Measure per-source volume, quota and terms before writing ingestion code; choose the source mix | §Source Facts (live probes); §ING-00 Spike Design; Adzuna `count` field makes the spike cheap |
| ING-01 | Remotive ≤ 4×/day, honour terms | Live legal notice quoted; schedule 2×/day; attribution fields |
| ING-02 | Adzuna India within the measured quota | Official swagger params; category + `sort_by=date` + `max_days_old` crawl plan; quota budget config |
| ING-04 | One `SourceAdapter` plugin interface | §Pattern 2 (adapter registry, no `source` CHECK constraint) |
| ING-05 | Idempotent upsert on `(source, source_job_id)` with `content_hash` | §Pattern 3. PG18 `RETURNING old/new` upsert verified live |
| ING-06 | `ingest_runs` counts and errors | §Pattern 4 (run lifecycle) |
| ING-07 | Retry on 429/5xx with backoff and jitter, max 5 tries | tenacity `wait_random_exponential` + `Retry-After` |
| ING-08 | Per-source expiry policy with a 50% volume guard | §Pattern 5 (absence by scope / TTL; median-of-5 guard) |
| ING-09 | Re-embed and re-skill only on `content_hash` change | Upsert nulls `embedding` on hash change; embed pass selects `embedding IS NULL` |
| ING-11 | `ranker ingest <source>` CLI plus admin endpoint | typer CLI; admin endpoint enqueues a `tasks` row (never runs ingestion in the API) |
| ING-12 | Production holds the ING-00 target | Volume math in §Source Facts; depends on Adzuna `count` |
| ING-13 (conditional) | Greenhouse/Lever public boards | Endpoints, shapes and quirks verified live; discovery design |
| NORM-01 | Clean text description; raw kept | nh3 (`html.unescape` first for Greenhouse) |
| NORM-02 | City/country/remote type, Indian aliases | `cities.yaml` alias table; Adzuna `location.area` |
| NORM-03 | `remote_eligibility` | §Region Rules table |
| NORM-04 | Salary parse to INR annual; Adzuna estimates flagged | §Salary Parsing; `fx.yaml` with `as_of` |
| NORM-05 | Seniority and employment type: rules, then LLM fallback | Source hints table; gateway + `tasks` enrichment |
| NORM-06 | ≥ 400-skill taxonomy; C++/C#/Go/.NET/Node.js | §Skills Taxonomy (custom-boundary regex; licensed seeds) |
| NORM-07 | Companies table | §Company normalisation |
| SRCH-01 | Local bge-small embeddings, model name per row, backfill CLI | Existing `Embedder`; `ranker embed --missing/--all` |
| FND-06 | LLM gateway | §Pattern 6. `messages.parse` + `output_config.effort` verified; prices verified |
| FND-07 | `tasks` queue with `SKIP LOCKED` | §Pattern 7. Claim query verified live on PG18 |
| OPS-02 | Scheduling in exactly one worker process | APScheduler 3.11 in the worker + `pg_try_advisory_lock` leader guard |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- Python 3.14 + uv; FastAPI; Pydantic v2; SQLAlchemy 2.1 `[asyncio]`; psycopg 3; PG18 + pgvector 0.8.7; fastembed bge-small (no query prefix, model baked into the image at `/opt/models`).
- `apscheduler>=3.11,<4`, **only in the worker**, never in the API process.
- All LLM calls go through `ranker/llm/`; model IDs come from env (`LLM_MODEL_FAST=claude-haiku-5-5`). Use **no `dict` fields** in LLM output schemas. **Don't send `temperature`/`top_p`/`top_k`** to Haiku 5.5. Pass thinking blocks back unchanged in tool loops (not needed this phase, but the gateway's message type must not flatten blocks).
- pgvector-python 0.5 returns `list` from SQLAlchemy `Vector` columns.
- Alembic: write HNSW, GIN and generated-`tsvector` DDL with `op.execute()`.
- Use Postgres only: no Redis, no separate vector DB, no external queue.
- Tests: no live network or real LLM calls in the default run (use respx); LLM tests go behind `@pytest.mark.llm`. Pytest runs with `filterwarnings=error`. ruff + pyright strict on `ranker/`. Every new setting must appear in `.env.example` (`tests/test_env_example.py`).
- Privacy: no PII in logs or `llm_calls` (store no prompt or response text).
- Data access: official APIs/public data only, with source attribution and the original URL stored for every job.
- Budget: LLM cap of $5/month this phase (D-10).
- GSD workflow: edits go through GSD commands. Commit attribution follows the session reminder.

## Summary

**Remotive will not supply volume.** A live call today returned **16 jobs in total**, with about 5 in the tech scope. The response's embedded legal notice requires a link back to Remotive and a credit, forbids resubmitting jobs to third-party job sites, forbids putting the jobs behind a signup wall, and advises at most 4 fetches a day. **Adzuna is the only realistic volume source.** Its official swagger spec (fetched today from `api.adzuna.com/v1/api-docs/adzuna`) confirms `results_per_page`, `max_days_old`, `sort_by ∈ {date, salary, relevance}`, `sort_direction ∈ {up, down}`, `category`, `what_or`, `title_only`, `location0..7` and `company`. Each response returns `count` (total matches), and that makes the ING-00 spike cheap: one call per query shape measures the whole addressable volume. Descriptions are **truncated to 500 characters**, and `redirect_url` **must** be used to link out. The quota is **not published**. Third-party claims conflict (250/day versus 1,000/month), so the dashboard reading at the D-04 checkpoint is the only authority. Adzuna's ToS describes the free tier as "intended for evaluating the API". Permitted use includes "publishing Adzuna ad listings" with a hyperlinked "Adzuna" label on each advert, and on termination all Adzuna data must be removed. The ADR must record this, and the system needs a one-command purge per source.

**Greenhouse and Lever are public, unauthenticated, full-feed JSON with full descriptions.** Live probes worked: Groww on Greenhouse returned 9 jobs; CRED, Meesho and Paytm on Lever returned 11, 48 and 199. Both endpoints return every posting in one call with no pagination. Two quirks: Greenhouse `content` is **HTML-entity-escaped HTML** (run `html.unescape` before sanitising), and Lever `createdAt` is epoch milliseconds. Many board jobs are non-tech (Paytm: lending, sales, collections) or outside India (Luxembourg), so the D-05/D-06 scope filter decides real yield. Realistic Greenhouse/Lever yield from about 30 Indian boards is hundreds to low thousands, not 10k [ASSUMED]. **Expect the 10k target to stand or fall on Adzuna's quota and the India IT category volume**, with Greenhouse/Lever as a top-up.

**Engineering recommendations:**
- Use PG18's `INSERT … ON CONFLICT … RETURNING old.*, new.*` (verified live) to classify inserted, updated and unchanged rows in one statement. On a hash change, null `embedding` so one "embed where NULL" pass serves both the pipeline and the SRCH-01 backfill.
- Run APScheduler 3.11 `AsyncIOScheduler` and a `SKIP LOCKED` task consumer in the one worker, guarded by a Postgres advisory lock. The admin endpoint **enqueues** an `ingest` task rather than running ingestion in the API.
- Build the LLM gateway around `AsyncAnthropic().messages.parse(output_format=Model)`, which returns `parsed_output`. Cost comes from `usage` multiplied by a price table in config. The budget is checked against `SUM(llm_calls.cost_usd)` for the current UTC month.

**Primary recommendation:** Plan in this order:
1. Human checkpoint for the Adzuna keys.
2. ING-00 spike CLI and ADR 0001.
3. Schema and migration.
4. Normalisers, configs and taxonomy (pure functions, heavily unit-tested).
5. The adapters with respx fixtures.
6. Pipeline, expiry and embedding.
7. Tasks queue, LLM gateway and enrichment.
8. Worker scheduler, CLI and admin endpoint.
9. Production backfill, after the Anthropic-key checkpoint.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Source fetching (Remotive/Adzuna/GH/Lever) | Worker process | CLI (one-shot) | OPS-02: never in API processes. The CLI runs inline for operators under the same advisory lock. |
| Scheduling | Worker (APScheduler) | — | There is exactly one scheduler, protected by an advisory lock. |
| Normalisation, skills, hashing | Worker (pure Python modules) | — | Deterministic, unit-testable, no I/O |
| Persistence, upsert, expiry, dedupe | Database (SQL set operations) | Worker | Idempotency and counting belong in SQL (`ON CONFLICT`, `RETURNING old/new`). |
| Embedding | Worker (fastembed in a thread) | API (query embeddings later) | CPU work goes off the event loop via `anyio.to_thread.run_sync`. |
| Async background work (enrichment, ingest triggers) | Database (`tasks` table) | Worker consumer | Postgres-only queue (FND-07) |
| LLM calls | `ranker/llm/` gateway (worker this phase) | — | Single choke point for logging, budget and the fake provider |
| Admin trigger | API (`/api/admin/ingest/{source}`) | Database (`tasks`) | The API only enqueues and the worker executes. |
| Config (sources, cities, fx, skills, prices) | `backend/config/*.yaml` baked into the image | Settings env for secrets | The Docker build context is `backend/`, so a repo-root `config/` would not ship. |

## Standard Stack

### Core (already locked in `backend/uv.lock`)
| Library | Version | Purpose | Note |
|---------|---------|---------|------|
| apscheduler | 3.11.3 | Cron in the worker | Locked. Defaults verified in installed source: `misfire_grace_time=1`, `coalesce=True`, `max_instances=1` [VERIFIED: `.venv/.../apscheduler/schedulers/base.py`] |
| httpx | 0.28.1 | Source HTTP | Locked (dev group only today, so **move it to the main dependencies**) |
| pyyaml | 6.0.4 | Config files | Locked transitively. Add it explicitly. |
| fastembed / onnxruntime / numpy | 0.9.1 / 1.32.0 / 2.5.5 | Embeddings | Locked |
| sqlalchemy[asyncio], psycopg, alembic | 2.1.x / 3.3.x / 1.20.x | DB | Locked |

### To add
| Library | Version (PyPI, 2026-11-21) | Purpose | Why |
|---------|---------|---------|-----|
| anthropic | 2.12.0 (uploaded 2026-11-20) | LLM provider behind `ranker/llm/` | `messages.parse(output_format=…)` → `parsed_output` [CITED: platform.claude.com structured-outputs] |
| pgvector | 0.5.1 | SQLAlchemy `Vector(384)` type | Returns `list` (CLAUDE.md) |
| nh3 | 0.3.8 | HTML → text (NORM-01) | Rust-backed. `nh3.clean(html, tags=set())` |
| tenacity | 9.2.1 | Retry/backoff (ING-07) | `wait_random_exponential`, `stop_after_attempt(5)` |
| typer | 0.29.2 | `ranker` CLI | `[project.scripts] ranker = "ranker.cli:app"` |
| respx (dev) | 0.23.1 | Mock httpx | No live network in tests |
| time-machine (dev) | 3.6.0 | Freeze time | Expiry and TTL tests |
| hypothesis (dev) | 6.170.17 | Property tests | Salary parser, skill matcher, dedupe key |

**Installation:**
```bash
cd backend
uv add "anthropic>=2.12,<3" "pgvector>=0.5.1,<0.6" "nh3>=0.3.8,<0.4" "tenacity>=9.2,<10" "typer>=0.29,<0.30" "httpx>=0.28,<0.29" "pyyaml>=6.0,<7"
uv add --dev "respx>=0.23,<0.24" "time-machine>=3.6,<4" "hypothesis>=6.170,<7"
```

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-written `tasks` table | procrastinate 3.12 | Off-the-shelf, but hides the SKIP LOCKED skill the portfolio should show. About 150 lines by hand is fine. |
| tenacity | anthropic SDK built-in retries (for LLM only) | Use the SDK's retries for LLM calls (`max_retries=2`) and tenacity for source adapters. |
| nh3 | BeautifulSoup `get_text` | nh3 is faster with no parser dependency. Use `html.unescape` + regex for block-level newlines. |
| Cron in IST via `timezone="Asia/Kolkata"` | Cron expressed in UTC | IST has no DST, so UTC triggers avoid any tzdata dependency in the slim image. Comment the IST equivalent. |

## Package Legitimacy Audit

slopcheck could not be installed in this session (the action was blocked by the environment's permission classifier). Per protocol, every newly added package is **[ASSUMED]** for slopcheck purposes. All of them come from the research-verified stack in CLAUDE.md, and their versions were read live from the PyPI JSON API today.

| Package | Registry | Age | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-------------|-----------|-------------|
| anthropic | PyPI | multi-year, official | github.com/anthropics/anthropic-sdk-python | not run | Approved [ASSUMED]; official vendor SDK, named in Anthropic docs |
| pgvector | PyPI | multi-year | github.com/pgvector/pgvector-python | not run | Approved [ASSUMED] |
| nh3 | PyPI | multi-year | github.com/messense/nh3 | not run | Approved [ASSUMED] |
| tenacity | PyPI | multi-year | github.com/jd/tenacity | not run | Approved [ASSUMED] |
| typer | PyPI | multi-year | github.com/fastapi/typer | not run | Approved [ASSUMED] |
| respx | PyPI | multi-year | github.com/lundberg/respx | not run | Approved [ASSUMED] |
| time-machine | PyPI | multi-year | github.com/adamchainz/time-machine | not run | Approved [ASSUMED] |
| hypothesis | PyPI | multi-year | github.com/HypothesisWorks/hypothesis | not run | Approved [ASSUMED] |

**Packages removed:** none. **Flagged [SUS]:** none. The planner may add one `checkpoint:human-verify` covering the `uv add` batch, or accept these on the strength of the CLAUDE.md verification.

## Source Facts (ING-00 inputs)

### Remotive [VERIFIED: live GET 2026-11-21 17:41 UTC]
- `GET https://remotive.com/api/remote-jobs` returned `job-count: 16`, `total-job-count: 16`. One call returns the full feed.
- Categories: Software Development 4, Data and Analytics 1, Information Technology 1, Artificial Intelligence 1, and the rest non-tech. **About 5–7 in scope.**
- `candidate_required_location` values seen: `Worldwide` (7), `USA` (4), `LATAM, Europe, USA, Canada, APAC` (2), `USA, Canada, USA timezones`, `USA, Canada`, `Europe, USA, Canada, Australia, Singapore, Mexico, Saudi Arabia`.
- Fields: `id, url, title, company_name, company_logo, category, tags[], job_type, publication_date, candidate_required_location, salary, description`.
- `publication_date` is **naive ISO** (`2026-11-19T14:58:42`) with no timezone. Treat it as UTC.
- `salary` is free text, e.g. `$14/hour`, `$170k - $200k`, `$35,3k- $52k` (**decimal comma**), `OTE $25k - $35k`, `$50-$75 /hour`. 12 of 16 jobs had one.
- Descriptions are full HTML, 2.6k–44k characters.
- Legal notice (in the payload): link back to the Remotive URL **and** mention Remotive. Don't submit the jobs to third-party sites (Jooble, Google Jobs, LinkedIn…). Jobs are delayed 24 h. "Displaying our jobs in order to collect signups/email addresses … constitutes a breach". Advises "max. 4 times a day" and says "excessive requests will be blocked".

### Adzuna [CITED: api.adzuna.com/v1/api-docs/adzuna (swagger 1.2), developer.adzuna.com/docs/search, /docs/terms_of_service]
- Endpoint: `GET https://api.adzuna.com/v1/api/jobs/in/search/{page}?app_id&app_key&…`. `in` is in the country enum.
- Search parameters (swagger): `results_per_page`, `what`, `what_and`, `what_phrase`, `what_or`, `what_exclude`, `title_only`, `location0..7`, `where`, `distance` (default 10 km), `max_days_old`, `category`, `sort_direction ∈ {up, down}`, `sort_by ∈ {date, salary, relevance}`, `salary_min/max`, `salary_include_unknown`, `full_time`, `part_time`, `contract`, `permanent`, `company`.
- `results_per_page` max is **not stated officially**. Third parties say 50 [ASSUMED]. The spike should try 50 and record the result.
- Response: `count` (total matches), `mean`, `results[]`. Each job has `id`, `title`, `description` ("**truncated to 500 characters**"), `full_description` ("may not be present"), `redirect_url` ("necessary to be compliant with Adzuna's terms"), `created` (ISO 8601), `salary_min/max`, `salary_is_predicted`, `contract_type` (permanent/contract), `contract_time` (full_time/part_time), `company.display_name`, `company.canonical_name`, `category.tag/label`, `location.area[]` (e.g. `[India, Karnataka, Bangalore]`), `location.display_name`, `latitude/longitude`, `adref`.
- Other endpoints: `/jobs/in/categories` gives category tags. The India IT tag is probably `it-jobs` [ASSUMED], so the spike's first call confirms it. `/jobs/in/ad/{adref}` re-checks one ad (costs quota, so don't use it for expiry).
- **Quota: unpublished.** The ToS says limits "are shown in your account" and "We may vary limits". Third-party claims conflict: ~25/min, 250/day, 1,000/week, 2,500/month (project PITFALLS.md, LOW) versus "~1,000 calls/month" (jobspipe.dev, LOW). **Read the dashboard at the checkpoint.**
- ToS: free tier "intended for evaluating the API". Permitted use includes publishing Adzuna ad listings and personal research. Commercial or revenue/lead-generating use needs a licence after a 14-day trial. Every displayed advert needs an "Adzuna" label of at least 116×23 px, hyperlinked to Adzuna's local domain. Salary estimates must be labelled "Adzuna Jobsworth" with a 20×20 icon. On termination, "remove … all data acquired from Adzuna". No storage duration is stated.

### Greenhouse Job Board API [VERIFIED: live probe + CITED: docs.greenhouse.io/job-board.html]
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true` returns **all** posts, with no pagination. `meta.total` is present. No auth is needed for GET. "Requests may be rate limited."
- Fields with `content=true`: `id`, `internal_job_id`, `title`, `absolute_url`, `location.name`, `updated_at` and `first_published` (ISO with offset), `company_name`, `content`, `departments[]`, `offices[]`, `metadata[]`.
- `content` is **HTML-entity-escaped** (`&lt;div&gt;…`). The docs say editor HTML "will be automatically converted into corresponding HTML entities".
- Pay ranges come only from the single-job endpoint with `pay_transparency=true` (`pay_input_ranges[].min_cents/max_cents/currency_type`). Skip them; it costs one call per job.
- Probe results: `groww` 200 (9 jobs; `absolute_url` on `job-boards.eu.greenhouse.io` but the API host is the same), `razorpay` 404, `postman` 404. A 404 is the cheap "not a board" signal.

### Lever Postings API [VERIFIED: live probe + CITED: github.com/lever/postings-api]
- `GET https://api.lever.co/v0/postings/{site}?mode=json` returns all postings. `skip`/`limit` exist but the probe got everything without them. The EU host is `api.eu.lever.co`, and an EU site 404s on the global host.
- Fields: `id` (uuid), `text` (title), `categories{location, commitment, team, department, allLocations[]}`, `country` (ISO-2 or null), `workplaceType`, `createdAt` (**epoch ms**), `hostedUrl`, `applyUrl`, `descriptionPlain`, `description`, `lists[]`, `additionalPlain`, and optional `salaryRange{currency, interval, min, max}`.
- `workplaceType`: docs say `unspecified | on-site | remote | hybrid`, but the **live value was `onsite`**. Accept both spellings.
- The docs only rate-limit application POSTs and note that postings "may be scraped by third parties".
- Probe results: `cred` 11, `meesho` 48, `paytm` 199. Paytm locations: Noida 99, Mumbai 30, Bangalore 13, **Luxembourg 6**. Teams were mostly non-tech.

### Volume math for the ADR (to be filled by the spike)
- Remotive: about 5 tech jobs.
- Adzuna: steady-state active ≈ (new in-scope IT ads per day) × TTL days, capped by `calls_per_day × results_per_page`. Example: 1,500 new/day × 30-day TTL means 45k addressable, but at 50 per page that needs 30 calls/day (~900/month). A 1,000/month quota still works. 250/day is ample.
- Greenhouse/Lever: about 30 boards × ~5–40 in-scope India/remote jobs ≈ 300–1,500 [ASSUMED].

## ING-00 Spike Design

Build it as a CLI command (`ranker spike sources`) that writes a JSON report. Keep the code in the repo, because ADR 0001 cites its output.

1. **Remotive:** 1 call. Count total, in-scope and region breakdown.
2. **Adzuna** (about 12–20 calls, under any plausible quota):
   1. `/jobs/in/categories` (1 call) confirms the IT tag.
   2. `search/1?category=<it>&results_per_page=50&sort_by=date` with `max_days_old` ∈ {1, 7, 30} (3 calls). Read `count` for daily, weekly and monthly volume.
   3. Without a category: `what_or=developer engineer software data devops`, also with `max_days_old=1` (1–2 calls). Compare the counts.
   4. `where=Bangalore` versus `where=Bengaluru` (2 calls). This decides the spelling and checks for double counting.
   5. `results_per_page=100` (1 call) to test the cap.
   6. Deep page, e.g. `search/40` (1 call), to check whether deep pagination is capped.
   7. From the sampled results, measure: `salary_is_predicted` share, `full_description` presence, description length, `contract_time` presence and title in-scope rate.
   8. Record response headers for any rate-limit headers.
3. **Greenhouse/Lever:** iterate the seed list (about 30 boards; 1 call each, 1 s apart). Record 200/404, total jobs, in-scope India/remote count and the EU host fallback.
4. Output `docs/decisions/0001-sources.md`. Include: the measured numbers with dates; the quota as read from the dashboard (screenshot text); the terms summary per source; the resulting target (e.g. "≥ 10k" or "~N k because …"); the ING-13 decision; and the Adzuna call budget (`calls/day ≤ 0.8 × quota/day`, also checked against `quota/month ÷ 31`).

## Architecture Patterns

### System Architecture Diagram

```
                ┌───────────── worker process (one, advisory-locked) ─────────────┐
 APScheduler ──▶│ ingest job(source) ──▶ adapter.fetch() ──RawJob stream──▶        │
 cron (UTC)     │                       (httpx + tenacity, respx in tests)       │
                │        ▼                                                        │
 tasks table ──▶│ task consumer (SKIP LOCKED) ─┬─ kind=ingest ──▶ same pipeline   │
 (api enqueues  │                              └─ kind=enrich ──▶ LLM gateway ──▶ │──▶ Anthropic (or Fake)
  ingest,       │                                                   │ budget/log  │        │
  pipeline      │  pipeline:  scope filter ─▶ normalise (html, loc, region,        │        ▼
  enqueues      │             salary, seniority, company, skills) ─▶ content_hash │    llm_calls
  enrich)       │             ─▶ batch UPSERT (RETURNING old/new) ─▶ counts       │
                │             ─▶ embed WHERE embedding IS NULL (thread) ─────────▶│──▶ jobs.embedding
                │             ─▶ unknown fields? enqueue enrich task             │
                │             ─▶ expiry (absence-by-scope | TTL) unless guard    │
                │             ─▶ simple dedupe recompute ─▶ close ingest_runs     │
                └─────────────────────────────────────────────────────────────────┘
 CLI `ranker ingest X` ── same pipeline inline (takes per-source advisory lock)
 API POST /api/admin/ingest/{source} (X-Admin-Token) ── INSERT tasks(kind='ingest') ─▶ 202 {task_id}
```

### Recommended Project Structure
```
backend/
├── config/                      # baked into image: Dockerfile `COPY config ./config`
│   ├── sources.yaml             # per-source enable, schedule, budgets, seed boards, N
│   ├── cities.yaml              # canonical city + aliases + state + country
│   ├── regions.yaml             # remote-eligibility phrase rules
│   ├── scope.yaml               # tech include/exclude title regex + category maps
│   ├── fx.yaml                  # as_of + rates to INR
│   ├── skills.yaml              # ≥400 canonical skills: aliases, category, ambiguous, provenance
│   └── llm_prices.yaml          # per-model USD/MTok: input, output, cache_write_5m, cache_read, tiers
├── ranker/
│   ├── cli.py                   # typer: ingest, embed, spike, boards, tasks
│   ├── config_loader.py         # typed (Pydantic) loaders for the YAML files, lru_cached
│   ├── db/models.py             # SQLAlchemy 2.1 models; Base.metadata → alembic target_metadata
│   ├── ingest/
│   │   ├── sources/{base.py, registry.py, remotive.py, adzuna.py, greenhouse.py, lever.py}
│   │   ├── normalize/{html.py, location.py, region.py, salary.py, seniority.py, company.py, scope.py}
│   │   ├── skills.py            # taxonomy matcher
│   │   ├── hashing.py           # content_hash, dedupe_key
│   │   ├── expiry.py
│   │   ├── http.py              # shared AsyncClient factory + tenacity policy + URL redaction
│   │   └── pipeline.py
│   ├── embeddings/              # existing embedder + embed_missing() batch service
│   ├── llm/{client.py, providers/{anthropic.py, fake.py}, cost.py, schemas.py, errors.py}
│   ├── tasks/{queue.py, handlers.py, consumer.py}
│   ├── api/admin.py
│   └── worker/__main__.py       # scheduler + consumer + leader lock
└── migrations/versions/0002_jobs_index.py
```

### Pattern 1: Config location and loading
The Docker build context is `backend/` (compose `build: ./backend`, CI `context: backend`), and the Dockerfile copies only `ranker/`, `migrations/` and `alembic.ini`. **Put configs in `backend/config/`, add `COPY config ./config` to the runtime stage, and add `config_dir` to Settings** (default: resolved relative to the package, e.g. `Path(__file__).parents[1] / "config"`). The `.env.example` test requires the setting to be documented. Load each file into a Pydantic model so bad YAML fails at startup.

### Pattern 2: SourceAdapter plugin (ING-04)
```python
class RawJob(BaseModel):
    source: str                     # NOT a Literal: new sources must not touch shared code
    source_job_id: str
    source_scope: str               # 'all' (remotive), board slug (gh/lever), 'search' (adzuna)
    url: HttpUrl                    # remotive url / adzuna redirect_url / absolute_url / hostedUrl
    title: str
    company_name: str
    description_raw: str
    description_is_html: bool
    description_truncated: bool = False   # adzuna: True unless full_description present
    location_text: str | None
    location_parts: list[str] = []        # adzuna location.area; lever allLocations
    country_hint: str | None = None       # lever country, adzuna 'in'
    workplace_hint: str | None = None     # lever workplaceType, remotive => 'remote'
    region_hint: str | None = None        # remotive candidate_required_location
    category_hint: str | None = None      # remotive category, adzuna category.tag, lever team/department
    posted_at: datetime | None            # always tz-aware UTC
    salary_text: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period_hint: str | None = None
    salary_is_estimate: bool = False      # adzuna salary_is_predicted
    employment_type_hint: str | None = None

class FetchResult(Protocol):
    complete_scopes: set[str]       # scopes fetched fully this run (drives absence expiry)
    failed_scopes: dict[str, str]

class SourceAdapter(Protocol):
    name: str
    expiry_policy: Literal["absence", "ttl"]
    def fetch(self, since: datetime | None) -> AsyncIterator[RawJob]: ...
    def result(self) -> FetchResult: ...      # read after the iterator is exhausted
```
Use a registry dict keyed by `name`, populated by explicit imports in `sources/__init__.py`. **Drop SPEC's `CHECK (source IN (...))` on `jobs.source`**, because otherwise every new source needs a migration, which violates ING-04. Validate the source name against the registry in code instead.

### Pattern 3: Idempotent batch upsert with PG18 `RETURNING old/new` (ING-05/09)
Verified live on `pgvector/pgvector:0.8.7-pg18-trixie` (PostgreSQL 18.7):
```sql
INSERT INTO jobs (source, source_job_id, content_hash, title, ..., last_seen_at, last_seen_run_id)
VALUES (...), (...)
ON CONFLICT (source, source_job_id) DO UPDATE SET
  last_seen_at     = EXCLUDED.last_seen_at,
  last_seen_run_id = EXCLUDED.last_seen_run_id,
  missed_runs      = 0,
  status           = CASE WHEN jobs.status = 'expired' THEN 'active' ELSE jobs.status END,  -- reappeared
  content_hash     = EXCLUDED.content_hash,
  title            = CASE WHEN jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN EXCLUDED.title ELSE jobs.title END,
  -- ...same CASE for every content column...
  embedding        = CASE WHEN jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN NULL ELSE jobs.embedding END,
  updated_at       = CASE WHEN jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN now() ELSE jobs.updated_at END
RETURNING new.id, (old.id IS NULL) AS inserted,
          (old.id IS NOT NULL AND old.content_hash IS DISTINCT FROM new.content_hash) AS changed;
```
- inserted → `inserted++`; changed → `updated++`; otherwise `unchanged++`.
- **Embedding and skills recompute only for rows with `embedding IS NULL`** (ING-09). The same `embed_missing()` function powers `ranker embed --missing`. `ranker embed --all --model X` sets `embedding = NULL WHERE embedding_model IS DISTINCT FROM X`, then runs the same pass.
- Simpler alternative: compute skills in Python before the upsert (they are cheap and deterministic) and include them under the same CASE. Then only embedding is deferred.
- `content_hash = sha256` over **source-level fields** (`title | company_name | description_raw | location_text | salary_text | salary_min | salary_max`), not `description_clean`. Otherwise a normaliser or nh3 upgrade silently changes every hash and re-embeds the whole index. Keep a separate `normalizer_version` column and a `ranker renormalize` path for intentional re-runs. This deviates from SPEC §7.3 step 6; record it in the plan.
- Sequence gaps: `ON CONFLICT` consumes a `bigserial` value per attempted row (seen live: ids 1, 2, 5). This is harmless with BIGINT. Don't assert contiguous ids in tests.
- Batch in chunks of about 200–500 rows per statement. Build the statement with SQLAlchemy `insert(...).on_conflict_do_update(...)`, or use `text()` for the `RETURNING old/new` part. SQLAlchemy may not expose `old`/`new` aliases, so `text()` with `executemany` won't return rows. **Use one multi-row VALUES `text()` statement per batch**, or test that `sqlalchemy.dialects.postgresql.insert(...).returning(literal_column("old.id IS NULL"))` works. Treat it as [ASSUMED] until an executor spike confirms it.
- Running the same batch twice changes only `last_seen_at`/`last_seen_run_id`. Define "DB unchanged" in tests as: no content column, `updated_at` or `embedding` changes; counts show `unchanged = N`; and the embedder is called 0 times.

### Pattern 4: Run lifecycle (ING-06)
1. Take the per-source advisory lock (`pg_try_advisory_lock(hashtext('ingest:'||source))`). If it's taken, log and exit (CLI exit code 2).
2. Insert `ingest_runs(status='running')`.
3. Stream the adapter. Each item does validate → scope filter (count `skipped_out_of_scope`) → normalise → buffer → upsert per batch. A per-item exception counts `failed++` and appends `{source_job_id, error_type}` to `errors` (capped at 50 entries, **redacted**).
4. Embed the missing ones.
5. Enqueue enrichment tasks.
6. Expiry, unless guarded.
7. Dedupe recompute.
8. Close with `succeeded | partial | failed`.

Add columns to SPEC's `ingest_runs`: `skipped INT`, `notes JSONB` (board added/rejected log, guard decisions, budget used) and `calls_used INT`. A crash mid-run leaves `running`. At worker start, mark `running` rows older than 2 h as `failed` with error `abandoned`.

### Pattern 5: Expiry policy (ING-08)
- **Absence (Remotive, Greenhouse, Lever):** for jobs with `status='active' AND source=:s AND source_scope = ANY(:complete_scopes) AND last_seen_run_id <> :run_id`, do `missed_runs += 1`. At `missed_runs >= 2`, set `status='expired'`. A Lever board that 5xx'd is not in `complete_scopes`, so its jobs are untouched.
- **TTL (Adzuna):** `expires_at = coalesce(posted_at, first_seen_at) + 45 days`, and also expire if `last_seen_at < now() - 30 days`. Run it as its own daily sweep job, so an Adzuna outage doesn't freeze expiry (old ads really are stale). Both numbers sit in `sources.yaml`.
- **Volume guard:** `normal = median(fetched)` over the last 5 `succeeded` runs for that source (per scope for boards). If `fetched < 0.5 × normal`, the run is `partial` and expires nothing, and the guard decision is recorded in `notes`. With fewer than 2 prior successful runs, no absence expiry happens at all (cold start). A run that ended in a 429 or any adapter-level error is `partial` and never expires anything.
- A reappearing job goes back to `active` with `missed_runs=0` (handled in the upsert).
- Recovery: since `last_seen_run_id` is stored, a bad run can be undone with `UPDATE jobs SET status='active', missed_runs=0 WHERE expired_by_run_id = :bad`. Store `expired_by_run_id`.

### Pattern 6: LLM gateway (FND-06)
```python
# ranker/llm/client.py (shape)
class LLMGateway:
    async def structured[T: BaseModel](self, *, purpose: Purpose, tier: Literal["fast", "agent"],
                                        schema: type[T], system: str, messages: list[MessageParam],
                                        max_tokens: int, user_id: UUID | None = None) -> T: ...
    # raises LLMUnavailable (no key / provider down after retries) or BudgetExceeded; never returns partial junk
    async def complete(...) -> Completion: ...        # Phase 6 use
    async def run_tools(...) -> ToolTurn: ...          # Phase 6; keep raw content blocks (thinking) untouched

class Provider(Protocol):
    async def parse(self, *, model: str, schema: type[T], system: str, messages: list[MessageParam],
                    max_tokens: int, effort: str | None, thinking: dict[str, str] | None) -> ParsedResult[T]: ...
    # ParsedResult carries parsed, usage(input, output, cache_write, cache_read), stop_reason, model
```
- **Anthropic provider:** `AsyncAnthropic(api_key=..., max_retries=2, timeout=httpx.Timeout(30, connect=5))`. Call `await client.messages.parse(model=..., max_tokens=..., system=..., messages=..., output_format=Schema, output_config={"effort": "low"}, thinking={"type": "disabled"})` and read `resp.parsed_output` [CITED: structured-outputs and effort docs]. Haiku 5.5 accepts `thinking: disabled` at effort ≤ high [CITED: effort docs]. Send **no** `temperature`. The client-constructor retry and timeout options are [ASSUMED] from SDK knowledge, so verify them against the installed package source.
- **Fake provider:** constructed with a mapping `schema_name → Callable[[messages], BaseModel] | Exception`. It records the calls, returns a synthetic `usage` (e.g. 100 in / 20 out) so cost logging is exercised, and can simulate `RateLimit`/`Unavailable`. Selection: `LLM_PROVIDER=fake|anthropic`. If `anthropic` is chosen with an empty key, the gateway's `available()` returns False and calls raise `LLMUnavailable` (D-09).
- **Budget:** before each call, read `spent = SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'`, and estimate `worst = price_in × est_input_tokens + price_out × max_tokens`. If `spent + worst > LLM_MONTHLY_BUDGET_USD`, insert an `llm_calls` row with `success=false, error='budget_exceeded', cost_usd=0` and raise `BudgetExceeded` (D-10: skipped and logged). Concurrent overshoot is bounded by worker concurrency × one call, which is acceptable.
- **Cost:** `cost = (in × p_in + out × p_out + cache_write × p_cw + cache_read × p_cr) / 1e6`. Prices come from `config/llm_prices.yaml`. Use the higher Haiku tier if the total prompt is over 100k tokens. An unknown model **fails closed**: treat it as over budget and log an error.
- **Logging:** each call inserts `llm_calls(purpose, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, latency_ms, success, error)`. Store **no prompt or response text** (privacy). `error` is the exception class name only.
- **Schemas:** typed Pydantic models only, with `Literal` enums and no `dict`. Check with a test that walks `model_json_schema()` and fails on `additionalProperties != false` / object-without-properties, `oneOf`, `minimum`/`maxLength`, etc.
```python
class RegionGuess(BaseModel):
    remote_eligibility: Literal["worldwide","india","apac","us_only","eu_only","other_region","unknown"]
    evidence: str                      # short quote; no constraints (unsupported)
class JobEnrichment(BaseModel):        # one call fills all unknown fields for a job
    remote_eligibility: Literal[...] | None
    seniority: Literal["intern","junior","mid","senior","lead","unknown"] | None
    employment_type: Literal["full_time","part_time","contract","internship","unknown"] | None
```
- **Cache:** `llm_enrichment_cache(content_hash, purpose, prompt_version, result JSONB, PRIMARY KEY(content_hash, purpose, prompt_version))`. Check it before enqueueing or calling (D-08 "cache by content_hash").
- **Cost estimate:** one Haiku 5.5 enrichment call is about 1,000 input + 60 output tokens = **$0.00013** at $0.10/$0.50 per MTok [VERIFIED: platform.claude.com pricing]. 10k jobs ≈ $1.30, so the $5 cap covers a full backfill. The Batch API halves this but isn't needed.

### Pattern 7: `tasks` queue with SKIP LOCKED (FND-07)
```sql
CREATE TABLE tasks (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,                         -- 'ingest' | 'enrich_job' | later 'memory_extract', 'rocchio'
  payload JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','dead')),
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 5,
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_at TIMESTAMPTZ, locked_by TEXT,
  last_error TEXT,
  dedupe_key TEXT,
  user_id UUID,                               -- nullable; FK added when users exists (Phase 5)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX tasks_dedupe_pending ON tasks (dedupe_key) WHERE status IN ('queued','running');
CREATE INDEX tasks_ready ON tasks (run_after) WHERE status = 'queued';
```
Claim query (verified live):
```sql
WITH c AS (SELECT id FROM tasks WHERE status='queued' AND run_after <= now()
           ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT :n)
UPDATE tasks t SET status='running', attempts=t.attempts+1, locked_at=now(), locked_by=:me, updated_at=now()
FROM c WHERE t.id = c.id RETURNING t.*;
```
- Commit the claim immediately, then run the handler outside the claiming transaction.
- On success, set `done`.
- On failure: if `attempts >= max_attempts`, set `dead`; otherwise set `queued` with `run_after = now() + min(2^attempts × 30 s, 6 h) + jitter`.
- **Deferral without consuming attempts:** on `LLMUnavailable`/`BudgetExceeded`, set `status='queued', attempts=attempts-1, run_after = now()+1h` (budget: the first of next month). Without this, a missing key burns every task's attempts and kills them (D-09, D-11).
- **Lease reaper:** each minute, `UPDATE tasks SET status='queued' WHERE status='running' AND locked_at < now() - interval '15 min'`.
- "Exactly once" in the success criterion means the claim is exclusive. Handlers must still be idempotent, because the reaper re-queues work after a crash. Test it with two concurrent consumers over 100 tasks: every id is processed exactly once and the attempts sum is 100.
- Enqueue with `INSERT … ON CONFLICT (dedupe_key) WHERE status IN ('queued','running') DO NOTHING`. Use `dedupe_key = 'enrich:'||job_id||':'||content_hash` and `'ingest:'||source`.
- Poll every 2 s when the queue is idle (LISTEN/NOTIFY is optional). Concurrency is 1–2 for LLM tasks.

### Pattern 8: Worker = leader lock + APScheduler + consumer (OPS-02)
```python
async def run(stop):
    async with engine.connect() as lock_conn:              # dedicated session for the advisory lock
        got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(727274)"))).scalar()
        if not got: log.warning("scheduler_lock_held_elsewhere"); # still run consumer, but NOT scheduler
        sched = AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                              "misfire_grace_time": 3600})
        if got:
            for src in enabled_sources: sched.add_job(run_ingest, CronTrigger(hour=..., minute=...),
                                                      args=[src], id=f"ingest:{src}", replace_existing=True)
            sched.add_job(expire_ttl, CronTrigger(...), id="expire_ttl", replace_existing=True)
            sched.add_job(reap_leases, "interval", minutes=1, id="reaper")
            sched.start()
        consumer = asyncio.create_task(consume_tasks(stop))
        await stop.wait(); sched.shutdown(wait=False) if got else None; await consumer
```
- **`misfire_grace_time` defaults to 1 second** (verified in source). If the loop is busy, for example with embedding that is accidentally run synchronously, a cron fire is silently skipped. Set 3600 and keep CPU work in threads.
- The memory job store (default) is correct. Don't use a persistent job store, because the schedules come from config at start.
- `AsyncIOExecutor.shutdown` **cancels** pending futures (verified in source), so an in-flight ingest gets `CancelledError`. That's fine because the run row is marked abandoned on the next start. Compose has `stop_grace_period: 10s`.
- Proposed schedules (UTC with IST in comments):
  - Remotive: 03:40 and 15:40 UTC (09:10 and 21:10 IST). Twice a day, under the 4/day limit.
  - Adzuna: 22:00 UTC (03:30 IST).
  - Greenhouse/Lever: 23:30 UTC (05:00 IST), boards 1 s apart.
  - TTL expiry: 00:30 UTC (06:00 IST).
  - Board re-check/discovery: weekly, Sunday 01:00 UTC.

### Pattern 9: Admin trigger (ING-11)
`POST /api/admin/ingest/{source}` with header `X-Admin-Token`.
- Return **404** if `ADMIN_TOKEN` is unset (the endpoint is invisible).
- Compare with `hmac.compare_digest(given.encode(), token.encode())`. Return 401 on mismatch.
- Validate `source` against the registry (404 if unknown).
- Enqueue `tasks(kind='ingest', dedupe_key='ingest:'||source)` and return `202 {"task_id": ...}`, or 409 if one is already pending.
- The API never fetches. The CLI `ranker ingest <source>` runs inline (`docker compose exec worker ranker ingest adzuna`) under the per-source advisory lock.
- Phase 5 replaces the token with the admin role. Keep the dependency function swappable.

### Anti-Patterns to Avoid
- **Running embedding on the event loop.** It stalls the scheduler (1 s misfire grace) and the consumer. Use `await anyio.to_thread.run_sync(embedder.embed, batch)`.
- **Crawling Adzuna as terms × cities × pages** (SPEC's 8×8×5 = 320 calls/day). It overlaps heavily and exceeds any plausible free quota. Use category + date-sorted + `max_days_old` paging (see Adzuna crawl plan below).
- **Absence-expiring Adzuna.** Search APIs never show the full set.
- **Logging httpx URLs.** The Adzuna `app_key` is in the query string. See Pitfall 1.
- **`description_clean` in `content_hash`.** See Pattern 3.
- **A `source` CHECK constraint.** See Pattern 2.

### Adzuna crawl plan (ING-02)
```yaml
adzuna:
  enabled: true
  country: in
  quota: {per_day: 200, per_month: 2000}     # SET FROM DASHBOARD at checkpoint; code uses 0.8×
  results_per_page: 50
  queries:                                    # each is a paged slice; order = priority
    - {category: it-jobs, sort_by: date, max_days_old: 2, max_pages: 40}
    - {what_or: "data scientist machine learning analytics engineer", sort_by: date, max_days_old: 2, max_pages: 5}
  ttl_days: 45
  unseen_days: 30
  backfill: {max_days_old: 30, max_pages: 200, spread_over_days: 7}   # one-off initial fill
```
- Stop a slice when a page returns fewer than `results_per_page` results or the call budget is reached.
- Count calls in `ingest_runs.calls_used`. The daily/monthly budget is checked against `SUM(calls_used)` over completed runs.
- A 429 ends the run as `partial`.
- `max_days_old=2` overlaps one day for safety. Upserts dedupe by `id`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTML sanitising/stripping | regex tag stripper | `nh3.clean(html, tags=set())` after `html.unescape` (Greenhouse) | Entity edge cases, malformed HTML, script/style content |
| Retry/backoff | custom loops | tenacity `AsyncRetrying(stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=60), retry=retry_if_exception(_is_retryable))` + honour `Retry-After` | Jitter and correct exception filtering |
| LLM JSON parsing/validation | prompt "return JSON" + `json.loads` | `messages.parse(output_format=Model)` | Constrained decoding plus re-validation |
| Cron scheduling | `asyncio.sleep` loops | APScheduler 3.11 `CronTrigger` | Misfire/coalesce semantics |
| Upsert change detection | SELECT-then-INSERT/UPDATE | `ON CONFLICT … RETURNING old/new` | Race-free and one round trip |
| Queue claiming | status flag without locks | `FOR UPDATE SKIP LOCKED` | Exclusive claims under concurrency |
| Single-scheduler guard | PID files | `pg_try_advisory_lock` on a held connection | Survives restarts and works across containers |
| Programming-language alias data | typing aliases by hand | Seed from GitHub Linguist `languages.yml` (MIT): `C++→cpp`, `C#→csharp`, `Go→golang`, `JavaScript→js` [VERIFIED: raw file fetched] | Authoritative, permissive |

**Key insight:** The hard parts of this phase are the data rules (scope, region, salary, skills, expiry), not the plumbing. Keep every rule a pure function over config, with table-driven tests.

## Normalisation Detail

### Region rules (NORM-03, D-06/D-08)
Store `remote_regions TEXT[]` (normalised tokens) **and** `remote_eligibility` (one enum).

| Signal (case-insensitive) | Token |
|---|---|
| `worldwide`, `anywhere`, `global`, `work from anywhere`, `fully remote (anywhere)` | `worldwide` |
| `india`, `remote - india`, `remote (india)`, `ist`, `ist overlap`, `indian time`, `must be based in india`, any Indian city | `india` |
| `apac`, `asia`, `asia pacific`, `singapore`, `gmt+5:30`, `utc+5:30`, `utc+8` | `apac` |
| `usa`, `us only`, `u.s.`, `united states`, `us timezones`, `est/pst/cst`, `north america`, `canada`, `must reside in the us`, `us citizens`, `us work authorization` | `us` |
| `europe`, `eu`, `emea`, `uk`, `cet`, `gmt`, `eu timezone`, named EU countries | `eu` |
| `latam`, `australia`, `mexico`, `saudi arabia`, `africa`, other countries | `other` |

Precedence: `worldwide` → `worldwide`; else `india` → `india`; else `apac` → `apac`; else only `us` → `us_only`; else only `eu` → `eu_only`; else any token → `other_region`; else `unknown`.

Examples from live data: `"LATAM, Europe, USA, Canada, APAC"` → `apac`. `"USA, Canada, USA timezones"` → `us_only`. `"Europe, USA, Canada, Australia, Singapore, …"` → `apac` (Singapore counts as APAC). Record this choice, and note that ranking may want a weaker match for APAC lists that don't name India.

- **On-site/hybrid jobs** in India (Adzuna, India Greenhouse/Lever jobs) → `india`. On-site jobs outside India are out of the D-06 scope and are not stored.
- Field order: source field (Remotive `candidate_required_location`; Lever `country` + `workplaceType`), then description phrase scan (first 3k chars plus the last 1k, where "location requirements" usually sit), then LLM fallback.

### Location (NORM-02)
- `cities.yaml`: canonical city, aliases, state, country=IN, plus `multi` expansions.
  - Bengaluru ← Bangalore, BLR, Bengaluru Urban, Bangalore Rural.
  - Gurugram ← Gurgaon.
  - Mumbai ← Bombay, Navi Mumbai?, Thane? Decide and document; recommend separate canonicals mapped to the `mumbai` metro group.
  - Delhi ← New Delhi.
  - NCR / Delhi NCR → [Delhi, Gurugram, Noida].
  - Noida ← Greater Noida.
  - Hyderabad ← Secunderabad, Cyberabad.
  - Chennai ← Madras. Kolkata ← Calcutta.
  - Plus Pune, Ahmedabad, Kochi (Cochin), Thiruvananthapuram (Trivandrum), Coimbatore, Jaipur, Chandigarh (Mohali), Indore, Bhubaneswar.
  - "Pan India", "Multiple locations", "Anywhere in India" → country IN, city NULL.
- Adzuna: normalise from `location.area` (most specific last), not `display_name`.
- Lever: `categories.allLocations`. Greenhouse: `location.name` (e.g. "Bengaluru-VTP, India", so match tokens on aliases).
- Multi-city: keep primary `city` (the first canonical match) **plus `cities TEXT[]`**, because Phase 3 filters need multi-city. Dedupe key uses the primary.
- `remote_type`: source hint first (Lever `workplaceType` remote/hybrid/onsite|on-site; Remotive always remote), then keywords (`remote`, `wfh`, `work from home` → remote; `hybrid` → hybrid), then onsite if a city was found, else unknown.

### Salary parsing (NORM-04)
Return `ParsedSalary(min, max, currency, period ∈ {year, month, hour}, is_estimate, source_text)`, then INR annual = value × fx[currency] × {year: 1, month: 12, hour: 2080}. Store `salary_fx_as_of DATE` (the fx.yaml `as_of`) per row (D-12).

Table-driven cases (aim for ≥ 50):
- `₹12–18 LPA`, `12-18 LPA`, `12L - 18L`, `12 Lac`, `12 lakhs p.a.` → 1.2M–1.8M INR/year.
- `1.2 Cr` → 12M.
- `₹8,00,000` and `INR 8,00,000 - 12,00,000` (Indian digit grouping).
- `₹50k/month`, `30,000/month` → monthly × 12.
- `$120k–$150k` → USD annual.
- `$14/hour`, `$50-$75 /hour` → hourly × 2080.
- **`$35,3k- $52k`** (decimal comma, live Remotive) → 35.3k.
- `OTE $25k - $35k` → treat as annual and keep it.
- `€60k`, `£50k`.
- `₹10 LPA + ESOPs`.
- `CTC 15 LPA (fixed 12)` → take CTC, document.
- `Competitive`, `As per industry`, `DOE` → null.

Bare-`k` ambiguity: with ₹ and no period, values under 200k are monthly; with $/€/£, `k` is annual.

Adzuna gives numeric `salary_min/max` in INR (local currency). `period=year` is [ASSUMED], so check against the spike sample: values under 200k suggest monthly. `salary_is_predicted` (`"1"`/`1`/`true`) → `salary_is_estimate=true`.

Lever `salaryRange{currency, interval, min, max}` maps directly (`per-year-salary`, `per-month-salary`, `per-hour-wage` [ASSUMED interval strings]).

Sanity bounds: INR annual outside [50k, 50M] → keep the raw values, null the INR fields, and log it.

### Seniority and employment type (NORM-05)
- Title rules:
  - intern: `intern`, `trainee`, `apprentice`, `graduate trainee`.
  - junior: `junior`, `jr`, `associate`, `sde[- ]?i\b`, `engineer i\b`, `fresher`, `entry level`, `0-2 years`.
  - senior: `senior`, `sr\.?`, `sde[- ]?iii`, `engineer iii`, `ii{2,}`.
  - lead: `lead`, `staff`, `principal`, `architect`, `head of`, `manager` (engineering manager → lead).
  - mid: `sde[- ]?ii\b`, `engineer ii\b`, `mid`.
- Then "N+ years" in text: 0–1 → junior, 2–4 → mid, 5–7 → senior, 8+ → lead.
- Employment type from hints: Remotive `job_type` (full_time, contract, part_time, freelance → contract, internship); Adzuna `contract_time` + `contract_type`; Lever `commitment` ("Full-time Employment", "Intern", "Contract"); Greenhouse metadata if present. Then title (`intern` → internship). Then unknown, which triggers LLM enrichment.

### Scope (D-05)
**Skip out-of-scope jobs** (not stored). Count them in `ingest_runs.skipped` and keep a sample of titles in `notes`. Rationale: storage stays clean; Adzuna/board fetches are cheap to repeat; reclassification needs a refetch, which is acceptable for daily feeds.

Rules:
- Include if the title matches the tech regex, **or** the category is in the tech map (Remotive: Software Development, Data and Analytics, DevOps / Sysadmin, QA, Artificial Intelligence; Adzuna `it-jobs`) **and** the title doesn't match an exclude.
- Include regex terms: engineer, developer, sde, programmer, devops, sre, platform, data (engineer|scientist|analyst?), ml, machine learning, ai, qa, sdet, test automation, security engineer, mobile, android, ios, backend, frontend, full stack.
- Exclude: product manager, designer, ui/ux designer, support, helpdesk, it admin, system administrator?, sales, recruiter, marketing.
- Decide "data analyst" explicitly. Recommend including it under data.
- Keep the rules in `scope.yaml` and test them on about 60 labelled titles.

### Skills taxonomy (NORM-06)
- Hand-curate ≥ 400 canonicals in `skills.yaml`: `{canonical, aliases[], category, ambiguous: bool, context?: [regex]}` plus a header block with provenance.
- Seeds:
  - (a) GitHub Linguist `languages.yml`, **MIT** [VERIFIED: LICENSE fetched], for programming-language names and aliases.
  - (b) O*NET Technology Skills / Hot Technologies, **CC BY 4.0** with version-specific attribution ("O*NET 30.x Database") [CITED: onetcenter.org/license_db.html], for tools, frameworks and databases.
  - (c) Hand additions for the Indian market (e.g. Spring Boot, Flutter, Kotlin, PySpark, Airflow, dbt, Snowflake, Kafka, GCP/AWS/Azure services).
- **Avoid** Lightcast Open Skills (licence terms conflict on commercial use, and Wikipedia-derived descriptions are CC BY-SA) and Stack Overflow tag dumps (CC BY-SA share-alike would attach to the YAML) [CITED: lightcast.io/open-skills FAQ via search, LOW-MEDIUM].
- **Matcher:** compile one alternation, longest alias first, with **custom boundaries**, because `\b` fails next to `+`, `#` and `.`:
```python
BOUND_L = r"(?<![A-Za-z0-9_+#.])"
BOUND_R = r"(?![A-Za-z0-9_+#])"          # allow trailing '.' (sentence end) but not '.js' continuation handled by alias order
pattern = re.compile(BOUND_L + "(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + ")" + BOUND_R, re.I)
```
- Required cases:
  - `C++` → cpp. `C#` → csharp. `.NET`/`dotnet`/`ASP.NET` → dotnet (asp.net → aspnet + dotnet).
  - `Node.js`/`NodeJS`/`node` (ambiguous) → nodejs.
  - `Go` only with case-sensitive `Go` plus a context (`golang`, `Go developer`, `Go,` in a skills list, `in Go`), else skip. `Golang` → go.
  - `JavaScript` must not yield `java`. `React Native` before `React`. `Spring Boot` before `Spring`.
  - `R` and `C` are case-sensitive, need list context, and are flagged ambiguous.
  - Others to guard: `Swift` (SWIFT payments), `Rust`, `Express`, `Make`, `Excel`, `Unity`.
- Measure per-skill precision on about 30 hand-checked JDs (acceptance).
- **Phase 3 note:** a generated `tsvector` **cannot** include `array_to_string(skills, ' ')`. Verified live: "generation expression is not immutable" (`array_to_string` is STABLE). If Phase 3 wants skill tokens in `tsv`, add a plain `skills_text TEXT` column now, maintained at ingest with canonical tokens like `cplusplus csharp dotnet golang nodejs`, and include it in the generated column. Default `to_tsvector('english', 'C++ / C# / .NET / Node.js / Go')` gives `'c' 'net' 'node.js' 'go'` (verified), so C++ and C# collapse to `c`.

### Company normalisation (NORM-07)
- Steps: NFKC → lowercase → strip punctuation → drop suffix tokens (`pvt`, `private`, `ltd`, `limited`, `llp`, `llc`, `inc`, `corp`, `corporation`, `gmbh`, `co`, `india`, `technologies`?) only **at the end** and iteratively → collapse whitespace.
- Be careful with `technologies`/`labs`/`solutions`: drop them only if the remainder is at least 3 characters. Document it.
- Prefer Adzuna `company.canonical_name` when present.
- Placeholder names (`confidential`, `hiring company`, `stealth`, `a client of …`, staffing agencies list) → `companies.is_placeholder = true`. Phase 6 dedupe and diversity exclude them.
- Upsert: `INSERT INTO companies(name, normalized_name) … ON CONFLICT (normalized_name) DO UPDATE SET name = companies.name RETURNING id`.

### Simple dedupe this phase
`dedupe_key = sha1(company_norm | title_norm | seniority | (city or remote_type))`. **Keep seniority in the key** (PITFALLS: otherwise junior and senior roles merge). At the end of each run, among active rows per affected `dedupe_key`, the newest `posted_at` (then the longest `description_clean`) is canonical (`canonical_job_id NULL`) and the others point to it. When the canonical expires, the recompute promotes the next one. Recompute set-based over the keys touched in this run plus the keys of jobs expired in this run.

## Common Pitfalls

### Pitfall 1: Leaking the Adzuna `app_key`
**What goes wrong:** httpx logs `HTTP Request: GET https://api.adzuna.com/...&app_key=...` at INFO. `httpx.HTTPStatusError` messages contain the full URL, and those end up in `ingest_runs.errors` and Sentry.
**How to avoid:** Set the `httpx`/`httpcore` loggers to WARNING in `configure_logging` (worker and CLI). Store errors as `type(exc).__name__ + status_code` only. Add a `redact_url()` helper that strips `app_id`/`app_key` from any URL logged. Add `app_key` and `app_id` to the redaction keys. Test: run the Adzuna adapter against respx with a 500, then assert that the key string is absent from captured logs and from the `errors` JSON.

### Pitfall 2: A missing LLM key burns task attempts
**What goes wrong:** Enrichment tasks fail 5 times while `ANTHROPIC_API_KEY` is empty and go `dead`. When the key arrives, nothing gets enriched.
**How to avoid:** `LLMUnavailable`/`BudgetExceeded` → defer without incrementing attempts (Pattern 7). Test: with the fake set to unavailable, run the consumer 10 times; attempts stay 0 and status stays queued.

### Pitfall 3: Config doesn't ship in the image
**What goes wrong:** Tests pass locally, but the worker crashes in production with `FileNotFoundError: config/sources.yaml`.
**How to avoid:** Use `backend/config/` + Dockerfile `COPY config ./config`. Extend the CI image selfcheck (`ranker.selfcheck`) to load every config file.

### Pitfall 4: `filterwarnings=error` and unregistered marks
**What goes wrong:** `@pytest.mark.llm` without registration raises `PytestUnknownMarkWarning`, which becomes an error.
**How to avoid:** Add `markers = ["llm: real LLM calls (opt-in)"]` and `addopts = "-m 'not llm'"` in pyproject. Also watch for respx/httpx `ResourceWarning`s from unclosed clients: use `async with` clients in adapters.

### Pitfall 5: Naive datetimes
**What goes wrong:** Remotive `publication_date` has no timezone, so comparing it with `timestamptz` either errors or is silently shifted. Lever `createdAt` is epoch ms; treating it as seconds gives dates in 58000 AD.
**How to avoid:** Each adapter returns tz-aware UTC. Unit-test each parser.

### Pitfall 6: Greenhouse double-escaped HTML
**What goes wrong:** `nh3.clean` on `&lt;p&gt;` produces literal `<p>` text in `description_clean`.
**How to avoid:** Run `html.unescape()` first for Greenhouse, then nh3, then a whitespace/newline normaliser. Use a fixture taken from the live Groww payload shape.

### Pitfall 7: The embedding blocks the loop and misfires cron
**What goes wrong:** A 64-item fastembed batch on 2 vCPUs takes seconds. Run synchronously in the worker loop, it delays the scheduler past the 1 s default `misfire_grace_time`, and the job is skipped silently.
**How to avoid:** `anyio.to_thread.run_sync` + `misfire_grace_time=3600`.

### Pitfall 8: Memory on the 4 GB e2-medium
**What goes wrong:** db + api (model) + worker (model + embedding batches) + web + caddy share 4 GB. fastembed RSS per process is roughly 150–400 MB [ASSUMED; ARCHITECTURE.md says 150–300 MB, MEDIUM]. Large batches with long texts raise peak activation memory.
**How to avoid:**
- Worker embedding batch is 32. Text = title + company + top skills + the first 1,500 chars (EMB-01), which keeps sequences short. `EMBED_THREADS=2`.
- Set compose `mem_limit` on worker (e.g. 1 GB) and api so an OOM kills a container, not Postgres.
- Run `deploy/measure-memory.sh snapshot` during the first production backfill and record the result in the ADR.
- Postgres `shared_buffers` at default 128 MB is fine at 10k rows. The HNSW index at 10k × 384 floats is about 20–30 MB [ASSUMED].

### Pitfall 9: Deep pagination and budget exhaustion on Adzuna
**What goes wrong:** Requesting page 200 of a date-sorted query may return empty or error (cap unknown), so the backfill wastes quota.
**How to avoid:** The spike measures the deep-page behaviour. The crawler stops on the first short page. Track a per-run and per-day budget, and record `calls_used`.

### Pitfall 10: Terms compliance is a data-model concern
**What goes wrong:** Later UI phases can't show "Adzuna" link labels or Remotive credit if source and URL aren't stored, or if `redirect_url` is replaced with a scraped canonical.
**How to avoid:** Store `url` as the source-mandated link (`redirect_url` for Adzuna, the Remotive URL for Remotive). Add a `ranker purge-source <name>` CLI (sets `status='removed'`, clears descriptions) for ToS termination. Write down in the ADR that Remotive jobs must never sit behind signup (relevant for Phase 5 auth).

### Pitfall 11: A board slug is used unvalidated in a URL path
**What goes wrong:** A discovered slug like `../` or `foo?x=` ends up in the URL path or query string.
**How to avoid:** Validate with `^[a-z0-9][a-z0-9-]{0,62}$` before use, and build URLs with httpx `params=` and quoted path segments.

## Greenhouse/Lever discovery (D-03, if ING-13 is pulled in)

- **Seed list** (~30) in `sources.yaml`, each `{ats: greenhouse|lever, slug, host: global|eu}`.
  - Verified today: Lever `cred`, `meesho`, `paytm`; Greenhouse `groww`. Verified 404: Greenhouse `razorpay`, `postman`.
  - The rest are [ASSUMED]. The spike's board pass validates them and drops 404s.
  - Candidates to try:
    - Indian product companies: zerodha, swiggy, zomato, phonepe, cred, meesho, groww, sharechat, dream11, razorpay (other ATS), freshworks, browserstack, zeta, slice, jupiter, urban company, unacademy, upgrad, inmobi, rapido, zepto, postman (other ATS).
    - Remote-first companies hiring APAC/worldwide: gitlab, automattic, canonical, toptal, deel, remote.com, hashicorp.
- **Discovery sources this phase:** Greenhouse/Lever URLs found in Remotive URLs and descriptions; `companies.normalized_name` candidates tried as slugs (`name`, `name-with-dashes`, `namehq`) on both ATSs (a 404 is a cheap reject); later, HN posts (Phase 6). Adzuna `redirect_url` goes through Adzuna and **won't** reveal ATS slugs.
- **Accept rule:** at least **N=5** in-scope jobs located in India or remote-open-to-India (`remote_eligibility ∈ {worldwide, india, apac}`). Log every decision in a `source_boards(ats, slug, host, status ∈ {seed, active, rejected, dropped}, in_scope_count, last_checked_at, reason)` table and print it with `ranker boards report`. Copy the run's added/rejected lists into `ingest_runs.notes`.
- **Re-check weekly.** Drop a board after 2 consecutive checks with 0 in-scope jobs. A 404 drops it immediately (the company moved ATS).
- **Politeness:** 1 request/s, a descriptive User-Agent without personal contact details (e.g. `TopFit-jobs-indexer/1.0`), and tenacity retry on 429/5xx.

## Runtime State Inventory
Not a rename/refactor phase. **Omitted.**

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker + pgvector PG18 image | testcontainers tests | ✓ | `pgvector/pgvector:0.8.7-pg18-trixie` present locally; PG 18.7 | — |
| uv | deps | ✓ | 0.9.18 (local) | — |
| Remotive API (public) | ING-01, spike | ✓ | live 200 | — |
| Greenhouse/Lever public APIs | ING-13, spike | ✓ | live 200 | — |
| Adzuna keys | ING-00/02 live | ✗ | — | **Blocking for the spike and production. Human checkpoint (D-04).** Code and tests use respx. |
| Anthropic API key | Production enrichment | ✗ | — | Not blocking: fake provider; fields stay `unknown` and tasks defer (D-09). Checkpoint before the production backfill. |
| slopcheck | package audit | ✗ (install blocked) | — | Packages marked [ASSUMED] |
| Production VM (GCP e2-medium, 4 GB) | Production backfill | ✓ (per CONTEXT) | — | Measure memory during the backfill |

## Validation Architecture (compact; nyquist disabled)

Framework: pytest 9.2 + pytest-asyncio 1.5 (`asyncio_mode=auto`) + testcontainers PG18. Quick run: `cd backend && uv run pytest -q -x -m "not llm"`. Full run: `uv run pytest -q`.

| Req | Behaviour | Test |
|-----|-----------|------|
| ING-05/09 | Same batch twice → unchanged=N, embedder called 0 times; hash change → embedding NULL → re-embedded | integration (respx + FakeEmbedder spy) |
| ING-07 | 429 then 200 → success; 5 × 500 → failed; Retry-After honoured (time-machine/sleep patched) | unit (respx) |
| ING-08 | 40% fetch → no expiry; 2 missed complete runs → expired; failed board scope untouched; Adzuna TTL at day 46 | integration + time-machine |
| ING-06 | Counts correct and errors redacted (no `app_key`) | integration |
| ING-11 | CLI exit codes; admin 404 when token unset, 401 on bad token, 202 enqueues | API + CLI tests |
| NORM-01..07 | Table-driven: ≥50 salary cases, region phrases, city aliases, ≥30 skill cases incl. C++/C#/Go/.NET/Node.js/JavaScript-vs-Java, company suffixes, scope titles | unit + hypothesis |
| FND-06 | Fake provider: logs `llm_calls` row with cost; over budget → skipped + logged; schema-lint test rejects `dict` | unit/integration |
| FND-07 | Two concurrent consumers over 100 tasks → each processed once; reaper re-queues stale; defer keeps attempts | integration |
| OPS-02 | Second worker can't take the scheduler lock; scheduler never imported in `ranker.main` | integration + import test |
| SRCH-01 | `ranker embed --missing` fills NULLs with `embedding_model` set; dimension 384 | integration |

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | partial | Static `ADMIN_TOKEN` header with `hmac.compare_digest`; endpoint returns 404 when the token is unset |
| V4 Access Control | yes | Admin-only trigger. The API only enqueues work. |
| V5 Input Validation | yes | Pydantic `RawJob`; source name checked against the registry; slug regex; YAML loaded into Pydantic models |
| V7 Errors/Logging | yes | URL/key redaction; no prompt or response text in `llm_calls`; error class names only |
| V12/V14 Data from 3rd parties | yes | nh3 sanitisation; `description_raw` is never rendered (NORM-01) |

| Threat | STRIDE | Mitigation |
|--------|--------|-----------|
| Adzuna key leaked via logs/errors | Information disclosure | Pitfall 1 controls + test |
| Prompt injection in job descriptions sent to the enrichment LLM | Tampering | Output is enum-only typed schema; description wrapped in delimiters; the LLM can only choose enum values |
| Admin token brute force | Spoofing | Long random token (`openssl rand -hex 32`); later rate limiting (Phase 3 SEC-04) |
| SSRF/path injection via discovered slugs | Tampering | Slug regex; fixed hosts allow-list (`boards-api.greenhouse.io`, `api.lever.co`, `api.eu.lever.co`) |
| Budget exhaustion (cost DoS) | DoS | Gateway budget check before every call; enrichment concurrency 1–2 |
| Malicious HTML (XSS later) | Tampering | Store sanitised text only for display; never render raw |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `xmax = 0` trick to detect insert vs update | `RETURNING old.*, new.*` | PostgreSQL 18 (2025-09) | One statement classifies inserted, updated and unchanged rows [VERIFIED live] |
| Beta header for Claude structured outputs | GA `output_config.format` / `messages.parse` | 2026 | No beta header needed [CITED] |
| `temperature` for determinism | Omit it on Haiku 5.5; use `output_config.effort` | Claude 5.x | Passing `temperature` is unsupported or discouraged [CITED: CLAUDE.md, effort docs] |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Adzuna `results_per_page` max is 50 | Source Facts | Wrong call budget. The spike measures it. |
| A2 | Adzuna India IT category tag is `it-jobs` | Crawl plan | Wrong category. The spike's categories call fixes it. |
| A3 | Adzuna quota ≈ 250/day or ≈ 1,000/month (conflicting third-party claims) | Source Facts | The 10k target may be unreachable. The dashboard reading decides. |
| A4 | Adzuna numeric salaries are annual INR | Salary | Monthly salaries inflated 12×. Spike sample check. |
| A5 | Greenhouse/Lever seed slugs beyond the 4 verified ones exist | Discovery | Lower top-up volume. The spike validates them. |
| A6 | Greenhouse/Lever adds ~300–1,500 in-scope jobs | Summary | Target math. The spike measures it. |
| A7 | fastembed worker RSS 150–400 MB | Pitfall 8 | OOM on 4 GB. Measure during the backfill. |
| A8 | anthropic SDK `max_retries`/`timeout` constructor args and error classes | Pattern 6 | Minor API drift. Verify in installed source at implementation. |
| A9 | SQLAlchemy can express `RETURNING old/new`; otherwise use `text()` | Pattern 3 | Implementation detail only |
| A10 | Lever `salaryRange.interval` string values | Salary | Wrong period mapping. Check on real data. |
| A11 | Adzuna's ToS permits a free, non-revenue portfolio app that publishes listings with attribution under "publishing Adzuna ad listings" | Source Facts | Access could be revoked. **User should confirm, or email Adzuna. Record in ADR 0001.** |
| A12 | HNSW index ≈ 20–30 MB at 10k rows | Pitfall 8 | Negligible |

## Open Questions (RESOLVED)

1. **What is the real Adzuna quota, and what is the India IT volume?**
   - What we know: the quota is not published and third-party numbers conflict. `count` will reveal the volume.
   - Recommendation: the first plan is the human checkpoint plus the spike. The ADR sets the target before the adapters are tuned.
   - RESOLVED: 02-01 Task 2 (owner sitting reads the Adzuna quota) and 02-01 Task 3 (live spike measures volume; ADR 0001 records quota, volume and the active-job target). 02-10 sizes the Adzuna budget from the ADR.
2. **Does Adzuna's free tier allow this app?**
   - What we know: "intended for evaluating", but "publishing Adzuna ad listings" is a permitted use, with attribution.
   - Recommendation: record the reading in the ADR. Consider a short email to Adzuna (owner action). Keep `purge-source` ready.
   - RESOLVED: 02-01 Task 2 (owner reads the terms) and 02-01 Task 3 (terms reading recorded in ADR 0001); `purge-source` is built in 02-11.
3. **Should ING-13 come into this phase?**
   - Recommendation: build the adapter interface so Greenhouse/Lever is a drop-in. Decide after the spike, per D-01. Plans should make the Greenhouse/Lever plan conditional, either as a separate wave or a plan that is skipped if the ADR says no.
   - RESOLVED: 02-01 Task 3 writes the `ING-13 decision:` line in ADR 0001; the gated plan 02-14 Task 1 Step 0 reads it and stops with a SUMMARY if the value is `not-needed`.
4. **Is `data analyst` / `system administrator` in scope?**
   - Recommendation: include data analyst (data) and exclude sysadmin/IT admin (D-05). Note it in `scope.yaml`.
   - RESOLVED: 02-04 Task 3 `scope.yaml` puts data analyst in scope (data family) and system administrator / IT admin out of scope, with labelled-title tests.

## Sources

### Primary (HIGH)
- Live `GET https://remotive.com/api/remote-jobs` (2026-11-21 17:41 UTC): counts, fields, legal notice.
- Live Greenhouse `boards-api.greenhouse.io/v1/boards/{groww,razorpay,postman}/jobs[?content=true]` and Lever `api.lever.co/v0/postings/{cred,meesho,paytm}?mode=json` (2026-11-21).
- Adzuna swagger `https://api.adzuna.com/v1/api-docs/adzuna` (parameters, Job model, 500-char truncation, redirect_url requirement); `https://api.adzuna.com/v1/doc/Search.md`.
- https://developer.adzuna.com/docs/terms_of_service: attribution, permitted use, free-tier purpose, termination.
- https://docs.greenhouse.io/job-board.html: endpoints, `content=true`, entity-escaped content, no auth for GET.
- https://github.com/lever/postings-api: endpoints, EU host, fields, workplaceType.
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs: `messages.parse`, `parsed_output`, schema limits, 24 h grammar cache.
- https://platform.claude.com/docs/en/build-with-claude/effort: `output_config.effort`; Haiku 5.5 default medium; `thinking: disabled` allowed at ≤ high.
- https://platform.claude.com/docs/en/about-claude/pricing: Haiku 5.5 $0.10/$0.50 (≤ 100k prompt), cache read $0.01, batch 50%.
- Throwaway `pgvector/pgvector:0.8.7-pg18-trixie` (PG 18.7) run: upsert `RETURNING old/new`, partial HNSW DDL, SKIP LOCKED claim, tsvector tokenisation, generated-column immutability error.
- Installed APScheduler 3.11.3 source: job defaults; AsyncIOExecutor shutdown cancels futures.
- PyPI JSON API (2026-11-21): versions listed in Standard Stack.
- GitHub Linguist LICENSE (MIT) and `languages.yml` aliases.

### Secondary (MEDIUM)
- https://www.onetcenter.org/license_db.html (via search): O*NET database CC BY 4.0 with versioned attribution.
- PostgreSQL 18 release notes (via search): OLD/NEW in RETURNING.

### Tertiary (LOW)
- jobspipe.dev and MCP wrapper pages: Adzuna ~1,000 calls/month, `results_per_page` max 50, `sort_by` values (the latter confirmed by swagger).
- Lightcast Open Skills licence ambiguity (search summary).

## Metadata

**Confidence breakdown:**
- Source facts: HIGH for Remotive, Greenhouse, Lever and Adzuna parameters/terms. LOW for the Adzuna quota.
- Architecture (upsert, queue, scheduler): HIGH. The key SQL was verified on PG18, and APScheduler was checked in source.
- LLM gateway: HIGH for API shape and prices; MEDIUM for SDK client details.
- Normalisation rules: MEDIUM. They're domain heuristics, to be validated by table tests and a sample audit.
- Memory budget: LOW-MEDIUM until measured.

**Research date:** 2026-11-21
**Valid until:** about 2026-12-21. Adzuna terms and quota, Remotive volume and Anthropic pricing change fastest.
