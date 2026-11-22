"""Task handler registry: enrich_job, enrich_backlog, ingest, expire_ttl."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from typing import Literal

import anyio.to_thread
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import Embedder
from ranker.settings import Settings
from ranker.tasks.queue import Task

Handler = Callable[[Task], Awaitable[None]]

log = structlog.get_logger("ranker.tasks.handlers")


def make_ingest_handler(settings: Settings, engine: AsyncEngine) -> Handler:
    from ranker.embeddings.embedder import build_embedder
    from ranker.ingest.pipeline import (
        RunQuotaReached,
        SourceLocked,
        SourceNotConfigured,
        run_ingest,
    )
    from ranker.ingest.sources import UnknownSource

    embedder: Embedder | None = None
    lock = asyncio.Lock()

    async def shared_embedder() -> Embedder:
        nonlocal embedder
        async with lock:
            if embedder is None:
                embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
            return embedder

    async def handler(task: Task) -> None:
        source = task.payload.get("source")
        mode = task.payload.get("mode", "incremental")
        if not isinstance(source, str) or mode not in ("incremental", "backfill"):
            raise ValueError("invalid ingest payload")
        run_mode: Literal["incremental", "backfill"] = (
            "backfill" if mode == "backfill" else "incremental"
        )
        try:
            await run_ingest(
                engine, settings, source, mode=run_mode, embedder=await shared_embedder()
            )
        except (SourceLocked, RunQuotaReached, SourceNotConfigured) as exc:
            log.info("ingest_skipped", source=source, reason=type(exc).__name__)
        except UnknownSource:
            log.warning("ingest_skipped", source=source, reason="UnknownSource")

    return handler


def make_expire_ttl_handler(engine: AsyncEngine) -> Handler:
    from ranker.config_loader import load_config
    from ranker.ingest.expiry import ExpiryConfig, expire_ttl
    from ranker.ingest.sources import SourcesConfig

    async def handler(task: Task) -> None:
        source = task.payload.get("source")
        if not isinstance(source, str):
            raise ValueError("invalid expire_ttl payload")
        section = load_config("sources.yaml", SourcesConfig).root.get(source)
        if section is None or section.expiry_policy != "ttl":
            log.info("expire_ttl_not_applicable", source=source)
            return
        expired = await expire_ttl(
            engine,
            source=source,
            cfg=ExpiryConfig.model_validate(section.model_dump()),
            now=datetime.now(UTC),
        )
        log.info("expire_ttl_done", source=source, expired=expired)

    return handler


def build_handlers(settings: Settings, engine: AsyncEngine) -> dict[str, Handler]:
    from ranker.ingest.enrich import make_backlog_handler, make_enrich_handler
    from ranker.llm.client import build_gateway

    gateway = build_gateway(engine, settings)
    return {
        "enrich_job": make_enrich_handler(engine, gateway),
        "enrich_backlog": make_backlog_handler(engine),
        "ingest": make_ingest_handler(settings, engine),
        "expire_ttl": make_expire_ttl_handler(engine),
    }
