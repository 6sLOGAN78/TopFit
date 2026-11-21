import asyncio
import io
import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from ranker.observability import configure_logging
from ranker.settings import get_settings
from ranker.worker.__main__ import run

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    structlog.reset_defaults()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    get_settings.cache_clear()


async def _wait_for(buf: io.StringIO, needle: str, count: int = 1, limit: float = 10) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while buf.getvalue().count(needle) < count:
        assert asyncio.get_running_loop().time() < deadline, f"timeout waiting for {needle}"
        await asyncio.sleep(0.05)


async def test_exactly_one_scheduler_and_handover(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "0.2")
    get_settings.cache_clear()
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)

    stop_a, stop_b = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(run(stop_a))
    await _wait_for(buf, "scheduler_lock_acquired")
    b = asyncio.create_task(run(stop_b))
    await _wait_for(buf, "scheduler_lock_held_elsewhere")
    await _wait_for(buf, "task_consumer_started", count=2)
    assert buf.getvalue().count("scheduler_lock_acquired") == 1

    stop_a.set()
    await a
    await _wait_for(buf, "scheduler_lock_acquired", count=2)
    stop_b.set()
    await b


def test_api_import_does_not_load_apscheduler() -> None:
    code = "import sys, ranker.main; sys.exit(1 if 'apscheduler' in sys.modules else 0)"
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, check=False, env=_env())
    assert r.returncode == 0


def _env() -> dict[str, str]:
    import os

    return {**os.environ, "DATABASE_URL": "postgresql+psycopg://x:y@127.0.0.1:1/none"}
