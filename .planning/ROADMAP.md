# Roadmap: Ranker

## Overview

Ranker is built as a vertical MVP first, then completed to full v1. Phases 1–5 deliver every `[MVP]` requirement (the SPEC.md §18 ship-and-apply cut line, about 10–12 days) so the owner can apply to Elip with a live, deployed product. Phase 6 adds the remaining v1 work.

The order follows the research:

- Deploy a walking skeleton on the real domain on day 1.
- Run the source-viability spike before writing ingestion code.
- Build the LLM gateway and `tasks` queue once, early.
- Prove retrieval correctness and the eval harness before ranking.
- Build the `services/` layer and pure `rank()` before the agent.
- Start frontend work in parallel from the search phase onward.

SPEC.md §17 used phases 0–10. This roadmap compresses them into 6 coarse phases:

| This roadmap | SPEC/research phases |
|---|---|
| 1 | 0 |
| 2 | 1–2 |
| 3 | 3 + 7a |
| 4 | 4–5 + parts of 8 |
| 5 | 6, 7b, MVP parts of 8–10 |
| 6 | post-MVP v1 completion |

## Milestones

- **MVP (ship-and-apply):** Phases 1–5. All 88 `[MVP]` requirements, plus FND-07 (`tasks` queue). FND-07 is pulled forward on purpose because MVP memory extraction (MEM-02) depends on it.
- **v1 complete:** Phase 6. The remaining 25 non-MVP v1 requirements.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundations & Walking-Skeleton Deploy** - Pinned monorepo, CI, and a live API + pgvector on the production custom domain from day 1
- [ ] **Phase 2: Live Jobs Index** - Source spike, then scheduled, idempotent, normalised, embedded Remotive + Adzuna ingestion, plus the LLM gateway and tasks queue
- [ ] **Phase 3: Hybrid Search & Eval Baseline** - Correct lexical + vector + RRF retrieval with hard filters, guest search UI with chips, and a frozen-snapshot eval with a CI gate
- [ ] **Phase 4: Profiles, Ranking & Personalised Feed** - Resume → editable profile, services/ + pure rank(), cached LLM rerank with degraded mode, fit labels, save/skip, ablation, guest-session security
- [ ] **Phase 5: Grounded Agent, Memory & MVP Launch** - Streaming tool-calling chat with grounding and cross-session memory, profile page, accessibility polish, Sentry, README: MVP is live
- [ ] **Phase 6: v1 Completion** - HN + dedupe (+ Greenhouse/Lever if needed), Rocchio/MMR, accounts, full evals + /evals page, admin dashboard, production ops, real-user launch proof

## Phase Details

### Phase 1: Foundations & Walking-Skeleton Deploy

**Goal**: A pinned, CI-checked monorepo runs locally with one command, and a walking skeleton (API with the embedding model loaded + Postgres/pgvector) is live over HTTPS on the production custom domain
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: FND-01, FND-02, FND-03, FND-04, FND-05, OPS-01, OPS-04, SEC-07
**Success Criteria** (what must be TRUE):

  1. A developer runs `make dev` and gets db (`pgvector/pgvector` PG18, pgvector ≥ 0.8.3), api, worker and web running locally, with toolchain versions pinned as in FND-02 (Python 3.14, SQLAlchemy 2.1, TypeScript ~5.9.3, APScheduler < 4).
  2. `https://api.ranker.<tld>/readyz` on the production host reports DB reachable, pgvector version asserted ≥ 0.8.3, and the embedding model loaded from the image with no network download at startup. `/healthz` responds, and api memory (RSS) is measured and recorded.
  3. The Next.js shell on `ranker.<tld>` completes a cookie round-trip with `api.ranker.<tld>` (same registrable domain, HTTPS).
  4. Every pull request shows passing ruff, pyright, pytest (testcontainers on the same pgvector image) and Docker build checks in GitHub Actions.
  5. API logs are JSON with request IDs. A test proves resume text, message content and email fields never appear in logs, and `.env.example` documents every secret.

**Plans:** 5/7 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Backend API slice: pinned uv package, /api/healthz, /api/readyz (pgvector extversion + 384-d probe), signed cookie ping, baseline migration, stub worker (wave 1)
- [x] 01-05-PLAN.md — Production deploy artifacts: Caddyfile (single origin), compose.prod.yaml, bootstrap/deploy/smoke/measure-memory scripts (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Web status page: pinned Next.js 16 + shadcn shell running health and cookie round-trip checks against the real API (wave 2)
- [x] 01-03-PLAN.md — SEC-07 logging: structlog JSON, request IDs, PII redaction proven on rendered output (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-04-PLAN.md — Images with baked offline model, `make dev` Compose stack, Makefile, documented .env.example, pre-commit, README (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 01-06-PLAN.md — GitHub Actions: CI gates, offline image smoke, GHCR publish, secrets-gated SSH deploy + HTTPS smoke (wave 4)

**Wave 5** *(blocked on Wave 4 completion)*

- [ ] 01-07-PLAN.md — Batched owner checkpoint (Student Pack, VM, DNS, repo, secrets), first deploy, memory/cold-start ADR, browser check (wave 5)

**Research**: Not needed (standard patterns). Confirm the VPS plan's RAM before provisioning.

### Phase 2: Live Jobs Index

**Goal**: Production holds a fresh, normalised, embedded index of real tech jobs from Remotive and Adzuna. A single worker refreshes it on a schedule, and a refresh never wipes it. The shared LLM gateway and `tasks` queue are in place for every later phase.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: ING-00, ING-01, ING-02, ING-04, ING-05, ING-06, ING-07, ING-08, ING-09, ING-11, ING-12, NORM-01, NORM-02, NORM-03, NORM-04, NORM-05, NORM-06, NORM-07, SRCH-01, FND-06, FND-07, OPS-02
**Success Criteria** (what must be TRUE):

  1. `docs/decisions/0001-sources.md` records the measured per-source volume, quota and terms, plus the resulting active-job target. It also states whether Greenhouse/Lever (ING-13) must be pulled forward into this phase.
  2. The production index holds the ING-00 target number of active tech jobs, each with a 384-d embedding and its model name. A scheduler running in exactly one worker process keeps it refreshed within source quotas, and every run appears in `ingest_runs` with fetched/inserted/updated/unchanged/expired/failed counts.
  3. An admin can trigger one source with `ranker ingest <source>` or the admin endpoint:
     - Re-running the same batch leaves the database unchanged, and unchanged jobs are not re-embedded.
     - 429/5xx responses are retried with backoff.
     - A run that fetches under 50% of its normal volume expires nothing.
     - Adzuna jobs expire by age; full-feed sources expire by missed complete runs.
  4. A sampled job shows:
     - a clean description
     - city/country/remote type with Indian aliases resolved (Bangalore → Bengaluru, Gurgaon → Gurugram)
     - `remote_eligibility`, so US-only remote is not a match for India users
     - INR-annual salary, with Adzuna predictions flagged as estimates
     - seniority, employment type and a normalised company
     - taxonomy skills, with C++, C#, Go, .NET and Node.js extracted correctly
  5. Every LLM call goes through `ranker/llm/`:
     - logged to `llm_calls`
     - budget-checked
     - typed outputs
     - runnable against the fake provider in tests

     Background jobs enqueued in `tasks` are picked up by the worker exactly once, using `SKIP LOCKED`.
**Plans**: TBD
**Research**: Yes. Run `/gsd:plan-phase --research-phase 2`: live Adzuna quota, `results_per_page`/`max_days_old`/`sort_by`, ToS on storing descriptions; Greenhouse/Lever board APIs as a fallback volume source.

### Phase 3: Hybrid Search & Eval Baseline

**Goal**: Any visitor can type a natural-language query on the landing page and quickly get relevant, filtered, fresh jobs. Retrieval quality is measured on a frozen eval set and protected by a CI gate.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: SRCH-02, SRCH-03, SRCH-04, SRCH-05, SRCH-06, SRCH-07, EVAL-01, EVAL-02, EVAL-03, EVAL-08, UI-01, UI-04, UI-05, UI-06, OPS-03, SEC-04
**Success Criteria** (what must be TRUE):

  1. A guest types "backend python bengaluru 10 LPA remote ok" on the landing page without signing up and sees:
     - results with editable filter chips showing how the query was parsed
     - "set aside" counts per filter
     - "relax filter" options when nothing matches
     - on every card: source attribution, the "posted Xd ago · checked Yh ago" line and a link to the original posting
  2. Retrieval is correct:
     - Queries containing C++, C#, Go or .NET match the right jobs.
     - Full-sentence queries return lexical hits rather than collapsing to zero.
     - A test asserts the vector leg returns the full requested K.
     - The exact-vs-HNSW choice is recorded in an ADR with benchmark numbers.
  3. Search p95 is under 300 ms at production index size (synthetic rows allowed if the real index is smaller). Every search is logged to `search_logs` with filters, per-feature scores, pipeline version and latency. A guest over 60 searches/hour gets a 429 in the standard error shape.
  4. One command runs the ranking eval on the frozen, deduplicated snapshot over 10–15 personas (human-reviewed grades, agreement reported) and reports nDCG@10, Recall@50, MRR and P@5 with bootstrap CIs. CI fails a PR that drops nDCG@10 by more than 0.02.
  5. Every production deploy runs Alembic migrations automatically, then a `/readyz` + one-search smoke test.

**Plans**: TBD
**UI hint**: yes
**Research**: Yes. Run `/gsd:plan-phase --research-phase 3`. This research is empirical: exact vs HNSW benchmark at ~50k rows, tsvector tokenisation check, k6 at 20 rps with 1 uvicorn worker. Plan explicit spike tasks.

### Phase 4: Profiles, Ranking & Personalised Feed

**Goal**: A visitor turns their resume into an editable profile and gets a personalised feed that is ranked and explained. Save/skip changes what they see next, and search keeps working when the LLM is down or over budget.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: SEC-01, SEC-03, SEC-05, SEC-06, PROF-01, PROF-02, PROF-03, PROF-04, PROF-06, FND-08, RANK-01, RANK-02, RANK-03, RANK-04, RANK-05, RANK-06, RANK-07, RANK-08, RANK-11, EVAL-04, UI-02, UI-03, UI-08
**Success Criteria** (what must be TRUE):

  1. Onboarding with a guest session:
     - A first-time visitor gets a guest session (signed httpOnly cookie, works in production).
     - They upload a PDF, DOCX or TXT resume, review and edit every extracted profile field, and set preferences.
     - Oversized, scanned or invalid files get a clear error.
     - The original file is never stored, and the extracted text can be deleted.
  2. The feed shows ranked cards with:
     - a Strong / Good / Stretch fit label (no percentages), reasons and missing skills
     - salary, location and remote eligibility, and source attribution
     - Save, Skip-with-reason and Open

     No single company floods the list, and the saved-jobs page lists everything the user saved.

  3. Personalisation:
     - Skipping a job as "too senior" or "wrong location" visibly changes the next feed.
     - Saved and skipped jobs do not reappear in the feed.
     - Disliked companies and excluded industries are pushed down.
  4. When the LLM fails, times out or is over budget, search and feed still return feature-ranked results flagged `degraded` with rule-based reasons and never a 5xx. At 80% of the monthly budget, guests lose rerank. Repeating an identical query reuses the rerank cache.
  5. One command produces the ablation table (lexical → vector → hybrid → features → tuned → + rerank → + personalisation) by calling the production `rank()` with different configs, with weights tuned under persona cross-validation. Delete-my-data leaves zero rows in every user-linked table. State-changing requests are CSRF-protected, with security headers and CORS restricted to the web origin.

**Plans**: TBD
**UI hint**: yes
**Research**: Yes. Run `/gsd:plan-phase --research-phase 4`: measure Haiku rerank latency/throughput with structured outputs (grammar compile cost, chunk sizing) before fixing the timeout. Decide on company industry/stage data (OPEN-4).

### Phase 5: Grounded Agent, Memory & MVP Launch

**Goal**: Users can chat with a grounded agent that finds and explains real jobs and remembers their preferences across sessions. The MVP is publicly launched with monitoring and a README.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05, AGENT-06, AGENT-08, AGENT-09, MEM-01, MEM-02, MEM-03, MEM-05, MEM-06, EVAL-06, UI-07, UI-09, UI-12, UI-13, OPS-05, LNCH-01
**Success Criteria** (what must be TRUE):

  1. A user types "backend roles in Bengaluru, Python, at least ₹10 LPA, remote is fine" on the chat page and sees:
     - streaming tokens, tool status and job cards side by side (stacked on mobile), streaming unbuffered through the production proxy
     - stated preferences applied and briefly confirmed
     - at most one clarifying question, asked only when role or location is unknown
  2. Every job the agent mentions comes from its tool results. The scenario eval (10 scenarios, including prompt injection hidden in job text) shows zero ungrounded job references, 100% injection resistance, and a reported faithfulness score.
  3. A preference stated in one conversation (e.g. "no early-stage startups") changes results in a later conversation. The user can see that memory on the profile page, along with editable preferences and delete-all-data. Deleting the memory reverts its effect on preferences.
  4. When the LLM is down or the monthly budget is exhausted, chat shows a clear message and offers plain search instead of failing.
  5. Launch readiness:
     - Every page works at 360 px with no horizontal scroll, is keyboard-accessible, meets AA contrast and supports light/dark mode.
     - Loading, empty, error, degraded and LLM-unavailable states are all designed.
     - Production errors reach Sentry with PII scrubbed, and an uptime monitor pings `/healthz`.
     - The README shows the live link, architecture diagram, ablation table, eval method and honest notes on missed targets.

**Plans**: TBD
**UI hint**: yes
**Research**: Yes. Run `/gsd:plan-phase --research-phase 5`: Anthropic streaming + strict tool use + `between_tools` thinking + prompt caching in one request; verify exact request shapes against live docs.

### Phase 6: v1 Completion

**Goal**: Ranker reaches full v1:

- a richer, deduplicated index
- learning personalisation (Rocchio feedback, MMR diversity)
- accounts and the richer agent/UI features
- full evals published publicly
- production-grade operations
- proof of real usage

**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: ING-03, ING-10, ING-13, SRCH-08, PROF-05, RANK-09, RANK-10, AGENT-07, MEM-04, SEC-02, SEC-08, UI-10, UI-11, UI-14, UI-15, EVAL-05, EVAL-07, EVAL-09, EVAL-10, OPS-06, OPS-07, OPS-08, OPS-09, LNCH-02, LNCH-03
**Success Criteria** (what must be TRUE):

  1. The index includes jobs extracted from the monthly HN "Who is hiring" thread, plus Greenhouse/Lever boards. Cross-source duplicates show once, junior and senior roles at one company stay separate, and a duplicate is promoted when its canonical job expires.
  2. Accounts and richer features:
     - A user can register with email + password and keep their guest data.
     - A user can build a profile through chat without a resume.
     - A user can compare 2–3 jobs in chat and page through results.
     - A user can open a job detail page with the full description and a match breakdown, and read `/about` and `/privacy`.
  3. Saves and skips shift future results through the feedback vector. Results show diversity beyond the company cap. Contradicting memories supersede older ones, which are kept for audit.
  4. A public `/evals` page shows results per git SHA:
     - ≥ 30 personas
     - ≥ 25 agent scenarios
     - an LLM judge calibrated against ≥ 50 human labels, with agreement reported
     - resume-parse skills F1 and years-of-experience accuracy on ≥ 15 gold resumes
  5. Operations and launch proof:
     - The admin dashboard shows ingest runs, index size by source, online metrics from batched impressions (save rate, CTR@10, skip rate) and LLM cost per day.
     - `/metrics` is exposed.
     - Daily backups run, and the restore is documented in the runbook.
     - `search_logs` retention stays within the storage budget.
     - k6 shows 20 rps with p95 < 300 ms.
     - CI runs `pip-audit` and `npm audit`.
     - ≥ 20 real users and ≥ 100 logged interactions within 2 weeks of launch, a 2-minute demo video, and 3–5 ADRs.

**Plans**: TBD
**UI hint**: yes
**Research**: Optional. Greenhouse/Lever board APIs only if not already covered in Phase 2. Everything else uses standard patterns.

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6. Frontend work in Phases 3–5 can overlap with backend work once the search endpoint exists.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundations & Walking-Skeleton Deploy | 0/7 | Planned | - |
| 2. Live Jobs Index | 0/TBD | Not started | - |
| 3. Hybrid Search & Eval Baseline | 0/TBD | Not started | - |
| 4. Profiles, Ranking & Personalised Feed | 0/TBD | Not started | - |
| 5. Grounded Agent, Memory & MVP Launch | 0/TBD | Not started | - |
| 6. v1 Completion | 0/TBD | Not started | - |
