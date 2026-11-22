"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

import anyio.to_thread
import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI

from ranker.api import api_router
from ranker.api.health import embedding_selfcheck
from ranker.db import create_engine_from_settings
from ranker.embeddings import Embedder, build_embedder
from ranker.llm.client import LLMGateway, build_gateway
from ranker.observability import RequestLogMiddleware, configure_logging
from ranker.settings import get_settings

log = structlog.get_logger("ranker.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.engine = create_engine_from_settings(settings)
    embedder: Embedder | None
    try:
        embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
    except Exception as exc:  # keep healthz alive; readyz reports model=false
        log.error("embedder_load_failed", error_type=type(exc).__name__)
        embedder = None
    app.state.embedder = embedder
    gateway: LLMGateway | None
    try:
        gateway = build_gateway(app.state.engine, settings)
    except Exception as exc:  # search degrades without the LLM; never fail startup
        log.error("gateway_init_failed", error_type=type(exc).__name__)
        gateway = None
    app.state.gateway = gateway
    # One inference at startup; readyz reports this cached result (CR-01).
    app.state.embedding_check = await embedding_selfcheck(embedder)
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
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)  # last = outermost, request id set first
    return app


app = create_app()
