"""Task consumer loop: claim, dispatch by kind, record outcome."""

import asyncio
from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.tasks import queue
from ranker.tasks.handlers import Handler

log = structlog.get_logger("ranker.tasks")


async def _process(engine: AsyncEngine, handlers: Mapping[str, Handler], task: queue.Task) -> None:
    handler = handlers.get(task.kind)
    if handler is None:
        await queue.mark_dead(engine, task, "unknown_kind")
        log.warning("task_failed", task_id=task.id, kind=task.kind, error_type="unknown_kind")
        return
    try:
        await handler(task)
    except queue.TaskDeferred as exc:
        await queue.defer(engine, task, exc.delay_seconds)
        log.info("task_deferred", task_id=task.id, kind=task.kind, delay=exc.delay_seconds)
    except Exception as exc:
        await queue.fail(engine, task, type(exc).__name__)
        log.warning("task_failed", task_id=task.id, kind=task.kind, error_type=type(exc).__name__)
    else:
        await queue.complete(engine, task.id)
        log.info("task_done", task_id=task.id, kind=task.kind)


async def consume_tasks(
    engine: AsyncEngine,
    stop: asyncio.Event,
    handlers: Mapping[str, Handler],
    *,
    worker_id: str,
    poll_seconds: float,
) -> None:
    while not stop.is_set():
        try:
            claimed = await queue.claim(engine, worker_id)
            if claimed:
                await _process(engine, handlers, claimed[0])
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("db_unavailable", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass
