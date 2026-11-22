# Phase 3: Hybrid Search & Eval Baseline - Context

**Gathered:** 2026-11-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Any visitor (guest, no signup) types a natural-language query on the landing page and gets relevant, filtered, fresh jobs from the Phase 2 index. Retrieval is hybrid: Postgres full-text plus pgvector, merged with RRF (k=60). Filters apply inside retrieval. Retrieval quality is measured on a frozen eval set, and a CI gate protects it. The phase also delivers:
- the typed API contract (OpenAPI → Zod + typed client, Scalar docs, Schemathesis)
- Postgres-backed rate limits
- `search_logs`
- deploy-time migrations + a one-search smoke test

Requirements: SRCH-02…07, EVAL-01, EVAL-02, EVAL-03, EVAL-08, UI-01, UI-04, UI-05, UI-06, OPS-03, SEC-04, OPS-10.

Out of scope here (later phases):
- resume/profile and personalised feed
- fit labels, LLM rerank and the ablation table (Phase 4)
- chat/agent and accounts (Phase 5)

</domain>

<decisions>
## Implementation Decisions

### Search page and job cards
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

### Query parsing and filter chips
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

### Ranking before profiles
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

### Eval set and grading
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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product spec and requirements
- `SPEC.md`: §4 (SRCH, EVAL, UI, SEC, OPS requirement wording), §8 / §8.1 (search SQL, `search_logs`, indexes), §10 (ranking and eval), §11.5 (LLM usage map), §14.4 (rate limits).
- `.planning/REQUIREMENTS.md`: exact text for SRCH-02…07, EVAL-01/02/03/08, UI-01/04/05/06, OPS-03, SEC-04, OPS-10.
- `.planning/ROADMAP.md` (Phase 3 section): success criteria 1–6 and the empirical research note (exact vs HNSW at ~50k rows, tsvector tokenisation check, k6 at 20 rps with 1 uvicorn worker).

### Stack pitfalls
- `CLAUDE.md`, verdicts #1 (`SET LOCAL hnsw.ef_search`, non-materialised CTE), #3 (no `dict` in LLM output schemas), #6 (TS 5.9.3), #8 (Postgres counter for rate limits); no `temperature` on Haiku.
- `.planning/research/STACK.md`, `.planning/research/PITFALLS.md`, `.planning/research/ARCHITECTURE.md`.

### Phase 2 outputs and decisions this phase builds on
- `.planning/phases/02-live-jobs-index/02-CONTEXT.md`:
  - D-05/D-06 (scope, geography)
  - D-07 (unknown region shown and flagged)
  - D-13 (salary estimates)
  - D-14/D-15 (sources and attribution)
  - D-18 (ghost signals)
  - D-19 (dealbreaker fields)
  - D-20 (eval protocol)
- `docs/decisions/0001-sources.md`, `docs/decisions/0002-remote-eligibility-and-scope.md`, `docs/decisions/0003-salary-normalisation.md`.
- `docs/evals/ingest-normalisation.md`: the labelling protocol to mirror for grades (labelled_by/reviewed, honest counts).
- `.planning/phases/02-live-jobs-index/02-17-SUMMARY.md`: eval CLI conventions (`ranker eval ...`).

### Jev (TypeSafe AI) background, unverified
The owner's notes cite Analytics Vidhya ("Jev explained"), MindStudio ("what is the Jev classifier model"), Mezha ("TypeSafe AI unveils Jev"), NewsBytes, Blockchain Council and Analytics India Magazine. Research must check API access, pricing, the output contract and version pinning against TypeSafe AI's own docs (D-07).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable assets
- `backend/ranker/llm/`, the gateway. Pieces to reuse:
  - `client.py`: provider dispatch via `settings.llm_provider`
  - `cost.py`: budget reservation under an advisory lock
  - `schemas.py`
  - `providers/anthropic.py`, `providers/openrouter.py`, `providers/fake.py`

  Add `providers/jev.py` the same way. The per-request provider choice (D-06) needs a small extension, because today the provider is chosen globally from settings.
- `backend/ranker/embeddings/embedder.py`: fastembed bge-small (384-d). Use it for query embedding. There is no query prefix (CLAUDE.md #11).
- `backend/ranker/evals/ingest_eval.py` and the `ranker eval` typer sub-app in `backend/ranker/cli.py`: extend them with `ranker eval ranking` and `ranker eval parse`.
- `backend/ranker/api/`: `health.py`, `admin.py`, `session.py`. Mount the search router under `/api`.
- `deploy/smoke.sh`: extend it with a one-search check (OPS-03).
- `web/`: Next 16 + Tailwind 4 + shadcn. Today there is only `page.tsx` and `status-panel.tsx`, so the search UI is new.

### Established patterns
- Testing:
  - Tests use testcontainers PG18 + pgvector 0.8.7.
  - respx for HTTP, and the fake LLM provider.
  - `filterwarnings=error`.
  - LLM tests sit behind `@pytest.mark.llm`.
- CI and settings:
  - ruff + pyright strict gate CI.
  - Every setting must be documented in `.env.example`.
  - CI actions are pinned to SHAs.
- Phase 2 job fields to read:
  - `remote_eligibility`, `remote_type`, `onsite_days_per_week`
  - `salary_*` plus `salary_is_estimate`
  - `ghost_reasons`, `first_seen_at` / `last_seen_at`
  - `seniority`, `employment_type`, `industry`
  - `source` plus the original URL

### Integration points
- `/api/search` on the existing FastAPI app.
- The landing page in `web/src/app/page.tsx`.
- The rate-limit table and `search_logs` come via new Alembic migrations.
- The CI workflow gets new steps: openapi staleness, the eval gate with the private snapshot token, and Schemathesis.

</code_context>

<specifics>
## Specific Ideas

- The example query from the roadmap is the demo case: "backend python bengaluru 10 LPA remote ok".
- The settings panel with the "Query parsing model: Claude Haiku / Jev" choice is the owner's explicit request. It shows the model choice to visitors and pairs with the D-17 measurement.
- Jev's role: it is a classifier and decision model, not a text generator. It can do multiple-choice decisions (parse slots, yes/no, labels). It cannot write "why this job" reasons or chat. Use it **alongside** Claude, never instead of it.
- Product display name is **TopFit**. Package and image names stay `ranker`.

</specifics>

<deferred>
## Deferred Ideas

- **Jev in Phase 4.** Owner request, recorded here and in the ROADMAP Phase 4 notes:
  - Jev becomes an optional provider for the **dealbreaker set-aside** (yes/no plus confidence: "does this job break 'no fintech' / 'no 6-day on-site'?").
  - Jev also becomes an optional provider for the **fit label** (Strong / Good / Stretch).
  - Add an **ablation row: Jev vs Claude Haiku** on fit-label accuracy, latency and cost.
  - The same adoption gate as D-07 applies.
- Jev as a Phase 2 enrichment provider (remote / seniority / industry classification) is possible later. Phase 2 is not changed now.
- Account-backed settings (persist the parse-model choice) arrive with accounts in Phase 5.
- Learned ranking weights and persona cross-validation belong to Phase 4.

</deferred>

---

*Phase: 03-hybrid-search-eval-baseline*
*Context gathered: 2026-11-22*
