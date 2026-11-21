"""Stub worker: heartbeat loop with clean SIGTERM/SIGINT shutdown (D-08)."""

import asyncio
import signal

import structlog

from ranker.observability import configure_logging
from ranker.settings import get_settings


async def run(stop: asyncio.Event | None = None) -> None:
    log = structlog.get_logger("ranker.worker")
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # PID 1 ignores SIGTERM without a handler
    heartbeat = get_settings().worker_heartbeat_seconds
    log.info("worker_started")
    # Phase 2: start the APScheduler AsyncIOScheduler (apscheduler<4) and the tasks
    # consumer here, and shut them down after the loop below.
    while not stop.is_set():
        log.info("worker_heartbeat")
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat)
        except TimeoutError:
            pass
    log.info("worker_stopped")


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
