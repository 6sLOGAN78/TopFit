import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest import pipeline
from ranker.ingest.pipeline import RunQuotaReached, SourceLocked
from ranker.settings import Settings, get_settings
from ranker.tasks import queue
from ranker.tasks.consumer import consume_tasks
from ranker.tasks.handlers import Handler, build_handlers, make_ingest_handler
from ranker.tasks.queue import TaskDeferred

pytestmark = pytest.mark.usefixtures("clean_db")

FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
URL = "https://remotive.com/api/remote-jobs"


def _settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


async def _defer_enrich(task: queue.Task) -> None:
    raise TaskDeferred(3600)


async def _consume_until_done(engine: AsyncEngine, handlers: dict[str, Handler]) -> None:
    stop = asyncio.Event()
    t = asyncio.create_task(consume_tasks(engine, stop, handlers, worker_id="w", poll_seconds=0.05))
    for _ in range(200):
        async with engine.connect() as conn:
            n = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM tasks WHERE kind IN ('ingest','expire_ttl') "
                        "AND status IN ('queued','running')"
                    )
                )
            ).scalar_one()
        if n == 0:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await t


@respx.mock
async def test_ingest_task_runs_and_queues_enrichment(engine: AsyncEngine) -> None:
    settings = _settings()
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text()))
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))
    await queue.enqueue(
        engine,
        "ingest",
        {"source": "remotive", "mode": "incremental"},
        dedupe_key="ingest:remotive",
    )
    handlers = {"ingest": make_ingest_handler(settings, engine), "enrich_job": _defer_enrich}
    await _consume_until_done(engine, handlers)
    async with engine.connect() as conn:
        status = (await conn.execute(text("SELECT status FROM ingest_runs"))).scalar_one()
        done = (
            await conn.execute(
                text("SELECT count(*) FROM tasks WHERE kind='ingest' AND status='done'")
            )
        ).scalar_one()
        embedded = (
            await conn.execute(text("SELECT count(*) FROM jobs WHERE embedding IS NOT NULL"))
        ).scalar_one()
        enrich = (
            await conn.execute(text("SELECT count(*) FROM tasks WHERE kind='enrich_job'"))
        ).scalar_one()
    assert status == "succeeded"
    assert done == 1
    assert embedded > 0
    assert enrich > 0


@respx.mock
async def test_summary_reports_enrich_enqueued(engine: AsyncEngine) -> None:
    settings = _settings()
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text()))
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))
    summary = await pipeline.run_ingest(engine, settings, "remotive")
    assert summary.enrich_enqueued > 0


@respx.mock
async def test_enqueue_failure_does_not_change_run(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text()))
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("nope")

    monkeypatch.setattr(pipeline, "enqueue_enrichment", boom)
    with structlog.testing.capture_logs() as logs:
        summary = await pipeline.run_ingest(engine, settings, "remotive")
    assert summary.status == "succeeded"
    assert any(e["event"] == "enrich_enqueue_failed" for e in logs)
    async with engine.connect() as conn:
        status = (await conn.execute(text("SELECT status FROM ingest_runs"))).scalar_one()
    assert status == "succeeded"


@pytest.mark.parametrize("exc", [SourceLocked("remotive"), RunQuotaReached("remotive")])
async def test_skips_are_not_failures(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    async def raiser(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(pipeline, "run_ingest", raiser)
    handler = make_ingest_handler(_settings(), engine)
    task = queue.Task(
        id=1, kind="ingest", payload={"source": "remotive"}, attempts=1, max_attempts=5
    )
    with structlog.testing.capture_logs() as logs:
        await handler(task)
    skipped = [e for e in logs if e["event"] == "ingest_skipped"]
    assert skipped
    assert skipped[0]["reason"] == type(exc).__name__


async def test_not_configured_adzuna_is_skipped(engine: AsyncEngine) -> None:
    settings = _settings().model_copy(update={"adzuna_app_id": None, "adzuna_app_key": None})
    handler = make_ingest_handler(settings, engine)
    task = queue.Task(id=1, kind="ingest", payload={"source": "adzuna"}, attempts=1, max_attempts=5)
    with structlog.testing.capture_logs() as logs:
        await handler(task)
    assert any(
        e["event"] == "ingest_skipped" and e["reason"] == "SourceNotConfigured" for e in logs
    )


async def test_bad_payload_raises(engine: AsyncEngine) -> None:
    handler = make_ingest_handler(_settings(), engine)
    task = queue.Task(id=1, kind="ingest", payload={"source": 5}, attempts=1, max_attempts=5)
    with pytest.raises(ValueError):
        await handler(task)


async def test_expire_ttl_task(engine: AsyncEngine) -> None:
    handlers = build_handlers(_settings(), engine)
    ttl = queue.Task(
        id=1, kind="expire_ttl", payload={"source": "adzuna"}, attempts=1, max_attempts=5
    )
    with structlog.testing.capture_logs() as logs:
        await handlers["expire_ttl"](ttl)
    assert any(e["event"] == "expire_ttl_done" for e in logs)
    absence = queue.Task(
        id=2, kind="expire_ttl", payload={"source": "remotive"}, attempts=1, max_attempts=5
    )
    with structlog.testing.capture_logs() as logs:
        await handlers["expire_ttl"](absence)
    assert any(e["event"] == "expire_ttl_not_applicable" for e in logs)


async def test_boards_recheck_handler_is_registered(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ranker.ingest import boards

    called: list[int] = []

    async def fake(eng: AsyncEngine, settings: object) -> boards.DiscoveryResult:
        called.append(1)
        return boards.DiscoveryResult()

    monkeypatch.setattr(boards, "run_board_maintenance", fake)  # before build: imported there
    handlers = build_handlers(_settings(), engine)
    assert "boards_recheck" in handlers
    task = queue.Task(id=3, kind="boards_recheck", payload={}, attempts=1, max_attempts=5)
    await handlers["boards_recheck"](task)
    assert called == [1]
