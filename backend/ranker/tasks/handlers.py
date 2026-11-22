"""Task handler registry. Later plans register enrich_job, ingest, expire_ttl, ..."""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.settings import Settings
from ranker.tasks.queue import Task

Handler = Callable[[Task], Awaitable[None]]


def build_handlers(settings: Settings, engine: AsyncEngine) -> dict[str, Handler]:
    from ranker.ingest.enrich import make_backlog_handler, make_enrich_handler
    from ranker.llm.client import build_gateway

    gateway = build_gateway(engine, settings)
    return {
        "enrich_job": make_enrich_handler(engine, gateway),
        "enrich_backlog": make_backlog_handler(engine),
    }
