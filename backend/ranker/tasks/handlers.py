"""Task handler registry. Later plans register enrich_job, ingest, expire_ttl, ..."""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.settings import Settings
from ranker.tasks.queue import Task

Handler = Callable[[Task], Awaitable[None]]


def build_handlers(settings: Settings, engine: AsyncEngine) -> dict[str, Handler]:
    del settings, engine
    return {}
