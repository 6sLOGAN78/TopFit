---
phase: 01-foundations-walking-skeleton-deploy
verified: 2026-11-22T00:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Phase 1: Foundations & Walking-Skeleton Deploy Verification Report

**Phase Goal:** A pinned, CI-checked monorepo runs locally with one command, and a walking skeleton (API with the embedding model loaded + Postgres/pgvector) is live over HTTPS on the production custom domain.
**Verified:** 2026-11-22
**Status:** passed
**Re-verification:** No (initial verification)

Note on mode: ROADMAP marks this phase `mode: mvp`, but the goal is not written as a user story, so the MVP User Flow Coverage format does not apply. The orchestrator asked for verification anyway, so this report uses the standard goal-backward method against the 5 roadmap success criteria.

Allowed deviations (01-CONTEXT D-02/D-03/D-04/D-05): Azure VM instead of DigitalOcean; DuckDNS hostname `topfit.duckdns.org` instead of a registrable domain; single origin (`/` = Next.js, `/api/*` = FastAPI) instead of `ranker.` + `api.ranker.` subdomains. SC2 and SC3 are judged against the single-origin design.

## Goal Achievement

### Observable Truths

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---|---|---|
| 1 | `make dev` brings up db (pgvector PG18, >= 0.8.3), api, worker and web with pinned toolchain | VERIFIED | `Makefile` `dev:` runs `docker compose -f compose.yaml -f compose.dev.yaml up --build`. `compose.yaml` defines db (`pgvector/pgvector:0.8.7-pg18-trixie`), api, worker (`python -m ranker.worker`), web. Pins: `backend/.python-version` 3.14, `requires-python >=3.14,<3.15`, `sqlalchemy[asyncio]>=2.1.9,<2.2`, `apscheduler>=3.11,<4`, `psycopg[binary,pool]` 3.3, fastapi 0.165, pydantic 2.14; `web/package.json` `typescript ~5.9.3`, next 16.4.6, tailwind 4.3.3, shadcn. `make dev` itself not run by verifier (would start containers). |
| 2 | Prod `/readyz` reports DB reachable, pgvector >= 0.8.3, model loaded from the image with no download; `/healthz` responds; api RSS measured and recorded | VERIFIED | Live: `curl https://topfit.duckdns.org/api/readyz` returned `{"status":"ok","checks":{"db":{"ok":true},"pgvector":{"ok":true,"version":"0.8.7","min":"0.8.3"},"model":{"ok":true},"embedding":{"ok":true,"dim":384}}}`; `/api/healthz` 200. `health.py` queries `pg_extension.extversion` and compares to (0,8,3), runs a real 384-d embedding. `backend/Dockerfile` bakes the model into `/opt/models` at build time, sets `FASTEMBED_CACHE_PATH` and `HF_HUB_OFFLINE=1`; CI runs `ranker.selfcheck` under `--network none`. ADR 0000 records api RSS 334.4 MB / 277.7 MiB, after 100 embeddings flat, cold start 3.50 s to ready. |
| 3 | Web shell completes an HTTPS cookie round-trip with the API (single origin per D-04) | VERIFIED | Live: two `GET /api/session/ping` with a cookie jar returned `{"cookie_received":false}` then `{"cookie_received":true}`; `Set-Cookie: rk_ping=...; HttpOnly; Max-Age=3600; Path=/api; SameSite=lax; Secure`. `web/src/components/status-panel.tsx` performs this check from the browser and renders a "Cookie round-trip" row. `/` returns 200 over HTTPS; HTTP returns 308 to HTTPS. Owner approved the browser checkpoint (5 rows green). |
| 4 | Every PR runs ruff, pyright, pytest (testcontainers on the same pgvector image) and Docker build in GitHub Actions | VERIFIED | `.github/workflows/ci.yml` triggers on `pull_request`; `backend` job runs ruff check, ruff format check, pyright, pytest; `images` job builds api and web images on PRs (push only on main) plus an offline selfcheck. `tests/conftest.py` uses `PostgresContainer("pgvector/pgvector:0.8.7-pg18-trixie", driver="psycopg")`. Run 44952204254: all 6 jobs success (backend, web, deploy-config, images, gate, deploy). No PR has been opened yet, so the PR path is proven by config, not by an observed PR run. |
| 5 | API logs are JSON with request IDs; a test proves resume text, message content and email never appear in logs; `.env.example` documents every secret | VERIFIED | `ranker/observability.py`: structlog + stdlib + loguru all routed through one `ProcessorFormatter` with `redact` and `JSONRenderer`; `add_request_id` from asgi-correlation-id; `CorrelationIdMiddleware` outermost (live response carries `x-request-id`). `tests/test_logging_redaction.py` asserts on rendered output that `resume_text`, `email`, free-text emails, `content` (message content), passwords, cookies, auth headers, query strings and exception locals are absent. `tests/test_env_example.py` asserts every `Settings` field plus server/CI vars appear in `.env.example`. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `Makefile` | one-command dev, test, lint, deploy | VERIFIED | `dev`, `test`, `lint`, `typecheck`, `selfcheck`, `deploy`, `smoke` |
| `compose.yaml` / `compose.dev.yaml` | db, api, worker, web | VERIFIED | db healthcheck gates api/worker; dev runs `alembic upgrade head` before uvicorn |
| `backend/ranker/api/health.py` | healthz + readyz | VERIFIED | real DB, extversion and embedding checks; 503 on failure |
| `backend/ranker/api/session.py` | signed cookie ping | VERIFIED | itsdangerous signed, HttpOnly, Secure via settings |
| `backend/ranker/embeddings/embedder.py` | fastembed wrapper + fake | VERIFIED | used by lifespan in `main.py` |
| `backend/Dockerfile` | baked offline model | VERIFIED | model fetched at build, `HF_HUB_OFFLINE=1` at runtime |
| `backend/ranker/observability.py` | JSON logs, request IDs, redaction | VERIFIED | wired in `main.py` lifespan and middleware, and in worker |
| `backend/migrations/versions/0001_baseline_vector.py` | `CREATE EXTENSION vector` | VERIFIED | run by deploy and dev command |
| `backend/ranker/worker/__main__.py` | stub worker | VERIFIED | heartbeat + SIGTERM handling (scheduler is Phase 2) |
| `web/src/components/status-panel.tsx` | status page | VERIFIED | fetches healthz, readyz, session ping; renders real results |
| `deploy/Caddyfile`, `deploy/compose.prod.yaml`, `deploy/*.sh` | prod deploy | VERIFIED | Caddy single origin, `flush_interval -1`; CI runs `check-prod-ports.sh` |
| `.github/workflows/ci.yml` | CI/CD | VERIFIED | gates + images + secrets-gated deploy + HTTPS smoke |
| `.env.example` | every secret documented | VERIFIED | guarded by test |
| `docs/decisions/0000-hosting-and-memory.md` | memory + cold-start record | VERIFIED | measurements from prod host |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `main.py` lifespan | embedder | `build_embedder` in threadpool, `app.state.embedder` | WIRED | readyz reads `app.state.embedder` |
| readyz | Postgres | `engine.connect()`, `SELECT extversion` | WIRED | live returns 0.8.7 |
| status panel | API | same-origin `fetch('/api/...')` | WIRED | Caddy `@api path /api /api/*` routes to `api:8000` |
| CI push to main | prod | GHCR images, ssh `deploy.sh`, `smoke.sh` | WIRED | 3 consecutive green deploy runs (44950000973, 44951854473, 44952204254) |
| logging config | API and worker | `configure_logging` in lifespan and worker `main()` | WIRED | |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| `status-panel.tsx` | `rows` | `/api/healthz`, `/api/readyz`, `/api/session/ping` | Yes: live DB, extension version, real embedding, real cookie | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Prod readiness | `curl -s https://topfit.duckdns.org/api/readyz` | status ok, pgvector 0.8.7, dim 384 | PASS |
| Prod liveness | `curl -w %{http_code} .../api/healthz` | 200 | PASS |
| Cookie round-trip | two pings with cookie jar | false then true; Secure, HttpOnly, SameSite=Lax | PASS |
| HTTPS redirect | `curl http://topfit.duckdns.org/` | 308 to https | PASS |
| Request ID header | response headers | `x-request-id` present | PASS |
| Backend tests | `uv run pytest -q` (local, testcontainers) | 21 passed | PASS |
| Lint / types | `ruff check .`, `pyright` | clean, 0 errors | PASS |
| CI | `gh run view 44952204254` | all 6 jobs success | PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` declared or present. `deploy/smoke.sh` is the phase's smoke probe; it runs in every CI deploy (last 3 green). Verifier reproduced its checks by direct curl above rather than running it.

### Requirements Coverage

| Requirement | Description | Status | Evidence |
|---|---|---|---|
| FND-01 | `make dev` local stack, pgvector PG18 >= 0.8.3 | SATISFIED | Truth 1 |
| FND-02 | Pinned toolchain | SATISFIED | Truth 1 pins |
| FND-03 | PR CI: ruff, pyright, tests, Docker build | SATISFIED | Truth 4 |
| FND-04 | Walking skeleton deployed; memory, pgvector version, cold start proven | SATISFIED | Truth 2 + ADR 0000 |
| FND-05 | Model baked into image, no startup download | SATISFIED | Dockerfile + offline selfcheck in CI + 3.5 s cold start |
| OPS-01 | Public HTTPS custom domain | SATISFIED (deviation allowed) | `topfit.duckdns.org`, Let's Encrypt cert, per D-03 |
| OPS-04 | healthz, readyz, JSON logs with request IDs | SATISFIED | Truths 2 and 5 |
| SEC-07 | No PII in logs; secrets in env, documented in `.env.example` | SATISFIED (Phase 1 scope) | Truth 5. Error tracking and `llm_calls` parts do not exist yet (later phases). |

No orphaned requirements: REQUIREMENTS.md maps exactly these 8 IDs to Phase 1.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `deploy/deploy.sh` | 14 | `XXXXXX` | Info | `mktemp` template, not a debt marker |
| `backend/ranker/worker/__main__.py` | ~21 | "Phase 2: start the APScheduler..." comment | Info | Stub worker is in scope for Phase 1 (D-08) |
| `backend/tests/test_logging_redaction.py` | - | `message` key redaction not directly asserted (only `content`) | Info | Covered by `REDACT_KEYS`; consider adding an assertion |
| `01-REVIEW.md` CR-01, WR-01..WR-08 | - | readyz inference unthrottled, weak-secret acceptance in prod, redaction key gaps, SQLAlchemy `hide_parameters`, headers, TOFU keyscan | Warning (advisory) | Do not block Phase 1 goal; WR-03/WR-04 should be fixed before user data (Phase 3/4) flows through logs |

### Human Verification Required

None outstanding. The owner already approved the browser checkpoint (all 5 status rows green on https://topfit.duckdns.org).

### Gaps Summary

No gaps. All 5 roadmap success criteria hold in the codebase and on the live host. Follow-ups that are not Phase 1 blockers:

- The DuckDNS hostname is blocked by at least one Indian ISP (ADR 0000). Move to a registrable domain before the Phase 5 MVP launch. No later roadmap phase lists this explicitly, so it should be added to Phase 5's plan.
- ROADMAP still shows `01-07-PLAN.md` unchecked and "6/7 plans executed"; the orchestrator should update it.
- The PR CI path is proven by workflow config and by push runs, not yet by an actual PR.

---

_Verified: 2026-11-22_
_Verifier: Claude (gsd-verifier)_
