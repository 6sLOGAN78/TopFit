# Ranker

## What This Is

Ranker is a web app where a job seeker uploads a resume or just chats ("backend roles in Bengaluru, Python, at least ₹10 LPA, remote is fine"). An LLM agent with tools searches a continuously refreshed jobs index, ranks the results for that person with a multi-stage retrieval and ranking pipeline, explains why each job matches, and remembers preferences across sessions. It is built as a portfolio project for the Elip (YC W25) SDE Intern role. It must be live, deployed, used by real people, and documented with honest evaluation numbers.

The full design is in `SPEC.md` at the repo root. That file is the source of truth for schemas, algorithms, API shapes and acceptance criteria. This document summarises it for planning.

## Core Value

**A user describes what they want (by resume or conversation) and gets a short list of real, currently open jobs ranked well for them, with honest reasons.** If chat, accounts, or polish fail, hybrid search and ranking over a fresh index must still work, including when the LLM is unavailable (degraded mode).

## Requirements

### Validated

(None yet — ship to validate)

### Active

Detailed, ID-tagged requirements live in `.planning/REQUIREMENTS.md` (sourced from SPEC.md §4). Summary:

- [ ] **Ingestion** — Scheduled, idempotent ingest from Remotive, Adzuna (India) and HN "Who is hiring" (LLM-extracted), with expiry, a zero-fetch guard and cross-source dedupe; ≥ 10k active jobs in production
- [ ] **Normalisation** — Clean descriptions; normalise location, remote type, salary (INR annual), seniority, company; skills from a ≥ 400-skill taxonomy
- [ ] **Embeddings and search** — Local `bge-small-en-v1.5` embeddings via fastembed in pgvector (HNSW); lexical + vector retrieval fused with RRF; hard filters; p95 < 300 ms
- [ ] **Profiles** — Resume (PDF/DOCX/TXT) → typed `CandidateProfile` via LLM structured output; editable; no resume files stored
- [ ] **Ranking and personalisation** — Feature scoring with tuned weights, cached LLM rerank of top 20 with degraded-mode fallback, Rocchio feedback from saves/skips, preference penalties, MMR diversity
- [ ] **Agent and memory** — Tool-calling agent streamed over SSE, grounding check (no invented jobs), prompt-injection defence, short-term summary and long-term extracted memories the user can view and delete
- [ ] **Evaluation** — Frozen job-pool snapshot, ≥ 30 labeled personas, nDCG@10/Recall@50/MRR/P@5, ablation table, resume-parse eval, ≥ 25 agent scenarios with LLM judge calibrated against humans, CI regression gate
- [ ] **Frontend** — Next.js: landing/search, onboarding, feed, chat, job detail with match breakdown, saved, profile/memory, evals/about/privacy, admin; responsive and accessible
- [ ] **Accounts and security** — Guest sessions, email+password (argon2id), CSRF, rate limits, account deletion, LLM monthly budget cap
- [ ] **Operations** — Docker Compose, CI/CD on GitHub Actions, production deploy with scheduler, structured logs, metrics, Sentry, backups, runbook
- [ ] **Launch** — ≥ 20 real users, ≥ 100 interactions, README with live link, architecture, ablation and eval results, 2-minute demo video

### Out of Scope

- Auto-applying to jobs or emailing recruiters — risky and not needed to show matching skill
- Scraping sites whose terms forbid it (LinkedIn, Naukri, Indeed) — legal risk; official APIs and public data only
- Recruiter/employer-side features — different product
- Native mobile apps — responsive web covers phones
- Training custom deep-learning models — pre-trained embeddings + hosted LLMs; learning limited to weight tuning and feedback
- Multi-language support — English only for v1
- v2 items (Greenhouse/Lever sources, LLM skill extraction, hosted embeddings comparison, ParadeDB BM25, LightGBM learned ranker, email digests, interleaving A/B, Google OAuth) — deferred until v1 is live

## Context

- **Why:** Application for Elip's SDE Intern role (YC W25, AI career agent; ₹75K/month, 2 months, Gurugram/Bengaluru or remote). They screen for LLM prompting, tool use and structured outputs; table and query design for a real system; and picking up tools from docs. Good-to-haves: end-to-end recommender/search, RAG or LLM evals, a live project with real users, Docker/cloud deploys. Ranker is designed to hit every one of these.
- **Timing matters:** The posting was about a week old on 2026-11-21. SPEC.md §18 defines an MVP cut line (~10–12 days) to ship and apply with first, then keep building.
- **Builder:** Solo student developer (Ayush Maurya), Linux, building with Claude Code + GSD.
- **Users:** Final-year students and early-career engineers in India looking for internships/jobs; casual guests who want results without signing up.
- **Data sources:** Remotive public API (remote jobs, attribution required), Adzuna API for India (free app_id/app_key, attribution required), HN Algolia API for monthly "Who is hiring" threads. Terms and rate limits must be checked at build time.

## Constraints

- **Tech stack**: Python 3.14 + uv, FastAPI (native SSE), Pydantic v2, SQLAlchemy 2.1 `[asyncio]` + Alembic, psycopg 3, PostgreSQL 18 + pgvector ≥ 0.8.3 (0.8.7), fastembed; Next.js (App Router) + TypeScript ~5.9.3 (not 7.x) + Tailwind 4 + shadcn/ui; APScheduler < 4 — SPEC.md §16 stack with research-corrected pins (see `.planning/research/STACK.md`)
- **Hosting**: custom domain required (platform subdomains break the SameSite session cookie); default plan is one small VPS running Postgres, api (1 uvicorn worker), worker and Caddy via Docker Compose, with web on Vercel; embedding model baked into the image
- **Data sources**: Remotive returned only 16 jobs on 2026-11-21 and Adzuna's free quota is ~250 calls/day (unconfirmed) — a source-viability spike (ING-00) decides the real source mix and job-count target before ingestion code
- **Single datastore**: Postgres + pgvector only, no separate vector DB or Redis — one system to run, shows SQL skill
- **Budget**: ≤ ₹2,500/month (~$30) total infra + LLM at launch; LLM spend capped in code (default $20/month) with graceful degradation
- **Timeline**: MVP deployed in ~10–12 days; full v1 in ~20–27 days
- **Performance**: search p95 < 300 ms without rerank, < 3 s with rerank; chat first token p95 < 2.5 s; 20 rps sustained on search
- **Reliability**: LLM failure must never break search; one source failing must not affect others or wipe the index
- **Privacy**: resume files never stored; no PII in logs or LLM call logs; full account deletion
- **Testing**: no live network or real LLM calls in the default test run; LLM tests behind `@pytest.mark.llm`
- **Data access**: official APIs and public data only, with source attribution on every job card
- **LLM provider**: all calls go through one `ranker/llm/` abstraction; default Anthropic Claude with model IDs from env

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Postgres + pgvector for everything | One system; filters and vectors in one query; shows schema/SQL skill | — Pending |
| Hybrid retrieval with RRF (k=60) | Vectors miss exact terms ("Kafka", "Go"); lexical misses paraphrase; RRF needs no score calibration | — Pending |
| Local embeddings: `BAAI/bge-small-en-v1.5` (384d) via fastembed | Free, fast, no network per query, small image | — Pending |
| LLM rerank only top 20, cached, 6 s timeout, degraded fallback | Quality where it matters with bounded cost/latency | — Pending |
| Weighted-sum ranker with tuned weights (learned ranker in v2) | Explainable, works with zero interaction data, easy to ablate | — Pending |
| Structured preferences + free-text memories | Ranking needs deterministic fields; conversation needs nuance | — Pending |
| SSE (not WebSockets) for chat | One-way streaming is enough; simpler infra | — Pending |
| Monorepo (`backend/`, `web/`, `evals/`, `config/`) | API and UI change together in one PR | — Pending |
| Ship MVP cut line first, then full v1 | Application window is short; a live product beats a perfect plan | — Pending |
| Open: hosting combo, citext vs lower(email), rate-limit store, company industry/stage data, local cross-encoder, domain | Decided in the phase that needs them (SPEC.md §21) | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-11-21 after initialization*
