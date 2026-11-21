import asyncio
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.tasks import queue
from ranker.tasks.consumer import consume_tasks
from ranker.tasks.queue import Task, TaskDeferred

pytestmark = pytest.mark.usefixtures("clean_db")


async def _row(engine: AsyncEngine, task_id: int) -> dict[str, object]:
    async with engine.connect() as conn:
        r = await conn.execute(
            text(
                "SELECT status, attempts, last_error, "
                "EXTRACT(EPOCH FROM (run_after - now())) AS ahead FROM tasks WHERE id = :i"
            ),
            {"i": task_id},
        )
        return dict(r.mappings().one())


async def _run_until_idle(
    engine: AsyncEngine, handlers: dict[str, Callable[[Task], Awaitable[None]]], n: int = 1
) -> None:
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(
            consume_tasks(engine, stop, handlers, worker_id=f"w{i}", poll_seconds=0.05)
        )
        for i in range(n)
    ]
    for _ in range(200):
        async with engine.connect() as conn:
            pending = (
                await conn.execute(
                    text("SELECT count(*) FROM tasks WHERE status IN ('queued','running')")
                )
            ).scalar_one()
        if pending == 0:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.gather(*tasks)


async def test_enqueue_dedupes_pending(engine: AsyncEngine) -> None:
    first = await queue.enqueue(engine, "x", {"a": 1}, dedupe_key="k")
    assert first is not None
    assert await queue.enqueue(engine, "x", {"a": 1}, dedupe_key="k") is None
    await queue.complete(engine, first)
    assert await queue.enqueue(engine, "x", {"a": 1}, dedupe_key="k") is not None


async def test_two_consumers_process_each_task_exactly_once(engine: AsyncEngine) -> None:
    ids = [await queue.enqueue(engine, "x", {"i": i}) for i in range(100)]
    seen: list[int] = []

    async def handler(task: Task) -> None:
        seen.append(task.id)
        await asyncio.sleep(0)

    await _run_until_idle(engine, {"x": handler}, n=2)
    assert len(seen) == 100
    assert set(seen) == set(ids)
    async with engine.connect() as conn:
        total = (await conn.execute(text("SELECT SUM(attempts) FROM tasks"))).scalar_one()
        done = (
            await conn.execute(text("SELECT count(*) FROM tasks WHERE status='done'"))
        ).scalar_one()
    assert total == 100
    assert done == 100


async def test_failure_backs_off_then_dies(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "x", {}, max_attempts=2)
    assert tid is not None
    [t] = await queue.claim(engine, "w")
    await queue.fail(engine, t, "ValueError")
    row = await _row(engine, tid)
    assert row["status"] == "queued"
    assert row["last_error"] == "ValueError"
    assert 50 < float(row["ahead"]) < 70  # 2^1 * 30 s plus <=10% jitter
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET run_after = now()"))
    [t] = await queue.claim(engine, "w")
    assert t.attempts == 2
    await queue.fail(engine, t, "ValueError")
    assert (await _row(engine, tid))["status"] == "dead"


async def test_handler_exception_records_class_only(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "x", {"secret": "pii"})
    assert tid is not None

    async def boom(task: Task) -> None:
        raise ValueError("pii in message")

    stop = asyncio.Event()
    c = asyncio.create_task(
        consume_tasks(engine, stop, {"x": boom}, worker_id="w", poll_seconds=0.05)
    )
    await asyncio.sleep(0.5)
    stop.set()
    await c
    row = await _row(engine, tid)
    assert row["last_error"] == "ValueError"
    assert row["status"] == "queued"


async def test_deferral_does_not_consume_attempts(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "x", {})
    assert tid is not None
    for _ in range(10):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE tasks SET run_after = now()"))
        [t] = await queue.claim(engine, "w")
        await queue.defer(engine, t, 3600)
    row = await _row(engine, tid)
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert float(row["ahead"]) > 3500


async def test_deferral_via_consumer(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "x", {})
    assert tid is not None

    async def handler(task: Task) -> None:
        raise TaskDeferred(3600)

    stop = asyncio.Event()
    c = asyncio.create_task(
        consume_tasks(engine, stop, {"x": handler}, worker_id="w", poll_seconds=0.05)
    )
    await asyncio.sleep(0.5)
    stop.set()
    await c
    row = await _row(engine, tid)
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert float(row["ahead"]) > 3500


async def test_reaper_requeues_expired_leases(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "x", {})
    assert tid is not None
    await queue.claim(engine, "w")
    assert await queue.reap_expired_leases(engine, 1800) == 0
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE tasks SET locked_at = now() - interval '1 hour'"))
    assert await queue.reap_expired_leases(engine, 1800) == 1
    assert (await _row(engine, tid))["status"] == "queued"


async def test_unknown_kind_dies_immediately(engine: AsyncEngine) -> None:
    tid = await queue.enqueue(engine, "nope", {})
    assert tid is not None
    await _run_until_idle(engine, {})
    row = await _row(engine, tid)
    assert row["status"] == "dead"
    assert row["last_error"] == "unknown_kind"
