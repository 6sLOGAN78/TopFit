# Phase 2: Live Jobs Index - Context

**Gathered:** 2026-11-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Production holds a fresh, normalised, embedded index of real tech jobs. The sources are Remotive and Adzuna India, plus public Greenhouse/Lever boards if the ING-00 measurement shows a shortfall. A scheduler in exactly one worker process refreshes the index within source quotas, and a refresh never wipes it. This phase also delivers two pieces of infrastructure that every later phase uses: the LLM gateway (`ranker/llm/`) and the Postgres `tasks` queue (`SKIP LOCKED`).

Requirements: ING-00, ING-01, ING-02, ING-04, ING-05, ING-06, ING-07, ING-08, ING-09, ING-11, ING-12, NORM-01…NORM-07, SRCH-01, FND-06, FND-07, OPS-02. ING-13 (Greenhouse/Lever) is conditionally pulled in; see D-01.

Out of scope here: search endpoints and ranking (Phase 3+), HN ingestion (ING-03), cross-source dedupe beyond the simple rule (ING-10), and the UI for jobs.

</domain>

<decisions>
## Implementation Decisions

### Job target and sources
- **D-01: Conditional Greenhouse/Lever.** ING-00 runs first and measures real volume, quota and terms per source. If Remotive + Adzuna can't reach the target, **ING-13 (Greenhouse/Lever public boards) is pulled into this phase** as a third adapter. ADR `docs/decisions/0001-sources.md` records the measurement and the decision.
- **D-02: Target is ≥ 10k active tech jobs, with an honest floor.** If all sources together can't reach 10k, ADR 0001 records the real achievable number and the reason. Never pad the index with off-scope jobs to hit the number (see D-05/D-06).
- **D-03: Greenhouse/Lever boards are auto-discovered, with guardrails:**
  - Start from a seed list of about 30 known boards in config (Indian startups and remote-first product companies).
  - Add a discovered board slug (for example from job URLs seen in other sources) only if it has at least N in-scope tech jobs located in India or remote-open-to-India. N is a config value; Claude picks the default.
  - Log every board that was added or rejected, and make the list visible in a CLI report and in `ingest_runs` errors/notes.
  - Re-check discovered boards periodically and drop boards that go empty.
- **D-04: Adzuna credentials don't exist yet.** Phase 2 starts with a **human checkpoint**: sign up at developer.adzuna.com and put `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` into local `.env` and `/srv/ranker/.env` on the server. The ING-00 spike needs these keys. Code and tests must not need real keys (respx mocks).

### Which jobs count
- **D-05: Tech scope = engineering + data.** That means software/backend/frontend/full-stack, mobile, DevOps/SRE/platform, data engineering, data science/ML/AI, QA/SDET and security. It **excludes** product management, design, tech support and IT admin. Classify with rules on title and category; the planner/researcher picks the mechanism. Out-of-scope jobs are not stored (or are stored inactive and don't count toward the target; Claude's choice, documented).
- **D-06: Geography = India + remote.** That covers on-site/hybrid jobs in Indian cities plus remote jobs from anywhere. Every job gets `remote_eligibility` (worldwide / India / APAC / US-only / EU-only / other-region / unknown). Don't drop US/EU-only remote jobs at ingest; ranking filters them for India users later (NORM-03 default).
- **D-07: `remote_eligibility = unknown` is shown to India users, flagged.** It ranks below jobs confirmed as India, APAC or worldwide, and the UI later shows a "region not stated" badge. Phase 2 only stores the value correctly. Ranking and UI consume it in later phases, so capture this as a decision for them.
- **D-08: Region detection = rules first, LLM fallback.** Use source fields (e.g. Remotive `candidate_required_location`) and regex phrases ("US only", "must reside in", "EU timezone", "IST overlap", "anywhere"). Call the LLM (Haiku via the gateway, typed output) **only when the rules return unknown**, and cache by `content_hash`. The NORM-05 seniority/employment-type fallback uses the same pattern.

### LLM gateway, key and budget
- **D-09: There's no Anthropic API key yet.** Build and test the whole gateway against the **fake provider**. Getting a key (prepaid credit at console.anthropic.com, set as `ANTHROPIC_API_KEY` in `.env` and `/srv/ranker/.env`) is a **human checkpoint before the production backfill**. Without a key the system still runs fully: fallbacks are skipped and fields stay `unknown`.
- **D-10: The monthly LLM spend cap starts at $5.** It is env-configurable (e.g. `LLM_MONTHLY_BUDGET_USD=5`) and enforced in the gateway from `llm_calls` cost totals. Over budget, calls are skipped (not errored) and logged.
- **D-11: Degraded mode = save now, enrich later.** If the budget is exhausted or the API fails, the job is saved with the field set to `unknown`, and an enrichment task goes into the `tasks` table. The worker retries it when budget or the API is back (with backoff and a max-attempts limit). **Ingestion never blocks on the LLM.** This is the first real consumer of FND-07.

### Salary and currency
- **D-12: FX = fixed rates in config.** `config/fx.yaml` holds rates per currency to INR and an `as_of` date, updated by hand. Conversion is deterministic and testable, with no outside FX dependency. Store the rate date used, or make it derivable, so the UI can later say "converted at ₹X/$ as of <date>".
- **D-13: Adzuna predicted salaries are stored and shown as estimates.** They carry the flag `salary_is_estimate = true`. Later phases show them as "~₹X LPA (estimate)" and use them in salary filters only if the user allows estimates. Phase 2 stores the flag correctly.

### Source mix addendum (owner, 2026-11-22, during 02-01 checkpoint)
- **D-14: Use every source we can reach without logins or anti-bot bypass.** Owner overrode the "official APIs only" default for this non-commercial portfolio project. Sources, all measured by the ING-00 spike:
  - **Adzuna**: account is on "Pending activation: 25 test calls"; paid plans need a card. Use the test calls for the spike; keep the adapter (02-10) but budget from what the dashboard allows. No card is added by the executor.
  - **Greenhouse, Lever, Ashby** public board APIs (many company boards, India-heavy seed list). ING-13 is pulled in regardless of the volume math (D-01 overridden by D-14).
  - **Remotive**, **HN Who is hiring** (Algolia API), and free remote-job APIs: **Arbeitnow, Himalayas, Jobicy, RemoteOK** (attribution where required).
  - **Internshala** category listing pages (`/jobs/<category>-jobs`, `/internships/<category>-internship`, no query strings). robots.txt allows these and disallows search/detail pages, so only listing-card fields are used (title, company, location, salary, skills, posted age) and each card links out to the Internshala detail URL. 50 cards per page, verified 2026-11-22 with a plain request.
- **D-15: Scraper rules.** Honest User-Agent naming the project and site, obey robots.txt (checked at runtime), ≤ 1 request / 3 s per host, no login, no CAPTCHA or anti-bot bypass, no proxies or UA rotation, store only listing fields plus the source URL, attribute the source on every card, and keep `purge-source` working for each. Naukri, Indeed, LinkedIn and Wellfound get one plain probe in the spike; if blocked (or robots.txt disallows), record "blocked, not used" and move on.
- **D-16:** Adapters for Ashby, the remote-job APIs and Internshala go in a new plan after 02-14 (same SourceAdapter contract from 02-07).

### Claude's Discretion
- Exact schedule times (Remotive ≤ 4×/day per ING-01; Adzuna daily within the measured quota, with times in IST). Stagger sources so they don't overlap.
- How the admin ingest endpoint (ING-11) is protected before accounts exist (Phase 5). Expected: a static admin token from env via a header, constant-time compare, and disabled when unset.
- How the ≥ 400-skill taxonomy (`config/skills.yaml`) is built. Use permissively licensed or hand-curated sources, and record provenance. Handle C++, C#, Go, .NET and Node.js correctly (NORM-06).
- The default N for discovered-board relevance, the seed-board list, and the board re-check cadence.
- Tables and migration structure, following SPEC §8 (`jobs`, `companies`, `ingest_runs`, `llm_calls`, `tasks`, …), including the HNSW index DDL via `op.execute()`.
- Whether out-of-scope jobs are skipped or stored inactive (D-05).
- Expiry thresholds within ING-08 bounds (Adzuna 30–45 days), and how "normal volume" is computed for the 50% no-expiry guard.
- Dedupe: the simple rule is enough in this phase (same `dedupe_key` → keep newest), per SPEC's MVP note. Full ING-10 comes later.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product spec (source of truth)
- `SPEC.md` §4.1 (ING), §4.2 (NORM), §4.3 (EMB): requirement wording
- `SPEC.md` §6.2: component responsibilities (worker = scheduler + task consumer)
- `SPEC.md` §7.1–7.5: sources table, `config/sources.yaml` example, `SourceAdapter` interface, per-run pipeline steps, normalisation rules, dedupe
- `SPEC.md` §8: data model (jobs, companies, skills, ingest_runs, llm_calls, tasks) and HNSW/GIN indexes
- `SPEC.md` §11.5: LLM usage map (which model for which call)
- `SPEC.md` §14.5: LLM cost controls
- `SPEC.md` §15.3: `.env.example` configuration keys
- `.planning/REQUIREMENTS.md`: exact text for ING-00…13, NORM-01…07, SRCH-01, FND-06, FND-07, OPS-02
- `.planning/ROADMAP.md` (Phase 2 section): success criteria 1–5

### Stack and pitfalls (research-corrected)
- `CLAUDE.md`: stack pins and verdicts:
  - pgvector `hnsw.ef_search`
  - no `dict` in LLM output schemas
  - no `temperature` on Haiku 5.5
  - pass thinking blocks back unchanged
  - APScheduler `<4` in the worker only
  - fastembed has no query prefix and its model is baked in
  - pgvector-python 0.5 returns `list`
  - SQLAlchemy 2.1 `[asyncio]`
- `.planning/research/STACK.md`, `.planning/research/PITFALLS.md`, `.planning/research/ARCHITECTURE.md`: background research

### Phase 1 outputs (what exists)
- `.planning/phases/01-foundations-walking-skeleton-deploy/01-CONTEXT.md`: D-04 single origin, D-08 compose services (worker stub to grow into the scheduler), D-10 secrets in `/srv/ranker/.env`, D-22 log redaction keys
- `.planning/phases/01-foundations-walking-skeleton-deploy/01-01-SUMMARY.md`, `01-03-SUMMARY.md`, `01-04-SUMMARY.md`: backend layout, logging, env-example test (every settings field must be documented in `.env.example`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/ranker/embeddings/embedder.py`: fastembed wrapper (bge-small, 384-d, baked model, `EMBEDDER=fake` for tests). Reuse it for job embeddings and the SRCH-01 backfill CLI.
- `backend/ranker/db/engine.py`: async SQLAlchemy engine; lifespan puts it on `app.state.engine`.
- `backend/ranker/observability.py`: `configure_logging`, the `redact` processor and the request-ID middleware. The worker already calls `configure_logging`.
- `backend/ranker/worker/__main__.py`: stub worker with clean SIGTERM shutdown. Grow it into APScheduler (3.11, `<4`) plus the `tasks` consumer loop.
- Empty packages already exist for this phase: `backend/ranker/llm/`, `backend/ranker/tasks/`, `backend/ranker/services/`.
- `backend/migrations/` (Alembic; baseline `0001` creates the `vector` extension; `script_location = %(here)s/migrations`).

### Established Patterns
- Tests run against a real `pgvector/pgvector:0.8.7-pg18-trixie` via testcontainers. Pytest uses `filterwarnings=error`. There are no live network calls in the default run (use respx for source APIs), and LLM tests sit behind `@pytest.mark.llm`.
- ruff + pyright (strict on `ranker/`) gate CI. Every new setting must appear in `.env.example`, because `tests/test_env_example.py` enforces it.
- The CI workflow pins all actions to commit SHAs. The CD path deploys images and runs `alembic upgrade head`.

### Integration Points
- Production compose `worker` service (`deploy/compose.prod.yaml`) becomes the single scheduler process (OPS-02). Ingestion never runs in the API process.
- The admin ingest endpoint mounts under `/api/admin/...` on the existing FastAPI app.
- New env vars (Adzuna keys, Anthropic key, budget, admin token) go to `.env.example` and `/srv/ranker/.env` on the server.

</code_context>

<specifics>
## Specific Ideas

- The production VM is **GCP e2-medium in asia-south1 with DuckDNS** (the user changed this from DigitalOcean during Phase 1 go-live). Ingestion quota and memory budgets should assume 4 GB RAM shared by db, api, worker and web.
- The GitHub repo is `6sLOGAN78/TopFit` (public), and the product display name is **TopFit**. Package and image names stay `ranker`.
- Two human checkpoints in this phase: (1) Adzuna keys before ING-00, (2) Anthropic API key before the production LLM backfill. Batch them with any other owner actions.
- Attribution: every Remotive job must credit Remotive and link back (ING-01 terms). Source attribution appears on every job card later (CLAUDE.md data-access rule), so store the source and original URL.

</specifics>

<deferred>
## Deferred Ideas

- A live FX rate API: revisit only if fixed rates prove too stale.
- UI badge "region not stated" and "estimate" salary display belong to the search/feed UI phases (decisions D-07 and D-13 tell those phases what to do).
- HN "Who is hiring" ingestion (ING-03) and full cross-source dedupe (ING-10) stay in their roadmap phases.
- Product rename beyond display name (package, images, DB names stays `ranker`). Not planned.

</deferred>

---

*Phase: 02-live-jobs-index*
*Context gathered: 2026-11-21*
