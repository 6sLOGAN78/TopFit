---
phase: 01-foundations-walking-skeleton-deploy
reviewed: 2026-11-22T00:00:00Z
depth: standard
files_reviewed: 55
files_reviewed_list:
  - .env.example
  - .github/workflows/ci.yml
  - .pre-commit-config.yaml
  - Makefile
  - backend/.dockerignore
  - backend/Dockerfile
  - backend/alembic.ini
  - backend/migrations/env.py
  - backend/migrations/versions/0001_baseline_vector.py
  - backend/pyproject.toml
  - backend/ranker/__init__.py
  - backend/ranker/api/__init__.py
  - backend/ranker/api/health.py
  - backend/ranker/api/session.py
  - backend/ranker/db/__init__.py
  - backend/ranker/db/engine.py
  - backend/ranker/embeddings/__init__.py
  - backend/ranker/embeddings/embedder.py
  - backend/ranker/llm/__init__.py
  - backend/ranker/main.py
  - backend/ranker/observability.py
  - backend/ranker/selfcheck.py
  - backend/ranker/services/__init__.py
  - backend/ranker/settings.py
  - backend/ranker/tasks/__init__.py
  - backend/ranker/worker/__init__.py
  - backend/ranker/worker/__main__.py
  - backend/tests/conftest.py
  - backend/tests/test_env_example.py
  - backend/tests/test_health.py
  - backend/tests/test_logging_redaction.py
  - backend/tests/test_readyz.py
  - backend/tests/test_request_id.py
  - backend/tests/test_session_cookie.py
  - backend/tests/test_worker.py
  - compose.dev.yaml
  - compose.yaml
  - deploy/Caddyfile
  - deploy/bootstrap.sh
  - deploy/check-prod-ports.sh
  - deploy/compose.prod.yaml
  - deploy/deploy.sh
  - deploy/measure-memory.sh
  - deploy/smoke.sh
  - web/.dockerignore
  - web/Dockerfile
  - web/eslint.config.mjs
  - web/next.config.ts
  - web/package.json
  - web/src/app/layout.tsx
  - web/src/app/page.tsx
  - web/src/components/status-panel.tsx
  - web/src/components/ui/button.tsx
  - web/src/lib/utils.ts
  - web/tsconfig.json
  - README.md
  - docs/decisions/0000-hosting-and-memory.md
findings:
  critical: 1
  warning: 8
  info: 10
  total: 19
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-11-22T00:00:00Z
**Depth:** standard
**Files Reviewed:** 55
**Status:** issues_found

## Narrative Findings (AI reviewer)

## Summary

The walking skeleton is small and mostly sound. The cookie round-trip fix from 4d2fcec is correct: the first ping is discarded and only the second is asserted, in both `status-panel.tsx` and `smoke.sh`. Only Caddy publishes ports. Secrets are generated on the host, and stack traces are rendered without locals.

The main problem is that the public, unauthenticated `/api/readyz` runs a real ONNX embedding on every hit. Its 5 s timeout cannot interrupt that work. On the single-worker production API this lets anyone exhaust CPU and the shared threadpool. Other findings: the rollback bookkeeping in `deploy.sh` can lose the last good tag, nothing in config stops weak secrets or insecure cookies in production, the PII redaction has gaps that will matter once Phase 2+ logs real user data, and ASVS L1 security headers (CSP, frame-ancestors) are missing.

## Critical Issues

### CR-01: Public `/api/readyz` runs model inference per request with no rate limit, and its timeout does not work

**File:** `backend/ranker/api/health.py:22-50`
**Issue:**
- `readyz` is reachable without authentication at `https://<host>/api/readyz`. The status page also calls it on every page load.
- Every call opens a DB connection and runs `embedder.embed(["readiness probe"])` in the anyio worker-thread pool. Production runs one uvicorn worker, and the embedder uses `EMBED_THREADS=2` ONNX threads.
- A plain request loop therefore pins the VM's CPUs. It also fills anyio's default 40-token thread limiter, which is shared with every sync endpoint (`session_ping` is a sync `def`) and, later, with all query embedding for search. The result is denial of service for the whole API.
- `anyio.fail_after(5)` around `anyio.to_thread.run_sync(...)` does not limit latency. `run_sync` defaults to `abandon_on_cancel=False`, so the cancel is held back until the thread finishes. The request still waits for the full embedding, and only then gets the `TimeoutError`.
- `deploy/measure-memory.sh embeddings` already uses readyz as an embedding load generator. That shows the endpoint is a cheap way to trigger expensive work.

**Fix:** Keep the public probe cheap. Check that the model loaded and the DB/pgvector are reachable, and cache the embedding self-test result:
```python
# lifespan: run the embedding self-test once, store the result
vec = await anyio.to_thread.run_sync(embedder.embed, ["readiness probe"])
app.state.embed_dim = len(vec[0])

# readyz: no inference on the request path
dim = getattr(request.app.state, "embed_dim", 0)
checks["embedding"] = {"ok": dim == EMBED_DIM, "dim": dim}
```
If a live inference check is still wanted, put it on an internal-only path that Caddy does not route (for example `respond /api/readyz/deep 404`). Or use a dedicated `CapacityLimiter(1)` with `abandon_on_cancel=True` and cache the result for about 10 s. Also use `EMBED_DIM` instead of the literal `384` at line 47.

## Warnings

### WR-01: `deploy.sh` overwrites `PREVIOUS_IMAGE_TAG` before the deploy succeeds, so a failed or retried deploy loses the last good tag

**File:** `deploy/deploy.sh:25-31,40`
**Issue:** `.env` is changed (`PREVIOUS_IMAGE_TAG=<current>`, `IMAGE_TAG=<new>`) before `dc pull`, the migration and `up --wait`. Example: tag A is good. Deploying B fails at pull, migration or health wait, so `.env` now holds `IMAGE_TAG=B, PREVIOUS=A`. Retrying B (re-running CI) gives `PREVIOUS=B, IMAGE_TAG=B`. The rollback hint now points at the broken tag and A is not recorded anywhere. Also, after a failed run `.env` points at the failing image, so any later `docker compose up` on the host (manual restart, measure-memory `coldstart`) starts B.
**Fix:** Record the tags only after `up --wait` succeeds, and pass the candidate tag through the environment until then:
```bash
current="$(grep '^IMAGE_TAG=' .env | tail -n1 | cut -d= -f2- || true)"
export IMAGE_TAG   # compose interpolation: shell env takes precedence over .env
dc pull && dc up -d --wait db && dc run --rm api alembic upgrade head
dc up -d --wait --wait-timeout 240 --remove-orphans
[ -n "$current" ] && [ "$current" != "$IMAGE_TAG" ] && set_env PREVIOUS_IMAGE_TAG "$current"
set_env IMAGE_TAG "$IMAGE_TAG"
```

### WR-02: Settings accept weak or placeholder secrets and insecure cookies when `APP_ENV=production`

**File:** `backend/ranker/settings.py:13-16`
**Issue:** `session_secret` accepts any string, including the `.env.example` placeholder `change-me`, the compose value `dev-only-not-secret-change-in-prod`, or `dev` from the README. `cookie_secure=false` is also accepted in production. `app_env` is declared but never read, so the "production" label enforces nothing. Anyone who copies `.env.example` or `compose.yaml` values to a server gets forgeable signed cookies without any warning. This becomes an auth bypass once the same secret signs guest sessions (ASVS V6.4 / V3.4).
**Fix:**
```python
from pydantic import model_validator

@model_validator(mode="after")
def _prod_guardrails(self) -> "Settings":
    secret = self.session_secret.get_secret_value()
    if len(secret) < 32:
        raise ValueError("SESSION_SECRET must be at least 32 characters")
    if self.app_env == "production":
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if "change-me" in secret or secret.startswith("dev"):
            raise ValueError("placeholder SESSION_SECRET in production")
    return self
```
The dev compose and README values must then be at least 32 characters.

### WR-03: PII redaction misses underscore keys, common secret names and non-primitive values

**File:** `backend/ranker/observability.py:22-47`
**Issue:**
- (a) `set-cookie` and `x-api-key` can never match a structlog kwarg, because Python identifiers cannot contain `-`. `log.info("x", set_cookie=..., x_api_key=...)` goes out in clear text.
- (b) Common sensitive names are missing: `token`, `access_token`, `secret`, `session_secret`, `api_key`, `database_url`, `dsn`, `password_hash`, `phone`, `query`/`q` (search text is user text, and SPEC forbids logging it).
- (c) `_scrub` only processes `dict`/`list`/`tuple`/`str`. Any other value, such as a Pydantic model holding `resume_text`, a dataclass or a `set`, goes to `JSONRenderer`, whose fallback is `repr()`. The raw repr is emitted with no key masking and no email scrubbing, because the email regex only runs on `str` values before rendering.
- (d) Exception messages under `exception[].exc_value` only get email scrubbing.
- Tests cover only the happy-path keys.

**Fix:** Normalise key names before matching (`str(k).lower().replace("-", "_")`) and extend the set. Make the fallback safe by stringifying unknown objects and then scrubbing them:
```python
def _norm(k: object) -> str: return str(k).lower().replace("-", "_")
REDACT_KEYS = frozenset({"resume_text","message","content","email","password","password_hash",
  "authorization","cookie","set_cookie","x_api_key","api_key","token","access_token",
  "secret","session_secret","database_url","dsn","phone","query"})
...
if not isinstance(value, (int, float, bool, type(None))):
    return EMAIL_RE.sub("[EMAIL]", repr(value))
```
Add a test with a `set_cookie=` kwarg and a Pydantic object value.

### WR-04: SQLAlchemy engine does not hide bound parameters, so DB exceptions will log user data

**File:** `backend/ranker/db/engine.py:9`
**Issue:** SQLAlchemy `DBAPIError.__str__` includes `[SQL: ...]` and `[parameters: {...}]`. When a query fails in Phase 2+ (an insert of resume text, an email unique violation) and is logged with `log.exception` or by uvicorn's error logger, the bound values land in `exception[].exc_value`. Only emails are masked there, so resume text, chat text and other PII get through. This breaks the CLAUDE.md "no PII in logs" constraint, and the exception path is easy to miss in review.
**Fix:**
```python
return create_async_engine(settings.database_url, pool_size=5, pool_pre_ping=True,
                           hide_parameters=True)
```
Apply the same setting to the Alembic engine in `migrations/env.py` for consistency.

### WR-05: Missing ASVS L1 security headers (CSP, frame-ancestors, Permissions-Policy)

**File:** `deploy/Caddyfile:8-13`
**Issue:** No `Content-Security-Policy` or `X-Frame-Options`/`frame-ancestors` header is sent on the web app or the API. ASVS 4.0.3 14.4.3 (CSP) and 14.4.7 (frame embedding) are Level 1 requirements, and the project states ASVS L1 enforcement. Without frame-ancestors the site can be framed for clickjacking. That will matter once save, skip or delete-account buttons exist.
**Fix:**
```caddy
header {
	Strict-Transport-Security "max-age=31536000"
	X-Content-Type-Options "nosniff"
	Referrer-Policy "strict-origin-when-cross-origin"
	X-Frame-Options "DENY"
	Permissions-Policy "camera=(), microphone=(), geolocation=()"
	Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
	-Server
}
```
Swagger UI at `/api/docs` loads its assets from a CDN, so either loosen the CSP for `/api/docs*` or self-host the assets. A nonce-based CSP through Next middleware is better long term.

### WR-06: CD falls back to `ssh-keyscan` trust-on-first-use instead of failing closed

**File:** `.github/workflows/ci.yml:203-208`
**Issue:** If `DEPLOY_KNOWN_HOSTS` is unset or deleted, the deploy job trusts whatever host key answers at `DEPLOY_HOST`. It only prints a warning that nobody reads on a green run. A MITM or DNS/IP takeover (Azure public IPs can be reassigned) would then receive the deploy commands and files. The `gate` job does not require the variable either.
**Fix:** Fail closed. Add `KNOWN := ${{ secrets.DEPLOY_KNOWN_HOSTS }}` to the gate condition, or in the SSH step use `[ -n "$KNOWN_HOSTS" ] || { echo "::error::DEPLOY_KNOWN_HOSTS required"; exit 1; }`, and add `-o StrictHostKeyChecking=yes` to the `scp`/`ssh` calls.

### WR-07: `FORWARDED_ALLOW_IPS=*` is baked into the API image

**File:** `backend/Dockerfile:34` (also `.env.example`)
**Issue:** With `--proxy-headers` and `FORWARDED_ALLOW_IPS=*`, uvicorn trusts `X-Forwarded-For` and `X-Forwarded-Proto` from any peer. In prod this is fine today only because Caddy overwrites untrusted XFF and the api has no published port. But the setting ships in the image, so it also applies in dev (`127.0.0.1:8000` is published, so any local process can spoof), in CI and in any future topology. The planned Postgres rate limiter and audit logs will key on `request.client.host`, which a client can then spoof to dodge limits.
**Fix:** Remove it from the Dockerfile. Set it only in `compose.prod.yaml` to Caddy's address (a fixed IP on a user-defined network or subnet), for example `FORWARDED_ALLOW_IPS: "172.30.0.0/24"`. Leave it unset in dev.

### WR-08: `test_worker` can hang indefinitely (blocking `readline` ignores the deadline)

**File:** `backend/tests/test_worker.py:30-38`
**Issue:** The 3 s deadline is only checked between lines. `proc.stdout.readline()` blocks until output arrives, so a worker that hangs before logging (an import stall, or a settings validation path that waits) hangs the test run until the CI job timeout (20 min). No `pytest-timeout` is configured.
**Fix:** Read on a thread with a timeout, or use `selectors`/`select.select([proc.stdout], [], [], remaining)` before each `readline()`. Alternatively, add `pytest-timeout` with `timeout = 60` in `[tool.pytest.ini_options]`.

## Info

### IN-01: Readiness and API docs leak internals publicly

**File:** `backend/ranker/api/health.py:35-36,49`; `backend/ranker/main.py:43-46`
**Issue:** Unauthenticated callers see exception class names, the exact pgvector version, and the full OpenAPI schema and Swagger UI at `/api/docs`. That is low risk now, but it is free reconnaissance.
**Fix:** Return the detailed `checks` only to internal callers, or behind an `APP_ENV != production` check. Consider disabling `docs_url` in production.

### IN-02: `app_env` setting is never read

**File:** `backend/ranker/settings.py:13`
**Issue:** This is dead config that suggests behaviour which does not exist (see WR-02).
**Fix:** Use it for guardrails and docs toggles, or remove it.

### IN-03: Stray debug output in `check-prod-ports.sh`

**File:** `deploy/check-prod-ports.sh:29`
**Issue:** `echo "$owners"` prints `caddy` again after the OK line.
**Fix:** Remove the line.

### IN-04: uv toolchain mismatch between Docker and CI

**File:** `backend/Dockerfile:3`; `.github/workflows/ci.yml:29`; `backend/pyproject.toml` `[build-system]`
**Issue:** The image uses uv 0.13.9. CI uses 0.9.18 and `uv_build>=0.9.18,<0.10`. `uv sync --locked` validates the lock with different uv versions in the two places, and CLAUDE.md asks for 0.13.x. A lockfile-format change could break only the image build, or only CI.
**Fix:** Pin one uv version everywhere and bump the `uv_build` range to match.

### IN-05: Unpinned model revision and base-image digests

**File:** `backend/Dockerfile:2,17,20`; `web/Dockerfile:4,15`
**Issue:** `TextEmbedding('BAAI/bge-small-en-v1.5')` downloads whatever the HF repo serves at build time, and the `python:3.14-slim-trixie` and Node tags float. Embeddings stored in Phase 2 must come from identical weights, or vector search quality drifts silently after a rebuild.
**Fix:** Pin the HF revision or checksum the downloaded ONNX file in the build, and pin images by digest (Dependabot can bump them).

### IN-06: 500 responses from unhandled exceptions carry no `X-Request-ID`

**File:** `backend/ranker/main.py:48-49`
**Issue:** Starlette's `ServerErrorMiddleware` sits outside all user middleware. It builds the 500 response and sends it with the original `send`, so `CorrelationIdMiddleware` never adds the header. Users cannot quote an ID for the errors that matter most.
**Fix:** Add a catch-all exception handler that returns JSON with `correlation_id.get()`, or wrap the app in a pure ASGI middleware outside `ServerErrorMiddleware`.

### IN-07: Only the amd64 image is self-checked; pushed images come from a second build

**File:** `.github/workflows/ci.yml:117-141`
**Issue:** The selfcheck runs on the `load: true` amd64 build. The push step rebuilds for `vars.PLATFORMS`. If `PLATFORMS` adds arm64 (as bootstrap suggests for Oracle), the arm64 image ships untested.
**Fix:** Run the selfcheck per platform with QEMU, or push by digest from the tested build.

### IN-08: Production services have no memory limits

**File:** `deploy/compose.prod.yaml`
**Issue:** On a small VM a runaway api or ONNX process can trigger the host OOM killer, which may choose Postgres.
**Fix:** Add `mem_limit` (or `deploy.resources.limits.memory`) per service, based on the numbers in `docs/decisions/0000-hosting-and-memory.md`.

### IN-09: `env_file: .env` hands every host secret to api and worker

**File:** `deploy/compose.prod.yaml:22,38`
**Issue:** `POSTGRES_PASSWORD`, `ACME_EMAIL`, `GHCR_OWNER` and others end up in the app containers' environment. The app only needs `DATABASE_URL` (which already contains the password) plus its own settings.
**Fix:** List the needed variables under an explicit `environment:` block.

### IN-10: No automatic rollback when the post-deploy smoke test fails; root SSH still allowed

**File:** `.github/workflows/ci.yml:216`; `deploy/bootstrap.sh:72-76`
**Issue:** A failing smoke test leaves the broken release live. Rollback is manual and relies on WR-01 bookkeeping. `PermitRootLogin prohibit-password` is an open follow-up.
**Fix:** On smoke failure, ssh back in and run `IMAGE_TAG=$PREVIOUS ./deploy.sh` (as long as migrations stay backward compatible). Set `PermitRootLogin no`.

---

_Reviewed: 2026-11-22T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
