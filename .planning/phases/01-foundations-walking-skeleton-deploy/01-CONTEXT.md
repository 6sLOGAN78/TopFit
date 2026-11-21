# Phase 1: Foundations & Walking-Skeleton Deploy - Context

**Gathered:** 2026-11-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 1 delivers a pinned, CI-checked monorepo that runs locally with one command (`make dev`), plus a walking skeleton live over HTTPS on the production host. The skeleton is FastAPI with the embedding model loaded from the image, Postgres 18 + pgvector ≥ 0.8.3, a stub worker, and the Next.js shell. It proves memory fit, pgvector version, cold start, cookie round-trip and log hygiene before any feature work.

Requirements: FND-01, FND-02, FND-03, FND-04, FND-05, OPS-01, OPS-04, SEC-07.

Not in this phase: ingestion, search, LLM gateway, tasks queue, feature UI, scheduler jobs (the worker is a stub), Sentry/uptime (Phase 5), backups (Phase 6).

</domain>

<decisions>
## Implementation Decisions

The user delegated every Phase 1 decision to Claude ("choose everything according to yourself and this project"), then asked for zero-cost options. All decisions below are Claude's discretion, optimised for $0 hosting, Indian users and portfolio value.

### Hosting and cost
- **D-01: Zero-cost first.** The user is a student. The primary path is the **GitHub Student Developer Pack**: a free 1-year domain (e.g. `.me`/`.tech` via its registrar partners) and DigitalOcean credit. The user verifies which offers are live and claims them; that is a **human checkpoint** in the plan.
- **D-02: Server = one DigitalOcean droplet in the Bangalore region (BLR1)**, Ubuntu 24.04 LTS, ≥ 4 GB RAM, paid from student credit. This replaces the research default (Hetzner Singapore): it costs $0 with the pack, has lower latency for Indian users, and avoids Hetzner's identity-verification friction. Fallback order if the pack isn't available:
  1. Google Cloud: e2-medium (4 GB) in `asia-south1` (Mumbai) paid by the $300 / 90-day new-account credit. The user asked about GCP and it is acceptable. Note the credit expires after 90 days (then ~$25–30/mo or migrate). The always-free e2-micro (1 GB, US regions only) is too small and must not be used. On GCP, open 80/443 via VPC firewall rules in addition to ufw, and use a static external IP.
  2. Oracle Cloud Always Free ARM VM in an Indian region (Mumbai/Hyderabad); ARM64 is fine, and the onnxruntime/fastembed ARM wheels must be confirmed.
  3. Hetzner Singapore (~€5.50/mo).
  Planning must keep the deploy provider-agnostic: plain Ubuntu + Docker, nothing DO-specific.
- **D-03: Domain fallback = free dynamic-DNS hostname** (e.g. `ranker.duckdns.org`) if no free registrable domain is available. The design must work with either: the hostname is one env var (`PUBLIC_HOSTNAME`), never hard-coded.

### Topology (overrides research default of Vercel + api subdomain)
- **D-04: Single origin.** Web and API are served from one hostname by Caddy on the server: `https://<host>/` → Next.js, `https://<host>/api/*` → FastAPI. This removes cross-site cookie problems (PSL/SameSite), removes CORS, and makes even a free DDNS hostname work. Success criterion 3 ("cookie round-trip") is satisfied same-origin rather than across `ranker.` / `api.` subdomains.
- **D-05: Next.js runs on the server** as a `standalone` build in its own container. No Vercel in v1, so there are no extra accounts and everything lives in one Compose file. Vercel preview deploys are given up; that's acceptable.
- **D-06: FastAPI is mounted under the `/api` prefix** (routes `/api/healthz`, `/api/readyz`). Caddy passes the path through unchanged; it doesn't strip it.
- **D-07: Caddy handles TLS automatically** (Let's Encrypt) and gets a reverse-proxy config that won't break later SSE: `flush_interval -1` on `/api/*`, and no compression on streaming responses.
- **D-08: Production Compose services:** `caddy`, `web`, `api` (1 uvicorn worker), `worker` (stub: starts, logs a JSON heartbeat, exits cleanly on SIGTERM), `db` (`pgvector/pgvector:0.8.7-pg18-trixie`, volume at `/var/lib/postgresql`, **not** exposed outside the Docker network).

### Server baseline
- **D-09: Minimal hardening, applied by a checked-in script or cloud-init (`deploy/bootstrap.sh`):**
  - non-root `deploy` user with SSH-key-only login and password auth disabled
  - `ufw` allowing 22/80/443 only
  - unattended security upgrades
  - Docker Engine + Compose plugin
  - a 2 GB swap file as a guard against out-of-memory kills
  Re-runnable (idempotent).
- **D-10: Production secrets live in `/srv/ranker/.env`** on the server (mode 600, owned by `deploy`), never in git. `.env.example` documents every variable.

### Deploy pipeline
- **D-11: Public GitHub repo** (portfolio visibility). The user creates it and pushes; that's a human checkpoint.
- **D-12: CI on every push and PR:** ruff (lint + format check), pyright, pytest with testcontainers on the same pgvector PG18 image (`driver="psycopg"`), web lint + `tsc --noEmit` + `next build`, and Docker image builds.
- **D-13: CD on push to `main`:**
  - Build `api`/`worker` and `web` images and push them to **GHCR** (free for public repos).
  - SSH to the server (key in GitHub Actions secrets), then `docker compose pull && docker compose up -d`.
  - Run `alembic upgrade head` (empty baseline migration in Phase 1).
  - Smoke-test `https://<host>/api/readyz`. A failed smoke test fails the workflow.
  - CD is skipped automatically until the deploy secrets exist, so CI stays green before the server is bought.
- **D-14: `make deploy` is a manual fallback** doing the same steps from the developer's machine.

### Local dev workflow
- **D-15: `make dev` = `docker compose -f compose.yaml -f compose.dev.yaml up`** for db, api, worker and web, with source mounted and hot reload (uvicorn `--reload`, `next dev`). This meets FND-01's one-command requirement. No Caddy locally: web on :3000 proxies `/api` to api on :8000 through a Next.js rewrite, mirroring the production single-origin paths.
- **D-16: The native path is also supported:** `make db` (Postgres only) plus `uv run` / `npm run dev` for faster iteration. Documented in the README, not required.
- **D-17: Pre-commit hooks** run ruff, ruff-format and prettier/eslint on staged web files. Optional locally; CI is the gate.
- **D-18: Branching:** solo developer, commits go to `main` (GSD `branching_strategy: none`). CI runs on push to `main` and on any PR.

### Walking-skeleton behaviour
- **D-19: `/api/readyz`** returns 200 only when all of these pass, with a JSON body showing each check:
  - DB reachable
  - `SELECT extversion FROM pg_extension WHERE extname='vector'` ≥ 0.8.3
  - the embedding model loaded from the baked cache (`FASTEMBED_CACHE_PATH` inside the image, `HF_HUB_OFFLINE=1` so a missing model fails instead of downloading)
  - one test embedding returns 384 dims

  `/api/healthz` is liveness only.
- **D-20: Record memory (RSS)** for api, web, worker and db on the production host after startup and after 100 test embeddings, in `docs/decisions/0000-hosting-and-memory.md`. That ADR also records the provider/domain actually chosen.
- **D-21: A cookie round-trip test** in Phase 1: an endpoint `GET /api/session/ping` sets a signed httpOnly `Secure` `SameSite=Lax` test cookie, and a second call reads it back. The web shell page calls both and shows ✓/✗. A Playwright or curl smoke step checks it in production. Not real auth; guest sessions arrive in Phase 4.
- **D-22: Logging:** structlog JSON with a request-ID middleware (`X-Request-ID` honoured or generated). A redaction processor drops or masks the keys `resume_text`, `message`, `content`, `email`, `password`, `authorization`, `cookie`. A unit test asserts those values never reach log output (SEC-07).

### Claude's Discretion
- Exact repo layout follows SPEC.md §16 (`backend/`, `web/`, `config/`, `evals/`, `docs/`, `deploy/`), adjusted as the planner sees fit.
- Choice of Makefile targets, Compose file naming, GitHub Actions job split, and pre-commit tool versions.
- Whether the web shell uses shadcn/ui components yet; a minimal styled status page is enough.
- How the stub worker is structured, as long as Phase 2 can grow it into the scheduler/tasks consumer without restructuring.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project scope and requirements
- `SPEC.md` §15 (Deployment and operations), §16 (Repository structure), §17 Phase 0 — original design; superseded where research or this file disagree
- `.planning/PROJECT.md` — Constraints (pins, hosting, custom domain) and Key Decisions
- `.planning/REQUIREMENTS.md` — FND-01..05, OPS-01, OPS-04, SEC-07 wording
- `.planning/ROADMAP.md` — Phase 1 goal and success criteria

### Research (version pins and traps)
- `.planning/research/STACK.md` — exact library/image versions, Python 3.14, PG18, pgvector 0.8.7, TS ~5.9.3, APScheduler < 4, fastembed cache, SQLAlchemy 2.1 `[asyncio]`
- `.planning/research/SUMMARY.md` — corrections #15–#20 (walking-skeleton deploy, custom domain/PSL cookies, 1 uvicorn worker, hosting, version pins, baked model) and #35 (PII in logs)
- `.planning/research/PITFALLS.md` — free-tier hosting traps (RAM, sleeping hosts, `/tmp` model cache, SSE buffering)
- `.planning/research/ARCHITECTURE.md` — component boundaries the skeleton should anticipate (`services/`, `llm/`, `tasks/`, worker)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None: greenfield repo. Only `SPEC.md`, `CLAUDE.md` and `.planning/` exist.

### Established Patterns
- None yet. Phase 1 establishes them: uv-managed Python package `ranker` under `backend/`, Alembic migrations, pydantic-settings config, structlog logging, Next.js App Router under `web/`.

### Integration Points
- Phase 2 will add ingestion, the LLM gateway and the `tasks` queue into the `backend/ranker/` package and grow the stub `worker` service.
- Phase 3 adds `/api/jobs/search` and web search pages behind the same Caddy single origin.

</code_context>

<specifics>
## Specific Ideas

- The user is a student who wants everything free where possible: use GitHub Student Developer Pack offers (free domain + DigitalOcean credit), with free DDNS/Oracle Always Free as fallbacks.
- The only expected paid item in the MVP is Claude API usage (est. $5–20). Phase 1 needs no LLM key.
- Human checkpoints in this phase: claim the Student Pack / domain, create the server (or provide its IP), create the GitHub repo and add deploy secrets. Planning should batch these into as few user actions as possible, with exact click-by-click instructions, and make everything else runnable before they're done.

</specifics>

<deferred>
## Deferred Ideas

- Sentry + uptime monitoring → Phase 5 (OPS-05)
- Backups + restore runbook → Phase 6 (OPS-07). Consider a simple nightly `pg_dump` earlier if real user data arrives before Phase 6.
- Vercel preview deployments → not planned (single-origin decision D-04/D-05)
- Prometheus `/metrics` → Phase 6 (OPS-06)

</deferred>

---

*Phase: 01-foundations-walking-skeleton-deploy*
*Context gathered: 2026-11-21*
