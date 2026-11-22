# Ranker

**Live:** https://topfit.duckdns.org

Describe what you want (by resume or conversation) and get a short list of real, currently open jobs ranked well for you, with honest reasons. Hybrid search and ranking over a fresh index keep working even when the LLM is unavailable.

Status: Phase 1, walking skeleton (API, Postgres + pgvector, embedding model, web status page).

## Architecture

```
browser -> Caddy (single origin, HTTPS) -> web (Next.js)  /
                                        -> api (FastAPI)  /api/*
api, worker -> Postgres 18 + pgvector 0.8.7
worker: scheduled jobs (APScheduler), separate process
```

## Run locally

```
make dev
```

Then open http://localhost:3000. The first build downloads the ~67 MB embedding model into the image.

## Native path

```
make db
cd backend && uv sync
DATABASE_URL=postgresql+psycopg://ranker:ranker@localhost:5432/ranker SESSION_SECRET=dev COOKIE_SECURE=false uv run alembic upgrade head
DATABASE_URL=postgresql+psycopg://ranker:ranker@localhost:5432/ranker SESSION_SECRET=dev COOKIE_SECURE=false uv run uvicorn ranker.main:app --reload
# in web/
API_PROXY_TARGET=http://localhost:8000 npm run dev
```

## Checks

```
make test lint typecheck web-check selfcheck
```

`make build` must run before `make selfcheck`. Optional hooks: `pre-commit install`.

## Ingestion quality

`normalize_job` is scored against 78 real postings (Greenhouse, Ashby, Lever, Arbeitnow, RemoteOK, Remotive, Jobicy) that Claude labelled from the posting text before any scoring run; none are owner-reviewed yet. Only 10 of them are in-scope tech roles, so the field numbers are a smoke test, not precision estimates: scope accuracy 0.91 (n=70), remote type 0.89, seniority 0.89, employment type 1.00, salary 1.00 (coverage 0.22), remote eligibility 0.67, industry 0.44 (n=9 each). CI fails if any field drops below its recorded floor. Method, error patterns and gaps (Internshala returned a bot challenge, Adzuna has no keys): [docs/evals/ingest-normalisation.md](docs/evals/ingest-normalisation.md). Run it with `cd backend && uv run python -m ranker.cli eval ingest --markdown`; `ranker runs report` prints the per-run ingest counters.

## Deploy

Every push to `main` deploys automatically: CI builds and tests, pushes images to GHCR, then SSHes to the server to run migrations, start the stack and smoke-test `https://<host>/api/readyz`. Manual fallback: `make deploy DEPLOY_HOST=<ip> PUBLIC_HOSTNAME=<host>`. First-time server setup on any Ubuntu 24.04 VM: `deploy/bootstrap.sh`. Hosting choice, memory and cold-start numbers: [docs/decisions/0000-hosting-and-memory.md](docs/decisions/0000-hosting-and-memory.md).

## Environment variables

All variables are documented in `.env.example`. Never commit real secrets.
