"""Worker: heartbeat, task consumer and leader-locked scheduler (D-08, OPS-02)."""

import asyncio
import os
import signal
import socket

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ranker.db.engine import create_engine_from_settings
from ranker.observability import configure_logging
from ranker.settings import get_settings
from ranker.tasks.consumer import consume_tasks
from ranker.tasks.handlers import build_handlers
from ranker.worker.scheduler import build_scheduler, register_core_jobs, try_acquire_leader


async def run(stop: asyncio.Event | None = None) -> None:
    log = structlog.get_logger("ranker.worker")
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # PID 1 ignores SIGTERM without a handler
    settings = get_settings()
    heartbeat = settings.worker_heartbeat_seconds
    engine = create_engine_from_settings(settings)
    log.info("worker_started")

    consumer = asyncio.create_task(
        consume_tasks(
            engine,
            stop,
            build_handlers(settings, engine),
            worker_id=f"{socket.gethostname()}:{os.getpid()}",
            poll_seconds=settings.task_poll_seconds,
        )
    )
    log.info("task_consumer_started")

    lock_conn: AsyncConnection | None = None
    scheduler: AsyncIOScheduler | None = None
    announced_follower = False

    async def drop_leadership() -> None:
        nonlocal lock_conn, scheduler
        if scheduler is not None:
            scheduler.shutdown(wait=False)  # pyright: ignore[reportUnknownMemberType]
            scheduler = None
        if lock_conn is not None:
            try:
                await lock_conn.close()
            except Exception as exc:
                log.warning("db_unavailable", error_type=type(exc).__name__)
            lock_conn = None

    while not stop.is_set():
        log.info("worker_heartbeat")
        try:
            if lock_conn is None:
                lock_conn = await try_acquire_leader(engine)
                if lock_conn is not None:
                    scheduler = build_scheduler()
                    register_core_jobs(scheduler, engine, settings)
                    scheduler.start()  # pyright: ignore[reportUnknownMemberType]
                    announced_follower = False
                    log.info("scheduler_lock_acquired")
                elif not announced_follower:
                    announced_follower = True
                    log.info("scheduler_lock_held_elsewhere")
            else:
                await lock_conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("db_unavailable", error_type=type(exc).__name__)
            await drop_leadership()
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat)
        except TimeoutError:
            pass

    await drop_leadership()
    await consumer
    await engine.dispose()
    log.info("worker_stopped")


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
