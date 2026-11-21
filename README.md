# Ranker

<!-- LIVE_URL -->

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

## Deploy

See `deploy/` (bootstrap, deploy and smoke scripts). Manual fallback: `make deploy DEPLOY_HOST=<host>`. Details are filled in later in Phase 1.

## Environment variables

All variables are documented in `.env.example`. Never commit real secrets.
