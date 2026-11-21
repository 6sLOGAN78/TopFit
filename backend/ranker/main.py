"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

import anyio.to_thread
import structlog
from fastapi import FastAPI

from ranker.api import api_router
from ranker.db import create_engine_from_settings
from ranker.embeddings import Embedder, build_embedder
from ranker.settings import get_settings

log = structlog.get_logger("ranker.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.engine = create_engine_from_settings(settings)
    embedder: Embedder | None
    try:
        embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
    except Exception as exc:  # keep healthz alive; readyz reports model=false
        log.error("embedder_load_failed", error_type=type(exc).__name__)
        embedder = None
    app.state.embedder = embedder
    try:
        yield
    finally:
        await app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
