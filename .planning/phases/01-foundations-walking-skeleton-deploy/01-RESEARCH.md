# Phase 1: Foundations & Walking-Skeleton Deploy - Research

**Researched:** 2026-11-21
**Domain:** Monorepo scaffolding (uv + Next.js), Docker/Compose packaging with a baked ONNX model, Caddy single-origin reverse proxy, GitHub Actions CI/CD to a single Ubuntu VM, structured logging hygiene
**Confidence:** HIGH for the stack, images, CI mechanics and library behaviour (verified against registries, source code and official docs in this session). MEDIUM for hosting-provider specifics and memory estimates.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

The user delegated every Phase 1 decision to Claude ("choose everything according to yourself and this project"), then asked for zero-cost options. All decisions below are Claude's discretion, optimised for $0 hosting, Indian users and portfolio value.

#### Hosting and cost
- **D-01: Zero-cost first.** The user is a student. The primary path is the **GitHub Student Developer Pack**: a free 1-year domain (e.g. `.me`/`.tech` via its registrar partners) and DigitalOcean credit. The user verifies which offers are live and claims them; that is a **human checkpoint** in the plan.
- **D-02: Server = one DigitalOcean droplet in the Bangalore region (BLR1)**, Ubuntu 24.04 LTS, ≥ 4 GB RAM, paid from student credit. This replaces the research default (Hetzner Singapore): it costs $0 with the pack, has lower latency for Indian users, and avoids Hetzner's identity-verification friction. Fallback order if the pack isn't available:
  1. Google Cloud: e2-medium (4 GB) in `asia-south1` (Mumbai) paid by the $300 / 90-day new-account credit. The user asked about GCP and it is acceptable. Note the credit expires after 90 days (then ~$25–30/mo or migrate). The always-free e2-micro (1 GB, US regions only) is too small and must not be used. On GCP, open 80/443 via VPC firewall rules in addition to ufw, and use a static external IP.
  2. Oracle Cloud Always Free ARM VM in an Indian region (Mumbai/Hyderabad); ARM64 is fine, and the onnxruntime/fastembed ARM wheels must be confirmed.
  3. Hetzner Singapore (~€5.50/mo).
  Planning must keep the deploy provider-agnostic: plain Ubuntu + Docker, nothing DO-specific.
- **D-03: Domain fallback = free dynamic-DNS hostname** (e.g. `ranker.duckdns.org`) if no free registrable domain is available. The design must work with either: the hostname is one env var (`PUBLIC_HOSTNAME`), never hard-coded.

#### Topology (overrides research default of Vercel + api subdomain)
- **D-04: Single origin.** Web and API are served from one hostname by Caddy on the server: `https://<host>/` → Next.js, `https://<host>/api/*` → FastAPI. This removes cross-site cookie problems (PSL/SameSite), removes CORS, and makes even a free DDNS hostname work. Success criterion 3 ("cookie round-trip") is satisfied same-origin rather than across `ranker.` / `api.` subdomains.
- **D-05: Next.js runs on the server** as a `standalone` build in its own container. No Vercel in v1, so there are no extra accounts and everything lives in one Compose file. Vercel preview deploys are given up; that's acceptable.
- **D-06: FastAPI is mounted under the `/api` prefix** (routes `/api/healthz`, `/api/readyz`). Caddy passes the path through unchanged; it doesn't strip it.
- **D-07: Caddy handles TLS automatically** (Let's Encrypt) and gets a reverse-proxy config that won't break later SSE: `flush_interval -1` on `/api/*`, and no compression on streaming responses.
- **D-08: Production Compose services:** `caddy`, `web`, `api` (1 uvicorn worker), `worker` (stub: starts, logs a JSON heartbeat, exits cleanly on SIGTERM), `db` (`pgvector/pgvector:0.8.7-pg18-trixie`, volume at `/var/lib/postgresql`, **not** exposed outside the Docker network).

#### Server baseline
- **D-09: Minimal hardening, applied by a checked-in script or cloud-init (`deploy/bootstrap.sh`):**
  - non-root `deploy` user with SSH-key-only login and password auth disabled
  - `ufw` allowing 22/80/443 only
  - unattended security upgrades
  - Docker Engine + Compose plugin
  - a 2 GB swap file as a guard against out-of-memory kills
  Re-runnable (idempotent).
- **D-10: Production secrets live in `/srv/ranker/.env`** on the server (mode 600, owned by `deploy`), never in git. `.env.example` documents every variable.

#### Deploy pipeline
- **D-11: Public GitHub repo** (portfolio visibility). The user creates it and pushes; that's a human checkpoint.
- **D-12: CI on every push and PR:** ruff (lint + format check), pyright, pytest with testcontainers on the same pgvector PG18 image (`driver="psycopg"`), web lint + `tsc --noEmit` + `next build`, and Docker image builds.
- **D-13: CD on push to `main`:**
  - Build `api`/`worker` and `web` images and push them to **GHCR** (free for public repos).
  - SSH to the server (key in GitHub Actions secrets), then `docker compose pull && docker compose up -d`.
  - Run `alembic upgrade head` (empty baseline migration in Phase 1).
  - Smoke-test `https://<host>/api/readyz`. A failed smoke test fails the workflow.
  - CD is skipped automatically until the deploy secrets exist, so CI stays green before the server is bought.
- **D-14: `make deploy` is a manual fallback** doing the same steps from the developer's machine.

#### Local dev workflow
- **D-15: `make dev` = `docker compose -f compose.yaml -f compose.dev.yaml up`** for db, api, worker and web, with source mounted and hot reload (uvicorn `--reload`, `next dev`). This meets FND-01's one-command requirement. No Caddy locally: web on :3000 proxies `/api` to api on :8000 through a Next.js rewrite, mirroring the production single-origin paths.
- **D-16: The native path is also supported:** `make db` (Postgres only) plus `uv run` / `npm run dev` for faster iteration. Documented in the README, not required.
- **D-17: Pre-commit hooks** run ruff, ruff-format and prettier/eslint on staged web files. Optional locally; CI is the gate.
- **D-18: Branching:** solo developer, commits go to `main` (GSD `branching_strategy: none`). CI runs on push to `main` and on any PR.

#### Walking-skeleton behaviour
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

### Deferred Ideas (OUT OF SCOPE)
- Sentry + uptime monitoring → Phase 5 (OPS-05)
- Backups + restore runbook → Phase 6 (OPS-07). Consider a simple nightly `pg_dump` earlier if real user data arrives before Phase 6.
- Vercel preview deployments → not planned (single-origin decision D-04/D-05)
- Prometheus `/metrics` → Phase 6 (OPS-06)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FND-01 | `make dev` starts db, api, worker and web (Compose, pgvector PG18, pgvector ≥ 0.8.3) | Compose layout (§Architecture Pattern 5), venv-outside-mount pattern (Pitfall 7), PG18 volume path verified (Pitfall 3) |
| FND-02 | Pinned toolchain: Py 3.14 + uv, FastAPI, Pydantic v2, SQLAlchemy 2.1 `[asyncio]`, Alembic, psycopg 3; Next App Router + TS 5.9.x, Tailwind, shadcn; APScheduler < 4 | Standard Stack (versions re-verified on PyPI/npm today), `uv_build` flat layout, `create-next-app` installs `typescript ^5` (verified), pin to `~5.9.3` |
| FND-03 | Every PR runs ruff, pyright, tests, Docker build in GHA | CI workflow pattern (§Pattern 8), action versions verified, testcontainers import path (Pitfall 9) |
| FND-04 | Walking skeleton deployed to target host, proving memory fit, pgvector version, cold start | CD gate pattern (Pitfall 1), deploy script, smoke test, memory-measurement method (§Pattern 10) |
| FND-05 | Embedding model baked into the image (`FASTEMBED_CACHE_PATH`), no network at startup | fastembed 0.9.1 source verified: `HF_HUB_OFFLINE` forces `local_files_only`, raises `ValueError` if missing; offline `--network none` smoke (§Pattern 1) |
| OPS-01 | Public HTTPS custom domain; VPS + Compose + Caddy | Caddyfile (§Pattern 3), LE rate limits & PSL (Pitfall 11), DuckDNS on PSL (verified), GHCR visibility (Pitfall 2), bootstrap (§Pattern 9) |
| OPS-04 | `/healthz`, `/readyz` (DB, model); JSON logs with request IDs | readyz code (§Code Examples), asgi-correlation-id + structlog contextvars (§Pattern 6) |
| SEC-07 | No PII/resume text in logs; secrets only in env; `.env.example` | Redaction processor placed in the shared formatter chain, `show_locals` trap (Pitfall 5), `capture_logs` bypass trap (Pitfall 6), alembic `fileConfig` trap (Pitfall 8), access log disabled (Pitfall 12) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

These come from `./CLAUDE.md` and carry the same weight as locked decisions:

- **Pins:** Python 3.14 (never 3.15, which has no onnxruntime wheels); uv; FastAPI 0.165.x; SQLAlchemy 2.1 with the `[asyncio]` extra (greenlet is no longer a default dependency); psycopg 3 `[binary,pool]`; Alembic 1.20; PostgreSQL 18 with `pgvector/pgvector:0.8.7-pg18-trixie`, using the same image locally, in CI and in prod; **`typescript@~5.9.3`** (never 7.x); Tailwind 4 (CSS-first, no `tailwind.config.js`); `apscheduler>=3.11,<4`; Node 24 LTS.
- **Single datastore:** Postgres + pgvector only. No Redis or other vector DB.
- **Scheduler never inside the API process.** Run it in a separate `worker` container (the Phase 1 stub).
- **1 uvicorn worker.** fastembed `threads=` must be set explicitly.
- **Model baked into the image** via `FASTEMBED_CACHE_PATH`. The default cache is `/tmp`.
- **Testing:** the default test run makes no live network calls and no real LLM calls. LLM tests sit behind `@pytest.mark.llm` (none exist in Phase 1).
- **pytest-asyncio 1.x:** use `asyncio_mode = "auto"`. There is no `event_loop` fixture.
- **testcontainers:** pass `driver="psycopg"`, because the default is psycopg2.
- **pyright** (strict on `ranker/`), not mypy and not `ty`.
- **Privacy:** no PII in logs. Resume files are never stored.
- **Secrets only in env vars.** `.env.example` documents each one.
- **GSD workflow:** file edits happen through GSD commands (execution happens via `/gsd-execute-phase`).
- **What NOT to use** (STACK.md): PyTorch/sentence-transformers, in-memory rate limiting, the `typescript@latest` tag, APScheduler 4 alphas, Python 3.15, and pgvector < 0.8.3.

## Summary

Phase 1 is a scaffolding-and-plumbing phase: there are no algorithms, but there are many small traps that each cost hours. Every version in STACK.md was re-checked today against PyPI, npm, Docker Hub and GHCR, and all of them still hold. All Python dependencies, fastembed's transitive ones included, ship cp314 manylinux wheels for **both amd64 and arm64**, so the Oracle ARM fallback (D-02) is technically viable. The base images (`python:3.14-slim-trixie`, `node:24-trixie-slim`, `pgvector/pgvector:0.8.7-pg18-trixie`, `caddy:2.11.9-alpine`, `ghcr.io/astral-sh/uv:0.13.9`) are all published for amd64 and arm64.

The research turned up eight things STACK.md does not cover, each of which would break the phase:

1. **GitHub Actions rejects `secrets.*` in a job-level `if:`.** Auto-skipping CD needs a small "gate" job that exposes a boolean output.
2. **GHCR packages start out private even when the repo is public.** The server can't pull until the packages are made public, or until it logs in with a token.
3. **The GitHub owner `AyushMaurya-tech` has uppercase letters**, but GHCR image names must be lowercase.
4. **structlog's `dict_tracebacks` defaults to `show_locals=True`.** That dumps local variables, such as a future `resume_text`, into JSON logs, which defeats SEC-07.
5. **`structlog.testing.capture_logs()` removes all configured processors**, so a redaction test written with it passes even when redaction is broken.
6. **Alembic's generated `env.py` calls `logging.config.fileConfig()`**, which disables existing loggers. Running migrations in a test fixture can silently mute the log output the SEC-07 test inspects.
7. **`fastapi[standard]` now pulls in OpenTelemetry SDK + exporter, fastapi-cli, httpx2 and more.** Depend on `fastapi` + `uvicorn[standard]` instead, which contradicts STACK.md's `fastapi[standard]` line.
8. **Docker-published ports bypass ufw.** The `db` service must never have a `ports:` entry in prod.

The good news: fastembed 0.9.1 (source inspected) already treats `HF_HUB_OFFLINE=1` as `local_files_only=True` and raises `ValueError("Could not load model … from any source.")` when the cache is missing. D-19's "fail instead of downloading" comes for free, and a CI step can prove FND-05 by running the built image with `--network none`. Caddy flushes `text/event-stream` immediately even without `flush_interval -1`, and it ignores client-supplied `X-Forwarded-*` headers by default. Uvicorn only trusts proxy headers from `127.0.0.1` unless `FORWARDED_ALLOW_IPS` is set, which matters for correct `https` redirects behind Caddy in Docker.

**Primary recommendation:** Build in this order: (1) a local-only monorepo with `make dev`, tests and the CI workflow (no human dependency); (2) image builds plus the offline-model smoke test in CI; (3) the CD job behind a secrets gate (lands green and skipped); (4) one batched human checkpoint (Student Pack, domain, droplet, repo, `gh secret set`, GHCR visibility), then bootstrap and first deploy; (5) the memory ADR from on-host measurements.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TLS termination, HTTP→HTTPS, single-origin routing | Edge proxy (Caddy container) | — | D-04/D-07. Caddy owns certificates in its `/data` volume. Nothing else listens on 80/443. |
| Static assets + HTML shell / status page | Frontend server (Next.js standalone) | Browser | Server renders the shell. The ✓/✗ cookie checks **must run in the browser** (client component) so cookies traverse Caddy like real traffic. |
| `/api/healthz`, `/api/readyz`, `/api/session/ping` | API (FastAPI, 1 uvicorn worker) | Database | Business endpoints live only in FastAPI. Next must not define `app/api/*` route handlers, because Caddy sends `/api` to FastAPI. |
| Embedding model load + test embedding | API process (in-memory ONNX session) | Image (baked cache) | Loaded once in lifespan. Inference runs in a thread (`anyio.to_thread.run_sync`) so it doesn't block the event loop. |
| Signed cookie issue/verify | API | Browser (stores it) | itsdangerous signing with a secret from env. The browser only stores it. |
| Schema/extension management | Database via Alembic (run by api image) | — | `CREATE EXTENSION vector` lives in migration 0001. readyz reads `pg_extension`. |
| Background heartbeat (stub) | Worker container | — | Separate process. It grows into the APScheduler + tasks consumer in Phase 2. |
| Request ID + JSON logs + redaction | API (ASGI middleware + structlog) | Worker (same logging module) | One `ranker.logging` module shared by both processes. |
| Image build/publish | CI (GitHub Actions → GHCR) | — | CD pulls immutable `sha-` tags. |
| Host hardening | VM OS (bootstrap.sh) | Cloud firewall (GCP/Oracle only) | Provider-agnostic script. Cloud firewalls are a documented extra step for the fallbacks. |

## Standard Stack

All versions below were re-verified on 2026-11-21 against PyPI JSON, `npm view`, `docker buildx imagetools inspect` and `gh api …/releases/latest` [VERIFIED: registries]. They match STACK.md except where noted.

### Core (backend, `backend/pyproject.toml`)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.14.x (`python:3.14-slim-trixie`, latest 3.14.10) | Runtime | STACK.md verdict #4 |
| uv / uv_build | 0.13.9 (`uv_build>=0.13.9,<0.14`) | Lockfile, venv, build backend | [CITED: docs.astral.sh/uv/concepts/build-backend] flat layout via `module-root = ""` |
| fastapi | 0.165.0 — **plain `fastapi`, not `fastapi[standard]`** | API | `[standard]` now pulls `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `fastapi-cli[standard]`, `httpx2`, `fastar`, `jinja2`, `email-validator`, … [VERIFIED: PyPI requires_dist]. Image bloat with nothing used. |
| uvicorn[standard] | 0.54.0 | ASGI server (uvloop, httptools, watchfiles for `--reload`) | cp314 wheels on amd64/arm64 verified |
| pydantic / pydantic-settings | 2.14.4 / 2.16.3 | Settings (`SecretStr` for secrets) | |
| sqlalchemy[asyncio] | 2.1.9 | Async engine | `[asyncio]` brings greenlet (cp314 wheels verified) |
| psycopg[binary,pool] | 3.3.9 | Driver | URL scheme `postgresql+psycopg://` works for sync and async engines [CITED: docs.sqlalchemy.org/en/21/dialects/postgresql.html] |
| alembic | 1.20.1 | Migrations | `alembic init -t async migrations` (template verified in wheel) |
| pgvector (python) | 0.5.1 | Not needed until Phase 2 (`Vector` type). Add now only if you want the pin in place. | |
| fastembed | 0.9.1 | `BAAI/bge-small-en-v1.5` → HF repo `Qdrant/bge-small-en-v1.5-onnx-Q`, file `model_optimized.onnx`, 0.067 GB, dim 384 | [VERIFIED: fastembed 0.9.1 source] |
| structlog | 26.1.0 | JSON logs | |
| asgi-correlation-id | 5.0.1 | `X-Request-ID` middleware; default validator rejects non-UUID4 incoming IDs and regenerates (log-injection safe) | [VERIFIED: source] |
| itsdangerous | 2.2.0 | `URLSafeTimedSerializer` for the ping cookie | Stable, last release 2024-04, pure Python |
| apscheduler | 3.11.3 (`>=3.11,<4`) | Pinned now (FND-02). Unused until Phase 2. | |

### Dev / test (backend `[dependency-groups] dev`)
| Library | Version | Purpose |
|---------|---------|---------|
| pytest / pytest-asyncio | 9.2.0 / 1.5.0 | `asyncio_mode="auto"`, `asyncio_default_fixture_loop_scope="session"` if session-scoped async fixtures are used |
| testcontainers[postgres] | 4.15.0 | **Import from `testcontainers.community.postgres`.** `testcontainers.postgres` emits `DeprecationWarning` in 4.15 [VERIFIED: source] |
| httpx | 0.28.1 | `TestClient`/`AsyncClient` |
| ruff | 0.17.6 | lint + format |
| pyright | 1.1.414 | type check (PyPI wrapper downloads Node on first run) |
| pre-commit | 4.x (local has 4.3.0) | hooks: `astral-sh/ruff-pre-commit` rev `v0.17.6`, `pre-commit/pre-commit-hooks` `v6.0.0` |

### Frontend (`web/package.json`)
| Library | Version | Notes |
|---------|---------|-------|
| next / create-next-app / eslint-config-next | 16.4.6 | `output: 'standalone'` |
| react / react-dom | 19.3.1 | |
| typescript | **~5.9.3** | create-next-app 16.4.6 writes `"typescript": "^5"` [VERIFIED: package source], which resolves to 5.9.3 today. Tighten to `~5.9.3` explicitly. |
| eslint / typescript-eslint | 10.15.0 / 8.73.0 | **Next 16 removed `next lint`.** Use `eslint .` with flat `eslint.config.mjs` [CITED: nextjs.org/docs/app/guides/upgrading/version-16] |
| tailwindcss / @tailwindcss/postcss | 4.3.5 | |
| shadcn (CLI) | 4.23.0 | optional in Phase 1 (discretion). A plain styled page is enough. |
| @types/node | `^24` (24.19.2) | match Node 24 LTS, not `latest` (26.x) |
| prettier | 3.10.1 | |
| @playwright/test | 1.65.1 | optional for the prod cookie smoke. curl is enough (see Pattern 8). |

### Infrastructure images / actions
| Item | Version | Verified |
|------|---------|----------|
| `pgvector/pgvector:0.8.7-pg18-trixie` | PG 18.7, `PGDATA=/var/lib/postgresql/18/docker`, `VOLUME /var/lib/postgresql`, amd64+arm64 | [VERIFIED: image config] |
| `caddy:2.11.9-alpine` | Caddy v2.11.9 (2026-11-05) | [VERIFIED] |
| `node:24-trixie-slim` (24.22.x) | amd64+arm64 | [VERIFIED] |
| `ghcr.io/astral-sh/uv:0.13.9` | amd64+arm64 | [VERIFIED] |
| actions/checkout | v7 (7.0.1) | [VERIFIED: gh api] |
| astral-sh/setup-uv | v10 (10.4.0, inputs `version`, `python-version`, `enable-cache`, `working-directory`) | [VERIFIED] |
| actions/setup-node | v7 (7.1.0) | [VERIFIED] |
| docker/login-action / setup-buildx-action / build-push-action / metadata-action / setup-qemu-action | v4 / v4 / v7 / v6 / v4 | [VERIFIED] [CITED: docs.docker.com/build/ci/github-actions/multi-platform] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| itsdangerous | Starlette `SessionMiddleware` | It wraps itsdangerous but signs a whole session dict and adds a global middleware. Overkill for one ping cookie, and Phase 4 will use opaque DB-backed tokens anyway. |
| asgi-correlation-id | hand-written middleware | 20 lines, but the library already handles UUID validation and response-header echo. Keep it. |
| plain `ssh`/`scp` in a step | `appleboy/ssh-action` v1.2.5 | A third-party action handles the deploy key. Plain OpenSSH with a pinned `known_hosts` is simpler and keeps the supply chain smaller. |
| amd64-only images | multi-arch (`linux/amd64,linux/arm64`) | DO BLR1 and GCP e2-medium are amd64. Building arm64 under QEMU slows the `next build` considerably. **Recommend a `PLATFORMS` repo variable defaulting to `linux/amd64`**, switched to both only if the Oracle ARM fallback is used. `docker/github-builder` (v1.19, native per-arch runners) exists, but its default arm runner is `ubuntu-26.04-arm` and its GHCR auth path is undocumented. Don't adopt it in Phase 1. |

**Installation (scaffold commands):**
```bash
uv self update                                  # local uv is 0.9.18; target 0.13.9
cd backend && uv init --package --build-backend uv --python 3.14 --name ranker .   # then edit to flat layout
uv add fastapi "uvicorn[standard]" pydantic-settings "sqlalchemy[asyncio]" "psycopg[binary,pool]" alembic fastembed structlog asgi-correlation-id itsdangerous "apscheduler>=3.11,<4"
uv add --dev pytest pytest-asyncio "testcontainers[postgres]" httpx ruff pyright pre-commit
cd ../web && npx create-next-app@16.4.6 . --ts --tailwind --eslint --app --src-dir --import-alias "@/*" --use-npm --yes
npm i -D -E typescript@5.9.3 @types/node@^24
```

## Package Legitimacy Audit

slopcheck 0.6.1 ran locally (`slopcheck scan`) on 20 PyPI and 14 npm package names: **all [OK]**. `npm view <pkg> scripts.postinstall` is empty for every npm package listed.

| Package | Registry | Age | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-------------|-----------|-------------|
| fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, alembic, psycopg, pgvector, fastembed, structlog, asgi-correlation-id, itsdangerous, apscheduler | PyPI | multi-year | official org repos | [OK] | Approved |
| testcontainers, pytest, pytest-asyncio, ruff, pyright, httpx, pre-commit | PyPI | multi-year | official | [OK] | Approved |
| next, react, react-dom, typescript, eslint, eslint-config-next, typescript-eslint, tailwindcss, @tailwindcss/postcss, shadcn, @types/node, @types/react, prettier, @playwright/test | npm | multi-year | official | [OK], no postinstall | Approved |

**Packages removed due to [SLOP]:** none
**Packages flagged [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
                Internet (Indian users)
                        │  HTTPS :443 (HTTP :80 → redirect, ACME HTTP-01)
                        ▼
   ┌──────────────── Ubuntu 24.04 VM (ufw: 22/80/443) ─────────────────┐
   │  caddy  (TLS via Let's Encrypt, certs in volume caddy_data:/data)   │
   │     │ path /api, /api/*  ── reverse_proxy, flush_interval -1, no gzip│
   │     │ everything else    ── encode zstd gzip → reverse_proxy          │
   │     ▼                                   ▼                            │
   │  api:8000 (uvicorn, 1 worker)        web:3000 (node server.js)       │
   │   ├─ CorrelationIdMiddleware → structlog contextvars → JSON stdout   │
   │   ├─ lifespan: load TextEmbedding from /opt/models (offline)         │
   │   ├─ /api/healthz  (no deps)                                         │
   │   ├─ /api/readyz ──► db: SELECT 1; pg_extension.extversion ≥0.8.3    │
   │   │               └► embed("probe") in thread → len==384             │
   │   └─ /api/session/ping ⇄ signed cookie (itsdangerous)                │
   │                  │                                                   │
   │                  ▼ (docker network only, NO published port)          │
   │  db (pgvector 0.8.7 / PG18, volume pgdata:/var/lib/postgresql)       │
   │  worker (python -m ranker.worker: heartbeat log, SIGTERM → exit 0)   │
   └──────────────────────────────────────────────────────────────────────┘
        ▲ ssh deploy@host (key in GH secret), scp compose+Caddyfile+deploy.sh
        │ docker compose pull (ghcr.io/<owner-lowercase>/ranker-{api,web}:sha-XXXX)
   GitHub Actions: [backend CI] [web CI] [images: build → offline smoke → push on main]
                   → [gate: secrets present?] → [deploy → migrate → up → smoke]

   Local dev (no Caddy): browser → web:3000 (next dev) ─rewrite /api/*→ api:8000 (uvicorn --reload)
```

### Recommended Project Structure
```
.
├── Makefile                    # dev, db, test, lint, fmt, typecheck, build, deploy, logs, psql
├── compose.yaml                # local base: db, api, worker, web (build contexts)
├── compose.dev.yaml            # bind mounts, ports, --reload / next dev, dev env
├── .env.example                # every variable, documented (SEC-07)
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml    # CI + images + gated CD in one workflow
├── backend/
│   ├── pyproject.toml  uv.lock  .python-version (3.14)
│   ├── Dockerfile  .dockerignore
│   ├── alembic.ini
│   ├── migrations/ (env.py, versions/0001_baseline_vector.py)
│   ├── ranker/
│   │   ├── __init__.py
│   │   ├── main.py             # create_app(), lifespan, router include (prefix /api)
│   │   ├── settings.py         # pydantic-settings, SecretStr
│   │   ├── logging.py          # configure_logging(stream=...), redact processor
│   │   ├── db.py               # async engine/sessionmaker
│   │   ├── embeddings.py       # Embedder protocol, FastEmbedEmbedder, FakeEmbedder
│   │   ├── api/                # health.py, session.py  (Phase 3+ adds routers here)
│   │   ├── services/  llm/  tasks/   # empty packages with __init__.py (ARCHITECTURE.md anchors)
│   │   └── worker/__main__.py  # stub loop; Phase 2 adds scheduler + tasks consumer
│   └── tests/ (conftest.py, test_health.py, test_readyz.py, test_session_cookie.py, test_logging_redaction.py, test_worker.py)
├── web/
│   ├── package.json  next.config.ts  eslint.config.mjs  Dockerfile  .dockerignore
│   └── src/app/ (layout.tsx, page.tsx → client StatusPanel)
├── deploy/
│   ├── compose.prod.yaml       # standalone prod file (caddy, web, api, worker, db)
│   ├── Caddyfile
│   ├── bootstrap.sh            # idempotent host setup
│   ├── deploy.sh               # pull → migrate → up --wait → prune (used by CD and make deploy)
│   └── measure-memory.sh       # ADR numbers
├── docs/decisions/0000-hosting-and-memory.md
├── config/  evals/             # placeholders per SPEC §16
```

### Pattern 1: API image with deps-only venv, baked model, offline runtime
**What:** Multi-stage uv build. The venv goes in `/opt/venv` (outside `/app`), so the dev bind mount of `./backend:/app` can't hide it. The model is downloaded at build time into `/opt/models`. The runtime sets `HF_HUB_OFFLINE=1`.
**Why:** uv docs pattern [CITED: docs.astral.sh/uv/guides/integration/docker]. fastembed reads `FASTEMBED_CACHE_PATH` (default `tempfile.gettempdir()/fastembed_cache`) and maps `HF_HUB_OFFLINE∈{1,true,yes,on}` to `local_files_only=True`. With `local_files_only` it tries once and then `raise ValueError(f"Could not load model {model.model} from any source.")` [VERIFIED: fastembed 0.9.1 `common/model_management.py`, `common/utils.py`].
```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.14-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_DEV=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
ENV FASTEMBED_CACHE_PATH=/opt/models
RUN /opt/venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

FROM python:3.14-slim-trixie AS runtime
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models
WORKDIR /app
COPY ranker ./ranker
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/models HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    FORWARDED_ALLOW_IPS=*
USER app
EXPOSE 8000
CMD ["uvicorn", "ranker.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log", "--proxy-headers", "--timeout-graceful-shutdown", "20"]
```
- The project is **not** installed into the venv (`--no-install-project`). `ranker` is importable because the CWD is `/app`: uvicorn adds its `--app-dir` (default `.`) to `sys.path`, `python -m` adds the CWD, and Alembic's `prepend_sys_path = .` does the same. The same image serves `api`, `worker` (`command: ["python","-m","ranker.worker"]`) and migrations (`alembic upgrade head`).
- Builder and runtime share the same base, so the venv's interpreter symlinks stay valid.
- Expected size: roughly 450–650 MB uncompressed (onnxruntime + numpy + tokenizers + 67 MB model) [ASSUMED]. Record the real number from `docker image ls` in the ADR.
- In CI, prove FND-05: `docker run --rm --network none ranker-api:ci python -m ranker.selfcheck`. The selfcheck loads the model and asserts 384 dims; `--network none` guarantees no download.

### Pattern 2: Embedder abstraction (testability + "no live network in tests")
`ranker/embeddings.py` defines `class Embedder(Protocol): dim: int; def embed(self, texts: list[str]) -> list[list[float]]`. `FastEmbedEmbedder` wraps `TextEmbedding(model_name="BAAI/bge-small-en-v1.5", threads=settings.embed_threads)`. `embed` returns a generator of numpy arrays, so convert with `.tolist()`. `FakeEmbedder` returns deterministic 384-d vectors. The lifespan picks one via `settings.embedder` (`"fastembed"` in images, `"fake"` in pytest). Then the backend CI job never downloads from Hugging Face, and the **real** model path is covered by the image smoke test in Pattern 1. Default `EMBED_THREADS=2` on a 2-vCPU VM: onnxruntime otherwise sizes its pool from the host's visible cores, not the container's quota.

### Pattern 3: Caddyfile (single origin, SSE-safe)
[CITED: caddyserver.com/docs/caddyfile/directives/reverse_proxy, /matchers, /concepts]
```caddyfile
{
	email {$ACME_EMAIL}
	# First attempts while DNS settles: uncomment to avoid LE rate limits
	# acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}

{$PUBLIC_HOSTNAME} {
	@api path /api /api/*
	handle @api {
		reverse_proxy api:8000 {
			flush_interval -1
		}
	}
	handle {
		encode zstd gzip
		reverse_proxy web:3000
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}
```
- `/api/*` does **not** match `/api`, so list both. Path matching is case-insensitive [CITED: matchers].
- `{$VAR}` is substituted at parse time. Compose must pass `PUBLIC_HOSTNAME` and `ACME_EMAIL` into the caddy container's `environment:`.
- `flush_interval -1` = "low-latency mode", which disables buffering. Caddy also auto-flushes `text/event-stream` and unknown-length responses [CITED].
- Caddy sets `X-Forwarded-For/Proto/Host` and **ignores incoming values** unless `trusted_proxies` is set [CITED]. That makes uvicorn's `FORWARDED_ALLOW_IPS=*` safe, because api is reachable only from the Docker network.
- `encode` sits only on the web handle, satisfying D-07. Caddy doesn't log access by default (good for PII). Don't add a `log` directive in Phase 1.
- Validate in CI: `docker run --rm -e PUBLIC_HOSTNAME=localhost -e ACME_EMAIL=a@b.c -v $PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2.11.9-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`.

### Pattern 4: FastAPI app under `/api`
```python
app = FastAPI(lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
app.include_router(api_router, prefix="/api")
app.add_middleware(CorrelationIdMiddleware)   # add LAST => outermost
```
The default `/docs` and `/openapi.json` would be routed by Caddy to **Next.js**, so move them under `/api`. Don't use `root_path`, because the prefix is real (D-06). In Starlette, the last-added middleware is the outermost. Add `CorrelationIdMiddleware` last so the request-log middleware and handlers can see the ID.

### Pattern 5: Compose files
- **Local:** `compose.yaml` (db, api, worker, web with `build:`) plus `compose.dev.yaml`:
  - `db`: `ports: ["127.0.0.1:5432:5432"]` (loopback only)
  - `api`: `volumes: ["./backend:/app"]`, `ports: ["8000:8000"]`, `command: sh -c "alembic upgrade head && uvicorn ranker.main:app --host 0.0.0.0 --port 8000 --reload"`, `environment: EMBEDDER=fastembed` (model already baked into the dev-built image)
  - `web`: `build: {context: ./web, target: deps}`, `volumes: ["./web:/app", "/app/node_modules", "/app/.next"]`, `command: npm run dev -- --hostname 0.0.0.0`, `environment: API_PROXY_TARGET=http://api:8000`, `ports: ["3000:3000"]`
- **Prod:** a **standalone** `deploy/compose.prod.yaml`, not an override of `compose.yaml`. The server has no source tree, so `build:` contexts would be dangling. It contains:
  - images `ghcr.io/${GHCR_OWNER}/ranker-api:${IMAGE_TAG}` and `…/ranker-web:${IMAGE_TAG}`
  - `restart: unless-stopped` and `init: true` on python/node services
  - healthchecks
  - `db` with **no `ports:`**
  - `caddy` with `ports: ["80:80", "443:443", "443:443/udp"]` and volumes `caddy_data:/data`, `caddy_config:/config`, `./Caddyfile:/etc/caddy/Caddyfile:ro`
  - `pgdata:/var/lib/postgresql`
  - `env_file: .env`
- Healthchecks without curl (slim images have none):
  - api: `["CMD", "python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=3)"]`
  - web: `["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]`
  - db: `["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]`
- Use liveness (`healthz`) for Docker healthchecks, **not** `readyz`. readyz does an embedding and needs the extension, so a failing readyz would restart-loop the API before migrations run.
- Next dev rewrite (dev only; returns `[]` when unset so the prod build has no rewrites):
```ts
// next.config.ts
const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    const target = process.env.API_PROXY_TARGET;
    return target ? [{ source: "/api/:path*", destination: `${target}/api/:path*` }] : [];
  },
};
```

### Pattern 6: structlog + request ID + redaction (SEC-07)
```python
# ranker/logging.py
import logging, re, sys
from typing import Any, TextIO
import structlog
from structlog.tracebacks import ExceptionDictTransformer
from asgi_correlation_id import correlation_id

REDACT_KEYS = frozenset({"resume_text", "message", "content", "email", "password",
                         "authorization", "cookie", "set-cookie", "x-api-key"})
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
MASK = "[REDACTED]"

def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (MASK if str(k).lower() in REDACT_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return EMAIL_RE.sub("[EMAIL]", value)
    return value

def redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _scrub(event_dict)          # keys like "event"/"level"/"timestamp" are not in REDACT_KEYS

def add_request_id(_l: Any, _n: str, ed: dict[str, Any]) -> dict[str, Any]:
    if (rid := correlation_id.get()) is not None:
        ed.setdefault("request_id", rid)
    return ed

def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,   # True in prod is fine; False keeps tests reconfigurable
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,          # uvicorn / sqlalchemy / alembic stdlib records
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(ExceptionDictTransformer(show_locals=False)),
            redact,                        # LAST before rendering => covers structlog + stdlib + exceptions
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name); lg.handlers.clear(); lg.propagate = True
```
[CITED: structlog.org/en/stable/standard-library.html] for the `ProcessorFormatter`/`foreign_pre_chain`/`wrap_for_formatter` structure. [VERIFIED: structlog 26.1.0 source] shows `dict_tracebacks = ExceptionRenderer(ExceptionDictTransformer())` with `SHOW_LOCALS = True` as the default. That is why the code builds its own renderer with `show_locals=False`.
- Add a small request-log middleware that logs `method`, `path` (no query string), `status` and `duration_ms` once per request, and run uvicorn with `--no-access-log`. Uvicorn's access log includes the full query string, which will carry user search text from Phase 3.
- fastembed logs through **loguru** to stderr, which isn't JSON. In `configure_logging`, call `from loguru import logger as _l; _l.remove()` and optionally re-add a sink that forwards to stdlib, so all output stays JSON [VERIFIED: fastembed depends on `loguru<0.8,>=0.7.2`].
- The worker calls the same `configure_logging()`.

### Pattern 7: Signed cookie round-trip (D-21)
```python
from itsdangerous import BadSignature, URLSafeTimedSerializer
COOKIE = "rk_ping"

@router.get("/session/ping")
def session_ping(request: Request, response: Response, settings: SettingsDep) -> dict[str, bool]:
    s = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="rk-ping-v1")
    received = False
    if raw := request.cookies.get(COOKIE):
        try:
            s.loads(raw, max_age=3600); received = True
        except BadSignature:            # SignatureExpired subclasses BadSignature
            received = False
    response.set_cookie(COOKIE, s.dumps({"v": 1}), max_age=3600, path="/api",
                        httponly=True, secure=settings.cookie_secure, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"cookie_received": received}
```
- The web status panel (a `"use client"` component) calls `fetch("/api/session/ping")` twice. The first returns `cookie_received:false`, the second `true`. Same-origin fetch sends cookies by default.
- Starlette writes `Secure` whenever `secure=True`, whatever the request scheme. `X-Forwarded-Proto`/`FORWARDED_ALLOW_IPS` matter for **redirects and `request.url`** (e.g. FastAPI's trailing-slash redirect would otherwise emit `http://…` Locations).
- Keep `COOKIE_SECURE` as a setting, default `true`. Browsers accept `Secure` cookies on `http://localhost` [ASSUMED], so dev can keep `true`. Tests use `TestClient(app, base_url="https://testserver")`, because an http base URL may cause the client cookie jar to withhold `Secure` cookies [ASSUMED: httpx uses stdlib `http.cookiejar` policy].
- Prod smoke: `curl -fsS -c jar -b jar https://$HOST/api/session/ping` twice, then assert `"cookie_received":true` with `jq`.

### Pattern 8: GitHub Actions: one workflow, gated CD
```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request:
permissions: { contents: read }
concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }

jobs:
  backend:            # uv sync --locked; ruff check; ruff format --check; pyright; pytest (testcontainers)
    runs-on: ubuntu-24.04
    defaults: { run: { working-directory: backend } }
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
        with: { version: "0.13.9", enable-cache: true, working-directory: backend }
      - run: uv sync --locked
      - run: uv run ruff check . && uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q
  web:                # npm ci; eslint .; tsc --noEmit; next build
    runs-on: ubuntu-24.04
    defaults: { run: { working-directory: web } }
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with: { node-version: 24, cache: npm, cache-dependency-path: web/package-lock.json }
      - run: npm ci && npx eslint . --max-warnings 0 && npx tsc --noEmit && npm run build
  images:
    needs: [backend, web]
    runs-on: ubuntu-24.04
    permissions: { contents: read, packages: write }
    outputs: { tag: "${{ steps.vars.outputs.tag }}" }
    steps:
      - uses: actions/checkout@v7
      - id: vars
        run: |
          echo "owner=${GITHUB_REPOSITORY_OWNER,,}" >> "$GITHUB_OUTPUT"   # lowercase! (AyushMaurya-tech)
          echo "tag=sha-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"
          echo "push=${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}" >> "$GITHUB_OUTPUT"
      - uses: docker/setup-buildx-action@v4
      - uses: docker/login-action@v4
        if: steps.vars.outputs.push == 'true'
        with: { registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}" }
      - uses: docker/build-push-action@v7          # api: load locally first for the offline smoke
        with:
          context: backend
          load: true
          tags: ranker-api:ci
          cache-from: type=gha,scope=api
          cache-to: type=gha,scope=api,mode=max
      - run: docker run --rm --network none ranker-api:ci python -m ranker.selfcheck
      # then build-push api + web with push: ${{ steps.vars.outputs.push == 'true' }},
      # tags ghcr.io/<owner>/ranker-api:{tag,main}, labels org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
      # caddy validate (Pattern 3)
  gate:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    outputs: { enabled: "${{ steps.check.outputs.enabled }}" }
    steps:
      - id: check
        env:
          KEY: ${{ secrets.DEPLOY_SSH_KEY }}
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          if [ -n "$KEY" ] && [ -n "$HOST" ]; then echo "enabled=true" >> "$GITHUB_OUTPUT"
          else echo "enabled=false" >> "$GITHUB_OUTPUT"; echo "::notice::Deploy skipped: DEPLOY_SSH_KEY/DEPLOY_HOST not set"; fi
  deploy:
    needs: [images, gate]
    if: needs.gate.outputs.enabled == 'true'
    runs-on: ubuntu-24.04
    concurrency: { group: deploy-production, cancel-in-progress: false }
    env:
      HOST: ${{ secrets.DEPLOY_HOST }}
      USER_: ${{ secrets.DEPLOY_USER || 'deploy' }}
      PUBLIC_HOSTNAME: ${{ secrets.PUBLIC_HOSTNAME }}
    steps:
      - uses: actions/checkout@v7
      - name: ssh setup
        env: { KEY: "${{ secrets.DEPLOY_SSH_KEY }}", KNOWN: "${{ secrets.DEPLOY_KNOWN_HOSTS }}" }
        run: |
          install -m 700 -d ~/.ssh
          printf '%s\n' "$KEY" > ~/.ssh/id_ed25519 && chmod 600 ~/.ssh/id_ed25519
          if [ -n "$KNOWN" ]; then printf '%s\n' "$KNOWN" > ~/.ssh/known_hosts
          else ssh-keyscan -t ed25519 "$HOST" > ~/.ssh/known_hosts; echo "::warning::DEPLOY_KNOWN_HOSTS not set; using TOFU keyscan"; fi
      - run: scp deploy/compose.prod.yaml deploy/Caddyfile deploy/deploy.sh "$USER_@$HOST:/srv/ranker/"
      - run: ssh "$USER_@$HOST" "cd /srv/ranker && IMAGE_TAG=${{ needs.images.outputs.tag }} ./deploy.sh"
      - name: smoke
        run: |
          curl -fsS --retry 12 --retry-delay 5 --retry-all-errors "https://$PUBLIC_HOSTNAME/api/readyz" | tee readyz.json
          jq -e '.status == "ok"' readyz.json
          curl -fsS -c jar -b jar "https://$PUBLIC_HOSTNAME/api/session/ping" >/dev/null
          curl -fsS -c jar -b jar "https://$PUBLIC_HOSTNAME/api/session/ping" | jq -e '.cookie_received == true'
```
- **Why the gate job exists:** "Secrets cannot be directly referenced in `if:` conditionals" [CITED: docs.github.com …/use-secrets]. `jobs.<id>.if` only has `github`, `needs`, `vars` and `inputs`, so `env` and `secrets` aren't available there [CITED: docs.github.com …/contexts]. Step-level `if` can read `env`, and the gate turns that into a `needs` output.
- `deploy.sh` (server side, also called by `make deploy`):
```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/ranker
export IMAGE_TAG="${IMAGE_TAG:?}"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${IMAGE_TAG}/" .env || echo "IMAGE_TAG=${IMAGE_TAG}" >> .env   # remember for rollback/restarts
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up -d --wait db
docker compose -f compose.prod.yaml run --rm api alembic upgrade head
docker compose -f compose.prod.yaml up -d --wait --remove-orphans
docker image prune -f
```
  The migration runs **before** the new api starts, so readyz's extension check passes on first boot. Keep the previous tag in `.env` (`PREVIOUS_IMAGE_TAG`) if you want a one-line rollback.

### Pattern 9: `deploy/bootstrap.sh` (idempotent, provider-agnostic)
Run once as root (`ssh root@IP 'bash -s' < deploy/bootstrap.sh -- --deploy-pubkey "ssh-ed25519 …" --hostname X --email Y`). Every step is guarded:
- `id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy`. Then `install -d -m 700 -o deploy -g deploy /home/deploy/.ssh`. Merge root's `authorized_keys` and the CI deploy pubkey with `sort -u`.
- sshd: write `/etc/ssh/sshd_config.d/00-ranker.conf` with `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin prohibit-password` (switch to `no` once deploy login is verified). **The file must sort before `50-cloud-init.conf`**, because sshd uses the first value it obtains [ASSUMED: sshd_config(5) "first obtained value"]. Run `sshd -t && systemctl reload ssh`, then verify with `sshd -T | grep -i passwordauthentication`.
- ufw: `ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; ufw allow 443/udp; ufw --force enable`. Allow SSH **before** enabling.
- `apt-get install -y unattended-upgrades` and write `/etc/apt/apt.conf.d/20auto-upgrades` (`APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";`).
- Docker: `command -v docker || {` the official repo steps `}`. Those are the keyring at `/etc/apt/keyrings/docker.asc`, deb822 `/etc/apt/sources.list.d/docker.sources`, and packages `docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` [CITED: docs.docker.com/engine/install/ubuntu]. Then `usermod -aG docker deploy`. Membership in the docker group is root-equivalent; that's acceptable for a solo VM, so document it.
- `/etc/docker/daemon.json` → `{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"5"}}`, written only if it differs, then restart docker. This keeps container logs from filling the disk.
- Swap: `swapon --show | grep -q /swapfile || { fallocate -l 2G /swapfile; chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile; grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; }`, plus `vm.swappiness=10` in `/etc/sysctl.d/99-ranker.conf`.
- `/srv/ranker`: `install -d -o deploy -g deploy -m 750 /srv/ranker`. If `.env` is missing, generate it with `POSTGRES_PASSWORD=$(openssl rand -hex 24)`, `SESSION_SECRET=$(openssl rand -hex 32)`, `PUBLIC_HOSTNAME`, `ACME_EMAIL`, `GHCR_OWNER`, `IMAGE_TAG=main`, `DATABASE_URL=postgresql+psycopg://ranker:${POSTGRES_PASSWORD}@db:5432/ranker`. Set `chmod 600` and `chown deploy`. **Never overwrite an existing `.env`.** Hex secrets avoid URL-escaping and configparser `%` problems.
- Timezone UTC, `apt-get install -y jq curl`.
- **GCP extra (fallback 1):** at VM creation, tick "Allow HTTP/HTTPS traffic", or `gcloud compute firewall-rules create ranker-web --allow=tcp:80,tcp:443 --target-tags=ranker-web` plus tag the VM. Reserve a static external IP [ASSUMED: standard GCP flow]. **Oracle (fallback 2):** open 80/443 in the VCN security list. Oracle's Ubuntu images ship iptables REJECT rules that must be adjusted [ASSUMED]. Remember that `PLATFORMS` must include `linux/arm64`.

### Pattern 10: Measuring memory for the ADR (D-20)
`deploy/measure-memory.sh`, run on the host:
```bash
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
ps -eo rss,args --sort=-rss | grep -E 'uvicorn|ranker.worker|next-server|node server.js|postgres|caddy' | grep -v grep | awk '{printf "%7.1f MB  %s\n",$1/1024,substr($0,index($0,$2))}'
for c in $(docker compose -f compose.prod.yaml ps -q); do echo "$(docker inspect -f '{{.Name}}' $c) peak=$(docker exec $c cat /sys/fs/cgroup/memory.peak 2>/dev/null)"; done
free -m
```
- `docker stats` reports cgroup usage minus page cache [ASSUMED: Docker CLI docs wording]. Host-side `ps` gives true per-process RSS. With `init: true`, PID 1 inside the container is `docker-init`, so `/proc/1/status` would measure the wrong process. Ubuntu 24.04 (kernel 6.8, cgroup v2) exposes `memory.peak` [ASSUMED].
- **"After 100 embeddings" without a debug endpoint:** each `/api/readyz` call performs one in-process embedding, so `for i in $(seq 100); do curl -fsS https://$HOST/api/readyz >/dev/null; done` exercises the API's own model. `docker exec api python …` would start a separate process and measure nothing useful.
- Postgres is multi-process with shared buffers, so summing RSS double-counts. Use the cgroup number for `db`.
- Cold start: record `time` from `docker compose up -d api` until healthz returns 200, and until readyz returns 200 (model load).

### Anti-Patterns to Avoid
- **`readyz` as the Docker healthcheck.** It couples restarts to DB/migrations. Use healthz.
- **Server-side fetch to `/api/...` from a Next server component.** In prod that goes to the web container's own origin, not Caddy. Phase 1's status checks run in the browser.
- **Next `app/api/*` route handlers.** Caddy routes `/api` to FastAPI, so they're unreachable in prod but work in dev, which is a confusing split.
- **Publishing `db` ports in prod** (Pitfall 4).
- **`latest` image tags in prod.** Use `sha-xxxxxxx`, and keep `main` only as a convenience tag.
- **Loading the embedding model in the worker in Phase 1.** It wastes about 150–300 MB. Phase 2 decides whether the embed backfill runs there.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| TLS certs/renewal | certbot cron + nginx | Caddy automatic HTTPS | Renewal, OCSP and HTTP→HTTPS come built in |
| Request-ID propagation | custom middleware + header parsing | `asgi-correlation-id` | UUID validation (blocks log injection), response echo, contextvar |
| Cookie signing | `hmac` + base64 + timestamp | `itsdangerous.URLSafeTimedSerializer` | Constant-time compare, expiry, salt namespaces |
| JSON logging of stdlib loggers | custom `logging.Formatter` | `structlog.stdlib.ProcessorFormatter` | One chain for uvicorn/sqlalchemy/app logs |
| Throwaway Postgres for tests | docker CLI in conftest | `testcontainers` `PostgresContainer` | Readiness wait (`pg_isready`-style exec strategy), cleanup via Ryuk |
| Migrations | `CREATE` scripts at startup | Alembic | Versioned, rerunnable, CD step |
| Image tagging/labels | shell string building | `docker/metadata-action@v6` (optional) or the explicit `vars` step shown | OCI labels link GHCR package to repo |
| Model download at runtime | custom HF fetch | fastembed + build-time `RUN` + `HF_HUB_OFFLINE=1` | Built-in offline mode raises cleanly |

**Key insight:** in this phase every "small script" (cert renewal, cookie signing, log formatting) has a security edge case already solved upstream. Spend the effort on the gate/deploy/measure glue that is genuinely project-specific.

## Common Pitfalls

### Pitfall 1: `if: secrets.X != ''` on a job
**What goes wrong:** The workflow fails validation (`Unrecognized named-value: 'secrets'`), or the condition silently doesn't work, so CD either never runs or runs and fails red before the server exists.
**Why:** The `secrets` and `env` contexts aren't available in `jobs.<id>.if` [CITED].
**How to avoid:** Use the gate job with an output (Pattern 8). Read secrets via step `env:`.
**Warning signs:** Red CI on the first push to an empty-secrets repo.

### Pitfall 2: GHCR package private, so `docker compose pull` gets `denied`
**What goes wrong:** New container packages default to **private** even for public repos. Linked packages inherit the repo's permissions but not its visibility. Public packages allow anonymous pull [CITED: docs.github.com …/configuring-a-packages-access-control-and-visibility].
**How to avoid:** After the first push, include this in the human checkpoint: Profile → Packages → `ranker-api` and `ranker-web` → Package settings → Change visibility → Public. This can't be reverted, which is fine since the repo is public. Fallback: in `deploy`, run `echo "$GITHUB_TOKEN" | ssh … docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin` before pull (job needs `packages: read`).
**Also:** the owner name `AyushMaurya-tech` has uppercase letters, and GHCR references must be lowercase. Use `${GITHUB_REPOSITORY_OWNER,,}` everywhere and set `GHCR_OWNER=ayushmaurya-tech` in the server `.env`.

### Pitfall 3: PG18 volume mounted at the old path
**What goes wrong:** Mounting `/var/lib/postgresql/data` on PG18 leaves data outside the volume. It gets lost on container recreation, or initdb complains.
**Facts:** `pgvector/pgvector:0.8.7-pg18-trixie` declares `VOLUME /var/lib/postgresql` with `PGDATA=/var/lib/postgresql/18/docker` [VERIFIED: image config]. Docker Hub's postgres docs say to mount `/var/lib/postgresql` for 18+ [CITED: hub.docker.com/_/postgres].
**How to avoid:** `pgdata:/var/lib/postgresql` in all compose files. testcontainers needs no volume.

### Pitfall 4: Docker-published ports bypass ufw
**What goes wrong:** `ports: ["5432:5432"]` in prod exposes Postgres to the internet despite `ufw` allowing only 22/80/443 [CITED: docs.docker.com/engine/network/packet-filtering-firewalls, docs.docker.com/engine/install/ubuntu].
**How to avoid:** No `ports:` on db/api/web/worker in `compose.prod.yaml`. Only caddy publishes. Dev binds db to `127.0.0.1`. Add a CI grep/yq check that only `caddy` has `ports` in `compose.prod.yaml`.

### Pitfall 5: structlog traceback rendering leaks local variables
**What goes wrong:** `structlog.processors.dict_tracebacks` = `ExceptionRenderer(ExceptionDictTransformer())`, and `ExceptionDictTransformer` defaults to `show_locals=True` (`SHOW_LOCALS = True`) [VERIFIED: structlog 26.1.0 source]. Any exception logged with `exc_info` dumps frame locals, which from Phase 4 on includes `resume_text`.
**How to avoid:** Use `ExceptionRenderer(ExceptionDictTransformer(show_locals=False))`, placed **before** `redact` in the formatter chain. Add a test that logs an exception from a function holding a secret local and asserts the secret is absent.

### Pitfall 6: a redaction test that can't fail
**What goes wrong:** `structlog.testing.capture_logs()` "disables all configured processors" while active [VERIFIED: source], so redaction never runs and the assertions test raw event dicts.
**How to avoid:** Test the **rendered output**. Call `configure_logging(stream=io.StringIO())`, emit through both `structlog.get_logger()` and `logging.getLogger("uvicorn.error")`, then assert the secret substrings are absent and `[REDACTED]` is present. Also send a real request through `TestClient` with `Authorization`/`Cookie` headers and a JSON body containing `resume_text`, and assert none of it appears in the output. Keep `cache_logger_on_first_use=False` (or call `structlog.reset_defaults()`) between tests.

### Pitfall 7: dev bind mount hides the venv / node_modules
**What goes wrong:** `./backend:/app` hides `/app/.venv` from the image, and `./web:/app` hides `node_modules`. The container then crashes with import errors, or picks up a host venv built for Python 3.10 (the local `python3` is 3.10.12).
**How to avoid:** Put the venv at `/opt/venv` (`UV_PROJECT_ENVIRONMENT`), keep source-only in `/app`, and use anonymous volumes `/app/node_modules` and `/app/.next` for web. List `.venv`, `node_modules`, `.next` and `.env*` in each `.dockerignore`.

### Pitfall 8: Alembic `env.py` `fileConfig()` wipes logging
**What goes wrong:** The generated `env.py` calls `fileConfig(config.config_file_name)`, which uses `disable_existing_loggers=True` by default. Running migrations programmatically (test fixture, or `alembic` invoked inside the app) disables `ranker.*`/`uvicorn` loggers. Later log tests then see empty output and "pass", and prod loses logs.
**How to avoid:** Delete the `fileConfig` call and the `[loggers]/[handlers]/[formatters]` sections from `alembic.ini`. Call `ranker.logging.configure_logging()` in `env.py` instead. Read the URL from `ranker.settings` in `env.py` rather than `alembic.ini`, which avoids configparser `%` interpolation errors.
**Related:** the async template's `run_migrations_online()` uses `asyncio.run()`, which fails inside a running loop. Run `command.upgrade(cfg, "head")` from a **sync**, session-scoped pytest fixture, not from an async test.

### Pitfall 9: testcontainers import path, default driver, and a `filterwarnings=error` trap
`from testcontainers.postgres import PostgresContainer` still works in 4.15 but emits `DeprecationWarning` [VERIFIED: source]. With `filterwarnings = ["error"]` in pytest config, that becomes a collection error. Use `from testcontainers.community.postgres import PostgresContainer`, `PostgresContainer("pgvector/pgvector:0.8.7-pg18-trixie", driver="psycopg")`. `get_connection_url()` then yields `postgresql+psycopg://…`.

### Pitfall 10: PID 1 signal handling in the stub worker
**What goes wrong:** Python running as PID 1 with no handler ignores SIGTERM (the kernel drops default-action signals for PID 1). `docker compose down` then waits 10 s and SIGKILLs, which fails D-08's "exits cleanly on SIGTERM" [ASSUMED: well-known Linux PID 1 semantics].
**How to avoid:** `loop.add_signal_handler(signal.SIGTERM, stop_event.set)` (and SIGINT) in `ranker.worker`, log `worker_stopped`, exit 0. Also set `init: true` in compose. Test: start the worker in a subprocess, send SIGTERM, and assert exit code 0 within 5 s.

### Pitfall 11: Let's Encrypt rate limits while DNS isn't ready yet
**Facts:** 5 failed authorizations per identifier per account per hour; 5 duplicate certificates per exact identifier set per 7 days; 50 certificates per registered domain per 7 days. Registered domain is determined by the PSL [CITED: letsencrypt.org/docs/rate-limits]. `duckdns.org`, `dedyn.io`, `dynv6.net` and `ddns.net` are on the PSL [VERIFIED: publicsuffix.org list], so `ranker.duckdns.org` gets its own 50/week bucket.
**How to avoid:**
- Create the DNS A record first and check it with `dig +short $HOST` from outside before starting caddy.
- **Persist `caddy_data`.** Losing it means re-issuance, which burns the duplicate limit.
- Use `acme_ca` staging for the first attempt if unsure.
- Don't add an AAAA record unless the VM has working IPv6, because LE prefers IPv6 when it's present.
- Caddy also falls back to ZeroSSL by default [ASSUMED].

### Pitfall 12: uvicorn access log and proxy trust
Uvicorn's `--forwarded-allow-ips` defaults to `$FORWARDED_ALLOW_IPS` or `127.0.0.1,::1` [VERIFIED: uvicorn 0.54 source]. Behind Caddy on a Docker network, the peer is a 172.x address, so without `FORWARDED_ALLOW_IPS=*` the scheme stays `http` and client IPs are Caddy's. The access log prints full query strings, so disable it (`--no-access-log`) and log path only (Pattern 6).

### Pitfall 13: `fastapi[standard]` bloat
`fastapi[standard]` 0.165 pulls the OpenTelemetry SDK + OTLP exporter, `fastapi-cli[standard]`, `httpx2`, `fastar`, jinja2 and more [VERIFIED: PyPI]. It inflates the image and the attack surface. Depend on `fastapi` + `uvicorn[standard]`. Add `python-multipart` in Phase 4 for uploads. This corrects STACK.md's "`fastapi[standard]`" note.

### Pitfall 14: pgvector extension version lag after image upgrades
`pg_extension.extversion` is the **installed** version. If the image is later bumped (e.g. to 0.9.x), the extension stays at its old version until `ALTER EXTENSION vector UPDATE;` runs. readyz's check reads the installed version, so that's correct, but upgrades need a migration. Migration 0001 should use `CREATE EXTENSION IF NOT EXISTS vector;`. The image's `POSTGRES_USER` is a superuser, so this works. A future non-superuser app role would need it pre-created.

### Pitfall 15: Student-credit runway
A 4 GB / 2 vCPU DO Basic droplet is roughly $24/mo [ASSUMED]. A $200 credit therefore lasts about 8 months, not 12. DO requires a card or PayPal to activate the credit [CITED: digitalocean.com/github-students via search]. Record the burn date in the ADR and add a reminder. Indian debit cards sometimes fail international verification [ASSUMED], in which case use PayPal or move to the GCP fallback.

## Code Examples

### readyz (D-19)
```python
# ranker/api/health.py
import asyncio, anyio
from fastapi import APIRouter, Request, Response
from sqlalchemy import text

router = APIRouter()
MIN_VECTOR = (0, 8, 3)

@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    engine = request.app.state.engine
    try:
        async with asyncio.timeout(3), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            checks["db"] = {"ok": True}
            ver = (await conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"))).scalar_one_or_none()
            ok = ver is not None and tuple(int(p) for p in ver.split(".")[:3]) >= MIN_VECTOR
            checks["pgvector"] = {"ok": ok, "version": ver, "min": "0.8.3"}
    except Exception as exc:                       # log type only, never the DSN
        checks.setdefault("db", {"ok": False, "error": type(exc).__name__})
        checks.setdefault("pgvector", {"ok": False})
    embedder = getattr(request.app.state, "embedder", None)
    checks["model"] = {"ok": embedder is not None}
    if embedder is not None:
        try:
            with anyio.fail_after(5):
                vec = await anyio.to_thread.run_sync(embedder.embed, ["readiness probe"])
            checks["embedding"] = {"ok": len(vec[0]) == 384, "dim": len(vec[0])}
        except Exception as exc:
            checks["embedding"] = {"ok": False, "error": type(exc).__name__}
    else:
        checks["embedding"] = {"ok": False}
    all_ok = all(c["ok"] for c in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ok" if all_ok else "unavailable", "checks": checks}
```
Lifespan: `configure_logging()`. Create the engine with `create_async_engine(settings.database_url, pool_size=5, pool_pre_ping=True)`. Build the embedder **in a thread** (`await anyio.to_thread.run_sync(build_embedder)`) and catch load failure: log it, leave `app.state.embedder = None` so healthz stays live and readyz reports `model: false`, rather than crash-looping. Dispose the engine on shutdown.

### Migration 0001 (empty baseline + extension)
```python
revision = "0001"; down_revision = None
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
```

### conftest (testcontainers, sync migration fixture)
```python
import os, pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

PG_IMAGE = "pgvector/pgvector:0.8.7-pg18-trixie"

@pytest.fixture(scope="session")
def database_url() -> str:
    with PostgresContainer(PG_IMAGE, driver="psycopg") as pg:
        url = pg.get_connection_url()
        os.environ["DATABASE_URL"] = url          # read by ranker.settings and migrations/env.py
        os.environ["EMBEDDER"] = "fake"
        command.upgrade(Config("alembic.ini"), "head")   # sync fixture => asyncio.run in env.py is fine
        yield url
```
`pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
testpaths = ["tests"]
filterwarnings = ["error"]
```
Tests for readyz can use `httpx.AsyncClient(transport=ASGITransport(app), base_url="https://testserver")` with the lifespan run via `asgi-lifespan`, or `with TestClient(app, base_url="https://testserver") as c:` (the context manager runs the lifespan). Add a readyz test that drops the extension in a separate DB and expects 503.

### Worker stub
```python
# ranker/worker/__main__.py
import asyncio, signal, structlog
from ranker.logging import configure_logging
from ranker.settings import get_settings

async def run() -> None:
    log = structlog.get_logger("ranker.worker")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker_started")
    while not stop.is_set():
        log.info("worker_heartbeat")
        try:
            await asyncio.wait_for(stop.wait(), timeout=get_settings().worker_heartbeat_seconds)
        except TimeoutError:
            pass
    log.info("worker_stopped")
    # Phase 2: AsyncIOScheduler (apscheduler<4) + tasks consumer start/stop around this loop

def main() -> None:
    configure_logging()
    asyncio.run(run())

if __name__ == "__main__":
    main()
```

### Web Dockerfile (standalone)
```dockerfile
FROM node:24-trixie-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS builder
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-trixie-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
```
[CITED: nextjs.org/docs/app/api-reference/config/next-config-js/output] Standalone doesn't copy `public` or `.next/static`; `PORT`/`HOSTNAME` env vars apply. A `.env`/`.env.production` loaded at build is copied into standalone, so keep `.env*` out of the build context via `.dockerignore`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `next lint` | `eslint .` + flat `eslint.config.mjs` | Next 16 | `npm run lint` script must call eslint directly |
| PG ≤17 volume `/var/lib/postgresql/data` | PG18+ `/var/lib/postgresql` (PGDATA `/var/lib/postgresql/18/docker`) | postgres 18 images | compose volume path |
| `testcontainers.postgres` | `testcontainers.community.postgres` | testcontainers 4.x (deprecated alias in 4.15) | import path |
| `fastapi[standard]` lean | `[standard]` now includes OTel SDK/exporter + fastapi-cli | FastAPI 0.16x | use `fastapi` + `uvicorn[standard]` |
| Docker apt `.list` file | deb822 `/etc/apt/sources.list.d/docker.sources` | current Docker docs | bootstrap script |
| QEMU multi-arch builds | native `ubuntu-24.04-arm` runners (free for public repos, GA 2025-08) / `docker/github-builder` | 2025 | arm64 path if Oracle is chosen |
| `uv sync --frozen` everywhere | `--locked` when pyproject is present (validates lock) | uv docs | Dockerfile + CI |

**Deprecated/outdated:** `pytest-asyncio` `event_loop` fixture (removed in 1.x); APScheduler 4 alphas; `fastapi-slim` (discontinued).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | API image ~450–650 MB uncompressed | Pattern 1 | Low. Just record the measured value. |
| A2 | Browsers accept `Secure` cookies on `http://localhost` | Pattern 7 | Dev cookie check fails. Set `COOKIE_SECURE=false` in `compose.dev.yaml`. |
| A3 | httpx `TestClient` withholds `Secure` cookies on `http://testserver` | Pattern 7 | None if you use the https base_url anyway |
| A4 | sshd uses the first obtained value, so `00-` must sort before `50-cloud-init.conf` | Pattern 9 | Password auth stays on. Mitigated by verifying with `sshd -T`. |
| A5 | Python as PID 1 ignores SIGTERM without a handler | Pitfall 10 | None: the handler and `init: true` are added regardless |
| A6 | `docker stats` subtracts page cache; `memory.peak` exists on Ubuntu 24.04 | Pattern 10 | ADR numbers slightly off. Host `ps` RSS is the primary figure. |
| A7 | DO 4 GB droplet ≈ $24/mo, so $200 lasts ~8 months; Indian cards may fail verification | Pitfall 15 | Budget/runway planning. The user confirms in the DO console. |
| A8 | GCP "Allow HTTP/HTTPS" / tag-based firewall flow; Oracle Ubuntu iptables REJECT rules | Pattern 9 | Fallback-only. The user follows provider docs at that time. |
| A9 | Caddy falls back to ZeroSSL automatically | Pitfall 11 | Low |
| A10 | Memory estimates (api 250–400 MB with model, web 80–150 MB, db 60–150 MB idle, caddy 20–40 MB, worker 50–80 MB) | Summary/ADR | This is exactly what D-20 measures. A 4 GB VM leaves ample headroom either way. |
| A11 | DuckDNS update URL `https://www.duckdns.org/update?domains=<sub>&token=<tok>&ip=` (blank ip = auto-detect); a static VPS IP only needs setting once in the web UI | Open Questions | Low. Not needed if the Student Pack domain is used. |

## Open Questions (RESOLVED)

1. **Which domain path will the user actually get?**
   - What we know: the Student Pack currently lists a Namecheap 1-year `.me` domain and $200 DO credit (1 year) [search results; the user must confirm at education.github.com/pack]. DuckDNS is on the PSL, so cookies and LE limits are isolated.
   - RESOLVED: Recommendation: the plan treats `PUBLIC_HOSTNAME` as an opaque input collected at the human checkpoint. Both paths need only one A record pointing at the VM IP. With Namecheap: Advanced DNS → A record `@` (and optionally `www`) → IP, TTL 5 min.
2. **Should images be multi-arch from day one?**
   - RESOLVED: Recommendation: no. Use a `PLATFORMS` repo variable (default `linux/amd64`). The arm64 wheels are verified, so switching later is a config change.
3. **Playwright vs curl for the prod cookie smoke (D-21 allows either)?**
   - RESOLVED: Recommendation: curl + jq in CD (Pattern 8). It exercises Caddy, TLS and the Secure cookie end to end. A Playwright browser check of the ✓/✗ UI can wait for the Phase 4/5 E2E suite.
4. **Who runs `bootstrap.sh`?** It needs root SSH once. DO injects the user's key for root at droplet creation. RESOLVED: Recommendation: the human checkpoint includes `ssh root@IP 'bash -s' < deploy/bootstrap.sh -- …` (or pasting the script as droplet "User data"/cloud-init). Everything after that is automated.
5. **Batched human checkpoint (one sitting), in this order:** RESOLVED (adopted by plan 01-07).
   1. Claim the Student Pack, then DO credit and the domain.
   2. Create the droplet in BLR1, Ubuntu 24.04, 4 GB, with an SSH key.
   3. Create the DNS A record.
   4. Create the public GitHub repo and push.
   5. Generate the CI key locally with `ssh-keygen -t ed25519 -f ~/.ssh/ranker_deploy -N ""`.
   6. Run bootstrap with the pubkey.
   7. `gh secret set DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY < ~/.ssh/ranker_deploy`, `DEPLOY_KNOWN_HOSTS` (from `ssh-keyscan -t ed25519 IP`) and `PUBLIC_HOSTNAME`. The `gh` CLI 2.98 is installed locally, which avoids UI clicking.
   8. Re-run the workflow.
   9. Make the two GHCR packages public.
   10. Run `measure-memory.sh` and fill in the ADR.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker Engine + Compose + Buildx | `make dev`, image builds, testcontainers | ✓ | 29.9.2 / Compose v5.6.1 / buildx 0.38.1 | — |
| uv | backend | ✓ (old) | 0.9.18 | `uv self update` → 0.13.9. uv downloads Python 3.14 itself. |
| Python 3.14 | backend native path | ✗ (system 3.10.12) | — | `uv python install 3.14` (managed), or only use the Docker path |
| Node / npm | web native path | ✓ (old major) | v22.23.4 / 10.8.2 | Next 16 needs ≥ 20.9, so 22 works locally. Images use Node 24. Optionally `nvm install 24`. |
| GNU make | `make dev` | ✓ | 4.3 | — |
| gh CLI | repo creation, `gh secret set` | ✓ | 2.98.0 | GitHub web UI |
| pre-commit | D-17 | ✓ | 4.3.0 | also add it as a uv dev dependency |
| caddy (local binary) | not needed | ✗ | — | validate via the `caddy:2.11.9-alpine` container |
| ctx7 | research only | ✗ | — | n/a |
| Production VM, domain, GitHub repo, secrets | FND-04/OPS-01 | ✗ (not yet) | — | **Blocking for phase completion only.** All code/CI work proceeds first, and CD stays skipped via the gate. |

**Missing dependencies with no fallback:** the production host, DNS name and GitHub repo, which are human checkpoint items.
**Missing with fallback:** Python 3.14 (uv-managed), caddy (container).

(Validation Architecture section omitted: `workflow.nyquist_validation` is `false` in `.planning/config.json`.)

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (Phase 4) | — |
| V3 Session Management | partial | ping cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api`, signed + `max_age` via itsdangerous. Secret from env (`SecretStr`). |
| V4 Access Control | minimal | Docs at `/api/docs` are public. Acceptable for a portfolio project; revisit in Phase 6. |
| V5 Input Validation | yes | Pydantic. `X-Request-ID` validated as UUID4 by asgi-correlation-id. |
| V6 Cryptography | yes | itsdangerous HMAC, `openssl rand -hex 32` secrets, Caddy/LE TLS. Nothing hand-rolled. |
| V7 Error Handling & Logging | **yes (SEC-07)** | redaction processor, `show_locals=False`, no access log with query strings, error type only in readyz |
| V9 Communications | yes | Caddy HTTPS + HSTS. HTTP only for ACME/redirect. |
| V14 Configuration | yes | secrets only in `/srv/ranker/.env` (600). `.dockerignore` excludes `.env*` (images are public on GHCR). Pinned images/actions. |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Postgres exposed via Docker port publish | Information disclosure | no `ports:` on db; CI check; ufw is not sufficient |
| Secrets baked into public GHCR image | Information disclosure | `.dockerignore` `.env*`; runtime `env_file`; never `ARG` secrets |
| Spoofed `X-Forwarded-*` / client IP | Spoofing | Caddy ignores incoming XFF by default; api unreachable except via Caddy |
| Log injection via request ID | Tampering | UUID4 validator regenerates invalid IDs |
| PII in logs (headers, bodies, tracebacks, query strings) | Information disclosure | redaction in the shared formatter, `show_locals=False`, no access log, rendered-output tests |
| SSH brute force | Elevation | key-only auth, `PermitRootLogin prohibit-password`→`no`, ufw; fail2ban optional |
| Compromised third-party Action | Tampering | official `actions/*` and `docker/*` at major tags, no third-party SSH action, `permissions:` minimal per job, `packages: write` only on `images` |
| MITM on first SSH from CI | Spoofing | `DEPLOY_KNOWN_HOSTS` secret pins the host key (keyscan fallback warns) |
| Cookie tampering | Tampering | itsdangerous signature + salt; test with a modified cookie → `cookie_received:false` |

## Sources

### Primary (HIGH confidence)
- PyPI JSON API (live): fastapi 0.165.0 (+ `[standard]` requires_dist), uvicorn 0.54.0, pydantic 2.14.4, pydantic-settings 2.16.3, sqlalchemy 2.1.9, alembic 1.20.1, psycopg 3.3.9, pgvector 0.5.1, fastembed 0.9.1 (requires_dist), structlog 26.1.0, asgi-correlation-id 5.0.1, itsdangerous 2.2.0, apscheduler 3.11.3, testcontainers 4.15.0, pytest 9.2.0, pytest-asyncio 1.5.0, ruff 0.17.6, pyright 1.1.414, onnxruntime 1.32.0 (cp314 manylinux_2_28 x86_64 + aarch64), psycopg-binary, tokenizers, numpy, pillow, mmh3, py-rust-stemmers, uvloop, httptools, watchfiles, greenlet, pydantic-core (cp314 amd64+arm64 wheels)
- Wheel source inspection (downloaded, read-only): fastembed 0.9.1 (`common/model_management.py`, `common/utils.py`, `text/onnx_embedding.py`); structlog 26.1.0 (`tracebacks.py`, `processors.py`, `testing.py`); uvicorn 0.54.0 (`config.py`); testcontainers 4.15.0 (`postgres.py`, `community/postgres`); asgi-correlation-id 5.0.1 (`middleware.py`); alembic 1.20.1 templates; create-next-app 16.4.6 (`typescript:"^5"`)
- npm registry (live): next/create-next-app/eslint-config-next 16.4.6, react 19.3.1, eslint 10.15.0, typescript-eslint 8.73.0, tailwindcss 4.3.5, shadcn 4.23.0, prettier 3.10.1, @playwright/test 1.65.1, @types/node 24.19.2; postinstall scripts empty
- `docker buildx imagetools inspect`: pgvector/pgvector:0.8.7-pg18-trixie (Volumes, PGDATA, PG_VERSION 18.7), caddy:2.11.9-alpine, python:3.14-slim-trixie, node:24-trixie-slim, ghcr.io/astral-sh/uv:0.13.9 (platforms)
- `gh api releases/latest`: actions/checkout v7.0.1, astral-sh/setup-uv v10.4.0, actions/setup-node v7.1.0, docker/* actions, caddy v2.11.9, ruff-pre-commit v0.17.6
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets: secrets not allowed in `if:`
- https://docs.github.com/en/actions/reference/workflows-and-actions/contexts: context availability table
- https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility: GHCR default private, anonymous pull for public
- https://caddyserver.com/docs/caddyfile/directives/reverse_proxy, /matchers, /concepts
- https://hub.docker.com/_/postgres: PG18 PGDATA/VOLUME change
- https://docs.docker.com/engine/install/ubuntu/ and /engine/network/packet-filtering-firewalls/: apt repo, ufw bypass
- https://docs.docker.com/build/ci/github-actions/multi-platform/: action major versions, github-builder
- https://docs.astral.sh/uv/guides/integration/docker/ and /concepts/build-backend/
- https://nextjs.org/docs/app/api-reference/config/next-config-js/output: standalone
- https://www.structlog.org/en/stable/standard-library.html: ProcessorFormatter
- https://letsencrypt.org/docs/rate-limits/; https://publicsuffix.org/list/public_suffix_list.dat (duckdns.org present)

### Secondary (MEDIUM confidence)
- https://github.com/docker/github-builder README: default runner mapping `ubuntu-26.04-arm`, inputs
- https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/ and https://docs.github.com/en/actions/reference/runners/github-hosted-runners: free arm64 runners for public repos
- https://nextjs.org/docs/app/guides/upgrading/version-16 (via search): `next lint` removed, codemod `next-lint-to-eslint-cli`
- https://education.github.com/pack and https://digitalocean.com/github-students (via search): $200 DO credit for 1 year (card/PayPal verification), Namecheap `.me` 1 year

### Tertiary (LOW confidence)
- Provider pricing and runway, Oracle iptables behaviour, GCP firewall UI flow, and the DuckDNS update URL: see the Assumptions Log

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH. Every version, wheel and image was checked live today.
- Architecture: HIGH. The patterns come from official docs, and the critical behaviours (fastembed offline, structlog locals, uvicorn proxy trust, testcontainers import) were read from source.
- Pitfalls: HIGH for 1–9, 12–14 (verified). MEDIUM for 10, 11 (partly assumed), 15 (pricing).
- Hosting/provider specifics: MEDIUM-LOW. The user confirms them at the human checkpoint.

**Research date:** 2026-11-21
**Valid until:** ~2026-12-21 (stable stack). Re-check GitHub Student Pack terms and provider pricing at checkpoint time.
