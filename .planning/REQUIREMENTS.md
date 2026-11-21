# Requirements: Ranker

**Defined:** 2026-11-21
**Core Value:** A user describes what they want (by resume or conversation) and gets a short list of real, currently open jobs ranked well for them, with honest reasons. Hybrid search and ranking over a fresh index must work even when the LLM is unavailable.

**Sources:** `SPEC.md` §4 (original requirement IDs kept where possible) plus corrections from `.planning/research/` (STACK, FEATURES, ARCHITECTURE, PITFALLS, SUMMARY). Where research contradicts SPEC.md, the requirement below wins and SPEC.md should be read with the research corrections in mind.

**MVP marker:** `[MVP]` = inside the SPEC.md §18 ship-and-apply cut line. Everything else in v1 follows right after the MVP is live.

## v1 Requirements

### Foundations (FND)

- [ ] **FND-01** [MVP]: Developer can start db, api, worker and web locally with one command (`make dev`, Docker Compose, `pgvector/pgvector` PG18 image, pgvector ≥ 0.8.3)
- [ ] **FND-02** [MVP]: Toolchain is pinned: Python 3.14 + uv, FastAPI, Pydantic v2, SQLAlchemy 2.1 (`[asyncio]`), Alembic, psycopg 3; Next.js App Router + TypeScript 5.9.x (not 7.x), Tailwind, shadcn/ui; APScheduler < 4
- [ ] **FND-03** [MVP]: Every PR runs lint (ruff), type-check (pyright), tests and Docker build in GitHub Actions
- [ ] **FND-04** [MVP]: A walking skeleton (API with the embedding model loaded + Postgres with pgvector) is deployed to the target host in the first phase, proving memory fit, pgvector version and cold-start behaviour before feature work
- [ ] **FND-05** [MVP]: The embedding model is baked into the Docker image (`FASTEMBED_CACHE_PATH`), so startup needs no network download
- [ ] **FND-06** [MVP]: All LLM calls go through one gateway (`ranker/llm/`) with timeout, retry, per-call logging to `llm_calls`, monthly budget check, a fake provider for tests, typed structured outputs (no free-form `dict` fields) and correct pass-through of thinking/tool content blocks
- [ ] **FND-07**: Async background work (memory extraction, feedback-vector updates, embedding backfill) runs through a Postgres `tasks` table using `SKIP LOCKED`, processed by the worker
- [ ] **FND-08** [MVP]: Route handlers and agent tools both call one `services/` layer; ranking exposes a DB-free `rank(candidates, user, config)` function used by production, evals and weight tuning alike

### Ingestion (ING)

- [ ] **ING-00** [MVP]: Before ingestion code is written, real per-source volumes, quotas and terms are measured and recorded (Remotive returned 16 jobs on 2026-11-21; Adzuna free quota ≈ 250 calls/day unconfirmed), and the source mix is chosen to reach the job-count target
- [ ] **ING-01** [MVP]: System ingests Remotive jobs at most 4 times/day, honouring its terms (attribution, no signup wall in front of its jobs)
- [ ] **ING-02** [MVP]: System ingests Adzuna India jobs within the measured daily quota (search terms × cities × pages budgeted in `config/sources.yaml`)
- [ ] **ING-03**: System ingests the monthly HN "Who is hiring" thread and turns top-level comments into structured job records via the LLM gateway, cached by comment ID and hash
- [ ] **ING-04** [MVP]: Each source is a plugin implementing one `SourceAdapter` interface; adding a source touches only `ingest/sources/`
- [ ] **ING-05** [MVP]: Ingestion is idempotent: re-running a batch leaves the database unchanged (upsert on `source, source_job_id`; `content_hash` detects changes)
- [ ] **ING-06** [MVP]: Every run is recorded in `ingest_runs` with fetched / inserted / updated / unchanged / expired / failed counts and errors
- [ ] **ING-07** [MVP]: Source calls retry with exponential backoff and jitter on 429/5xx (max 5 tries)
- [ ] **ING-08** [MVP]: Jobs expire by per-source rules: "not seen in 2 complete runs" only for sources fetched in full; age-based (30–45 days) for quota-capped sources like Adzuna; 45 days after thread date for HN. No expiry happens when a run fetched under 50% of its normal volume
- [ ] **ING-09** [MVP]: Embeddings and skills are recomputed only when `content_hash` changes
- [ ] **ING-10**: Cross-source duplicates collapse to one canonical job; the dedupe key keeps seniority (junior and senior roles at the same company stay separate); when a canonical job expires, the next duplicate is promoted
- [ ] **ING-11** [MVP]: Admin can trigger one source from the CLI (`ranker ingest <source>`) or an admin endpoint
- [ ] **ING-12** [MVP]: Production index holds a target number of active tech jobs set in ING-00 (aim ≥ 10k; record the real achievable number if the sources can't reach it)
- [ ] **ING-13**: System ingests public Greenhouse/Lever job boards for a curated company list (pulled into v1 if ING-00 shows the job-count target can't be reached otherwise)

### Normalisation (NORM)

- [ ] **NORM-01** [MVP]: Descriptions are stripped to clean text (`description_clean`), keeping the raw version; job HTML is never rendered raw in the UI
- [ ] **NORM-02** [MVP]: Location is normalised to city, country and remote type, with Indian aliases (Bangalore → Bengaluru, Gurgaon → Gurugram, NCR → Delhi/Gurugram/Noida)
- [ ] **NORM-03** [MVP]: Each job has `remote_eligibility` (regions it is open to, e.g. worldwide / India / APAC / US-only / unknown); US- or EU-only remote jobs do not count as remote matches for an India-based user by default
- [ ] **NORM-04** [MVP]: Salary is parsed (₹12–18 LPA, lakh, $120k–150k, monthly) into min/max/currency/period and INR annual; Adzuna predicted salaries are flagged and treated as estimates
- [ ] **NORM-05** [MVP]: Seniority and employment type are inferred by rules, with LLM fallback only when rules return unknown
- [ ] **NORM-06** [MVP]: Skills are extracted against a curated taxonomy (≥ 400 canonical skills with aliases), handling C++, C#, Go, .NET and Node.js correctly
- [ ] **NORM-07** [MVP]: Company names are normalised into a `companies` table

### Search (SRCH)

- [ ] **SRCH-01** [MVP]: Jobs are embedded locally with `BAAI/bge-small-en-v1.5` (384d, fastembed) and stored in pgvector with the embedding model name per row; backfill CLI exists
- [ ] **SRCH-02** [MVP]: Lexical search uses Postgres full-text with OR-style matching for natural-language queries (not all-terms-must-match) and a `simple`-config skills vector so tech terms survive tokenisation
- [ ] **SRCH-03** [MVP]: Vector search reliably returns the requested top-K: exact search or HNSW with `ef_search` ≥ K and iterative scans, chosen by a recorded benchmark; legs query `jobs` directly so planned indexes are used (verified with `EXPLAIN ANALYZE`) and a test asserts full row counts
- [ ] **SRCH-04** [MVP]: Lexical and vector candidates merge with Reciprocal Rank Fusion (k = 60)
- [ ] **SRCH-05** [MVP]: Hard filters (active, remote type and eligibility, city, employment type, seniority, posted within N days, min salary with unknown salary kept but penalised) apply inside retrieval
- [ ] **SRCH-06** [MVP]: Guest user can search and get results with p95 < 300 ms (no LLM rerank) at production index size; free-text queries are parsed by rules first, LLM parse only as a cached fallback
- [ ] **SRCH-07** [MVP]: Every search is logged to `search_logs` (query, filters, results with per-feature scores, pipeline version, latency, degraded flag)
- [ ] **SRCH-08**: Results paginate with cursors

### Profiles (PROF)

- [ ] **PROF-01** [MVP]: User can upload a resume (PDF via pypdf, DOCX, TXT; ≤ 5 MB, ≤ 10 pages), validated by magic bytes and parsed in a subprocess with a timeout
- [ ] **PROF-02** [MVP]: Resume is parsed into a typed `CandidateProfile` via structured output; on validation failure, one retry with the error, then a clear message
- [ ] **PROF-03** [MVP]: User can review and edit every extracted profile field before saving
- [ ] **PROF-04** [MVP]: Profile embedding is computed from headline, skills and recent roles
- [ ] **PROF-05**: User without a resume can build a profile through chat
- [ ] **PROF-06** [MVP]: Original resume files are never stored; only extracted text, which the user can delete

### Ranking (RANK)

- [ ] **RANK-01** [MVP]: Results go through retrieval (top 100) → feature scoring (top 50) → LLM rerank (top 20) → per-company cap → final top N; skipped and saved jobs are removed before rerank
- [ ] **RANK-02** [MVP]: Feature score is a weighted sum of semantic, lexical, skill, role, seniority, location, salary, recency, preference and feedback features, with weights in versioned `config/ranking.yaml`
- [ ] **RANK-03** [MVP]: LLM rerank scores all top-20 candidates in one fast call (relevance 0–3 only); reasons, missing skills and concerns are generated for the top few or on demand; blending for ranks beyond the reranked set is defined
- [ ] **RANK-04** [MVP]: Rerank results are cached by profile hash + query/filters hash + job content hash + prompt version
- [ ] **RANK-05** [MVP]: If rerank fails, times out or the budget is exhausted, results return feature-ranked with `degraded: true`; search never 5xxs because of the LLM
- [ ] **RANK-06** [MVP]: Each result shows a fit label (strong fit / good fit / stretch) derived from rerank relevance or feature score, not a fake match percentage
- [ ] **RANK-07** [MVP]: Structured preferences and typed memory effects apply boosts/penalties (excluded industries, avoided company stages, disliked companies)
- [ ] **RANK-08** [MVP]: User can skip a job with a reason (too senior, wrong location, wrong stack, job closed, not interested); reasons write preference updates through the same merge function the agent uses
- [ ] **RANK-09**: Saves and skips update a Rocchio feedback vector asynchronously (via FND-07), without double-counting feedback in both retrieval and scoring
- [ ] **RANK-10**: MMR diversity re-ordering (λ = 0.8) on top of the per-company cap
- [ ] **RANK-11** [MVP]: Weight-tuning script searches feature weights on the eval set with persona cross-validation and writes the best config with its metrics

### Agent (AGENT)

- [ ] **AGENT-01** [MVP]: User can chat; the agent uses tools (`search_jobs`, `get_job_details`, `update_preferences`, `save_job`) for at most 6 rounds per turn
- [ ] **AGENT-02** [MVP]: Replies stream over SSE (FastAPI native) with token, tool-status, jobs, done and error events; the web client reads them via `fetch` + `eventsource-parser`; proxies/compression don't buffer the stream
- [ ] **AGENT-03** [MVP]: Agent can only reference jobs from tool results: job cards are built from tool results, the model refers to jobs via `[[job:ID]]` markers filtered while streaming, and a post-hoc audit counts violations
- [ ] **AGENT-04** [MVP]: Agent asks at most one clarifying question, only when role or location/remote preference is completely unknown
- [ ] **AGENT-05** [MVP]: Job text is wrapped as untrusted data; no tool fetches URLs or acts externally; tools take `user_id` from the session, never from model arguments
- [ ] **AGENT-06** [MVP]: Stated preferences become `update_preferences` calls with salary units normalised (LPA → INR annual); the agent confirms briefly what changed
- [ ] **AGENT-07**: User can ask the agent to compare 2–3 jobs side by side (`compare_jobs`), get saved jobs and get recommendations through tools
- [ ] **AGENT-08** [MVP]: When the LLM is down or over budget, chat shows a clear message and offers plain search
- [ ] **AGENT-09** [MVP]: Agent system prompt and tool definitions use prompt caching to cut cost and latency

### Memory (MEM)

- [ ] **MEM-01** [MVP]: Recent messages within a 6k-token budget are kept; older turns compress into a running summary
- [ ] **MEM-02** [MVP]: After each user turn, a fast model extracts memories (typed kinds and typed effects) plus a preference patch, stored with a link to the source message
- [ ] **MEM-03** [MVP]: Up to 30 active memories and structured preferences are injected into the agent context
- [ ] **MEM-04**: Contradicting memories supersede older ones (kept for audit with `superseded_by`)
- [ ] **MEM-05** [MVP]: User can view and delete memories; deleting a memory also reverts the preference effect it created
- [ ] **MEM-06** [MVP]: A preference stated in one conversation changes results in a later conversation

### Evaluation (EVAL)

- [ ] **EVAL-01** [MVP]: A deduplicated, frozen job-pool snapshot (with precomputed embeddings, kept out of the public repo if source terms require) loads into an eval database
- [ ] **EVAL-02** [MVP]: Personas (10–15 for MVP, ≥ 30 for v1) have pooled candidates graded 0–3, with human review of every grade for a subset and agreement reported
- [ ] **EVAL-03** [MVP]: Ranking eval reports nDCG@10, Recall@50, MRR and P@5 with bootstrap confidence intervals
- [ ] **EVAL-04** [MVP]: One command produces the ablation table (lexical → vector → hybrid → features → tuned → + rerank → + personalisation), using a different model family or human labels to avoid the reranker grading itself
- [ ] **EVAL-05**: Resume-parse eval on ≥ 15 gold resumes reports skills F1 and years-of-experience accuracy
- [ ] **EVAL-06** [MVP]: Agent scenario eval (10 for MVP, ≥ 25 for v1) checks tool choice, filter extraction, no ungrounded jobs and 100% prompt-injection resistance, plus an LLM judge for faithfulness
- [ ] **EVAL-07**: Judge scores are calibrated against ≥ 50 human labels and agreement is reported
- [ ] **EVAL-08** [MVP]: CI runs the no-LLM ranking eval on the frozen snapshot using exact (deterministic) vector search and fails if nDCG@10 drops > 0.02 from baseline
- [ ] **EVAL-09**: Eval results are stored per git SHA and shown on a public `/evals` page
- [ ] **EVAL-10**: Online metrics (save rate, CTR@10, skip rate, searches per session) are computed from logs and shown on the admin dashboard

### Frontend (UI)

- [ ] **UI-01** [MVP]: Guest can search from the landing page without signing up
- [ ] **UI-02** [MVP]: User can onboard: upload resume → review/edit profile → set preferences
- [ ] **UI-03** [MVP]: Feed shows ranked job cards with fit label, reasons, missing skills, salary, location and eligibility, source attribution, and Save / Skip-with-reason / Open
- [ ] **UI-04** [MVP]: Each card shows freshness ("posted Xd ago · checked Yh ago") and links to the original posting
- [ ] **UI-05** [MVP]: Search and chat show editable filter chips reflecting how the query was parsed; empty results offer "relax filter" options
- [ ] **UI-06** [MVP]: Results show "set aside" counts (how many jobs each filter removed)
- [ ] **UI-07** [MVP]: Chat page shows streaming replies, tool status and job cards side by side (stacked on mobile)
- [ ] **UI-08** [MVP]: User can view saved jobs
- [ ] **UI-09** [MVP]: Minimal profile page lets the user edit preferences, view/delete memories and delete all their data
- [ ] **UI-10**: Job detail page shows the full description and a match breakdown of feature contributions
- [ ] **UI-11**: `/about` explains how ranking works; `/privacy` explains what is stored and how to delete it
- [ ] **UI-12** [MVP]: Pages work at 360 px width with no horizontal scroll, are keyboard-accessible, meet WCAG AA contrast and support light/dark mode
- [ ] **UI-13** [MVP]: Every loading, empty, error, degraded and LLM-unavailable state is designed
- [ ] **UI-14**: Admin dashboard shows ingest runs, index size by source, online metrics and LLM cost per day
- [ ] **UI-15**: Impressions are tracked (batched) when cards become visible

### Accounts and Security (SEC)

- [ ] **SEC-01** [MVP]: First visit creates a guest user with a signed httpOnly session cookie; web and API share one registrable custom domain so the cookie works in production
- [ ] **SEC-02**: User can register and log in with email + password (argon2id); registering keeps the guest's data
- [ ] **SEC-03** [MVP]: State-changing requests are CSRF-protected; security headers (CSP, HSTS, etc.) are set; CORS allows only the web origin
- [ ] **SEC-04** [MVP]: Rate limits (guest: 20 chat messages/day, 60 searches/hour; signed-in: 100/day, 300/hour) are enforced with a Postgres-backed counter and return 429 in the standard error shape
- [ ] **SEC-05** [MVP]: User can delete all their data; a test verifies zero rows remain in every user-linked table
- [ ] **SEC-06** [MVP]: Monthly LLM budget cap: at 80% rerank is disabled for guests; at 100% chat pauses and search runs degraded
- [ ] **SEC-07** [MVP]: No PII or resume text appears in logs, error tracking or `llm_calls`; secrets live only in env vars with `.env.example` documenting each
- [ ] **SEC-08**: CI runs `pip-audit` and `npm audit`

### Operations (OPS)

- [ ] **OPS-01** [MVP]: Production runs on a public HTTPS custom domain (default plan: one small VPS running Postgres, api, worker and Caddy via Docker Compose, web on Vercel or the same host; fallback Neon + Railway)
- [ ] **OPS-02** [MVP]: Scheduled ingestion runs in exactly one worker process (never inside multi-worker API processes or on a host that sleeps)
- [ ] **OPS-03** [MVP]: Alembic migrations run automatically on deploy; deploy runs a `/readyz` + one-search smoke test
- [ ] **OPS-04** [MVP]: `/healthz` and `/readyz` (DB reachable, model loaded) exist; JSON logs carry request IDs
- [ ] **OPS-05** [MVP]: Errors go to Sentry (or equivalent); an uptime monitor pings `/healthz`
- [ ] **OPS-06**: Prometheus `/metrics` exposes request latency, LLM latency/tokens and ingest counts (multiprocess-safe)
- [ ] **OPS-07**: Daily database backups run and a restore procedure is documented in `docs/runbook.md`
- [ ] **OPS-08**: Log retention keeps `search_logs` within the database storage budget
- [ ] **OPS-09**: Load test shows `/jobs/search` sustaining 20 rps with p95 < 300 ms

### Launch (LNCH)

- [ ] **LNCH-01** [MVP]: README has the live link, architecture diagram, ablation table, eval method and honest notes on missed targets
- [ ] **LNCH-02**: At least 20 real users and 100 logged interactions within 2 weeks of launch
- [ ] **LNCH-03**: A 2-minute demo video and 3–5 ADRs in `docs/decisions/` are published

## v2 Requirements

Deferred. Tracked but not in the current roadmap.

### Sources and Enrichment
- **V2-ING-01**: LLM skill extraction for jobs with < 3 taxonomy skills
- **V2-EMB-01**: Hosted embedding provider (e.g. Voyage) compared in evals
- **V2-SRCH-01**: True BM25 via ParadeDB `pg_search` compared against `ts_rank_cd`

### Ranking
- **V2-RANK-01**: Learned ranker (LightGBM LambdaMART) trained on interaction logs
- **V2-RANK-02**: Local cross-encoder reranker as a cheaper alternative to LLM rerank
- **V2-EVAL-01**: Interleaving A/B test between ranking configs

### Engagement and Accounts
- **V2-AGENT-01**: Daily email digest of new matches
- **V2-AUTH-01**: Google OAuth sign-in

## Out of Scope

| Feature | Reason |
|---------|--------|
| Auto-apply / emailing recruiters | Risky, harms users and employers, not needed to show matching skill |
| Resume tailoring and cover letters | Separate product; would consume the timeline |
| Precise match percentages | False precision; fit labels instead |
| Interview prep / general career coaching | Off-core; dilutes the matching story |
| Scraping LinkedIn, Naukri, Indeed | Terms forbid it; official APIs and public data only |
| Signup wall before results | Kills guest conversion and breaks Remotive's terms |
| Infinite feeds | Encourages low-quality scrolling; capped, ranked lists instead |
| Recruiter/employer features | Different product |
| Native mobile apps | Responsive web covers phones |
| Training custom deep-learning models | Pre-trained embeddings + hosted LLMs suffice |
| Multi-language support | English only for v1 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FND-01 | Phase 1 | Pending |
| FND-02 | Phase 1 | Pending |
| FND-03 | Phase 1 | Pending |
| FND-04 | Phase 1 | Pending |
| FND-05 | Phase 1 | Pending |
| FND-06 | Phase 2 | Pending |
| FND-07 | Phase 2 | Pending |
| FND-08 | Phase 4 | Pending |
| ING-00 | Phase 2 | Pending |
| ING-01 | Phase 2 | Pending |
| ING-02 | Phase 2 | Pending |
| ING-03 | Phase 6 | Pending |
| ING-04 | Phase 2 | Pending |
| ING-05 | Phase 2 | Pending |
| ING-06 | Phase 2 | Pending |
| ING-07 | Phase 2 | Pending |
| ING-08 | Phase 2 | Pending |
| ING-09 | Phase 2 | Pending |
| ING-10 | Phase 6 | Pending |
| ING-11 | Phase 2 | Pending |
| ING-12 | Phase 2 | Pending |
| ING-13 | Phase 6 | Pending |
| NORM-01 | Phase 2 | Pending |
| NORM-02 | Phase 2 | Pending |
| NORM-03 | Phase 2 | Pending |
| NORM-04 | Phase 2 | Pending |
| NORM-05 | Phase 2 | Pending |
| NORM-06 | Phase 2 | Pending |
| NORM-07 | Phase 2 | Pending |
| SRCH-01 | Phase 2 | Pending |
| SRCH-02 | Phase 3 | Pending |
| SRCH-03 | Phase 3 | Pending |
| SRCH-04 | Phase 3 | Pending |
| SRCH-05 | Phase 3 | Pending |
| SRCH-06 | Phase 3 | Pending |
| SRCH-07 | Phase 3 | Pending |
| SRCH-08 | Phase 6 | Pending |
| PROF-01 | Phase 4 | Pending |
| PROF-02 | Phase 4 | Pending |
| PROF-03 | Phase 4 | Pending |
| PROF-04 | Phase 4 | Pending |
| PROF-05 | Phase 6 | Pending |
| PROF-06 | Phase 4 | Pending |
| RANK-01 | Phase 4 | Pending |
| RANK-02 | Phase 4 | Pending |
| RANK-03 | Phase 4 | Pending |
| RANK-04 | Phase 4 | Pending |
| RANK-05 | Phase 4 | Pending |
| RANK-06 | Phase 4 | Pending |
| RANK-07 | Phase 4 | Pending |
| RANK-08 | Phase 4 | Pending |
| RANK-09 | Phase 6 | Pending |
| RANK-10 | Phase 6 | Pending |
| RANK-11 | Phase 4 | Pending |
| AGENT-01 | Phase 5 | Pending |
| AGENT-02 | Phase 5 | Pending |
| AGENT-03 | Phase 5 | Pending |
| AGENT-04 | Phase 5 | Pending |
| AGENT-05 | Phase 5 | Pending |
| AGENT-06 | Phase 5 | Pending |
| AGENT-07 | Phase 6 | Pending |
| AGENT-08 | Phase 5 | Pending |
| AGENT-09 | Phase 5 | Pending |
| MEM-01 | Phase 5 | Pending |
| MEM-02 | Phase 5 | Pending |
| MEM-03 | Phase 5 | Pending |
| MEM-04 | Phase 6 | Pending |
| MEM-05 | Phase 5 | Pending |
| MEM-06 | Phase 5 | Pending |
| EVAL-01 | Phase 3 | Pending |
| EVAL-02 | Phase 3 | Pending |
| EVAL-03 | Phase 3 | Pending |
| EVAL-04 | Phase 4 | Pending |
| EVAL-05 | Phase 6 | Pending |
| EVAL-06 | Phase 5 | Pending |
| EVAL-07 | Phase 6 | Pending |
| EVAL-08 | Phase 3 | Pending |
| EVAL-09 | Phase 6 | Pending |
| EVAL-10 | Phase 6 | Pending |
| UI-01 | Phase 3 | Pending |
| UI-02 | Phase 4 | Pending |
| UI-03 | Phase 4 | Pending |
| UI-04 | Phase 3 | Pending |
| UI-05 | Phase 3 | Pending |
| UI-06 | Phase 3 | Pending |
| UI-07 | Phase 5 | Pending |
| UI-08 | Phase 4 | Pending |
| UI-09 | Phase 5 | Pending |
| UI-10 | Phase 6 | Pending |
| UI-11 | Phase 6 | Pending |
| UI-12 | Phase 5 | Pending |
| UI-13 | Phase 5 | Pending |
| UI-14 | Phase 6 | Pending |
| UI-15 | Phase 6 | Pending |
| SEC-01 | Phase 4 | Pending |
| SEC-02 | Phase 6 | Pending |
| SEC-03 | Phase 4 | Pending |
| SEC-04 | Phase 3 | Pending |
| SEC-05 | Phase 4 | Pending |
| SEC-06 | Phase 4 | Pending |
| SEC-07 | Phase 1 | Pending |
| SEC-08 | Phase 6 | Pending |
| OPS-01 | Phase 1 | Pending |
| OPS-02 | Phase 2 | Pending |
| OPS-03 | Phase 3 | Pending |
| OPS-04 | Phase 1 | Pending |
| OPS-05 | Phase 5 | Pending |
| OPS-06 | Phase 6 | Pending |
| OPS-07 | Phase 6 | Pending |
| OPS-08 | Phase 6 | Pending |
| OPS-09 | Phase 6 | Pending |
| LNCH-01 | Phase 5 | Pending |
| LNCH-02 | Phase 6 | Pending |
| LNCH-03 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 114 total (88 marked [MVP])
- Mapped to phases: 114 ✓
- Unmapped: 0
- MVP (Phases 1–5): 89 requirements = all 88 [MVP] + FND-07 (pulled forward; MEM-02 depends on it)
- v1 completion (Phase 6): 25 requirements (ING-13 may move to Phase 2 depending on the ING-00 spike)

---
*Requirements defined: 2026-11-21*
*Last updated: 2026-11-21 after roadmap creation (traceability filled)*
