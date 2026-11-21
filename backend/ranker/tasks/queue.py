"""Postgres task queue: SKIP LOCKED claims, backoff, deferral, lease reaper (FND-07)."""

import json
import random
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_RNG = random.SystemRandom()
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 6 * 3600


class TaskDeferred(Exception):
    """Raised by a handler to re-queue without consuming an attempt."""

    def __init__(self, delay_seconds: float) -> None:
        super().__init__(f"deferred {delay_seconds}s")
        self.delay_seconds = delay_seconds


@dataclass(frozen=True)
class Task:
    id: int
    kind: str
    payload: dict[str, object]
    attempts: int
    max_attempts: int


def backoff_seconds(attempts: int) -> float:
    base = min(_BACKOFF_BASE_SECONDS * (2**attempts), _BACKOFF_CAP_SECONDS)
    return base * (1 + _RNG.uniform(0, 0.1))


async def enqueue(
    engine: AsyncEngine,
    kind: str,
    payload: dict[str, object],
    *,
    dedupe_key: str | None = None,
    delay_seconds: float = 0,
    max_attempts: int = 5,
) -> int | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO tasks (kind, payload, dedupe_key, max_attempts, run_after) "
                    "VALUES (:kind, CAST(:payload AS jsonb), :dedupe_key, :max_attempts, "
                    "now() + make_interval(secs => :delay)) "
                    "ON CONFLICT (dedupe_key) WHERE status IN ('queued','running') DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "kind": kind,
                    "payload": json.dumps(payload),
                    "dedupe_key": dedupe_key,
                    "max_attempts": max_attempts,
                    "delay": delay_seconds,
                },
            )
        ).first()
    return None if row is None else int(row[0])


async def claim(engine: AsyncEngine, worker_id: str, limit: int = 1) -> list[Task]:
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "WITH c AS (SELECT id FROM tasks WHERE status = 'queued' "
                    "AND run_after <= now() ORDER BY run_after, id "
                    "FOR UPDATE SKIP LOCKED LIMIT :n) "
                    "UPDATE tasks t SET status = 'running', attempts = t.attempts + 1, "
                    "locked_at = now(), locked_by = :me, updated_at = now() "
                    "FROM c WHERE t.id = c.id "
                    "RETURNING t.id, t.kind, t.payload, t.attempts, t.max_attempts"
                ),
                {"n": limit, "me": worker_id},
            )
        ).all()
    return [
        Task(
            id=int(r[0]),
            kind=str(r[1]),
            payload=dict(r[2]),
            attempts=int(r[3]),
            max_attempts=int(r[4]),
        )
        for r in rows
    ]


async def complete(engine: AsyncEngine, task_id: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET status = 'done', locked_at = NULL, locked_by = NULL, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": task_id},
        )


async def fail(engine: AsyncEngine, task: Task, error_type: str) -> None:
    dead = task.attempts >= task.max_attempts
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET status = CASE WHEN :dead THEN 'dead' ELSE 'queued' END, "
                "run_after = now() + make_interval(secs => :delay), last_error = :err, "
                "locked_at = NULL, locked_by = NULL, updated_at = now() WHERE id = :id"
            ),
            {
                "id": task.id,
                "dead": dead,
                "delay": 0 if dead else backoff_seconds(task.attempts),
                "err": error_type,
            },
        )


async def mark_dead(engine: AsyncEngine, task: Task, reason: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET status = 'dead', last_error = :err, locked_at = NULL, "
                "locked_by = NULL, updated_at = now() WHERE id = :id"
            ),
            {"id": task.id, "err": reason},
        )


async def defer(engine: AsyncEngine, task: Task, delay_seconds: float) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET status = 'queued', attempts = GREATEST(attempts - 1, 0), "
                "run_after = now() + make_interval(secs => :delay), locked_at = NULL, "
                "locked_by = NULL, updated_at = now() WHERE id = :id"
            ),
            {"id": task.id, "delay": delay_seconds},
        )


async def reap_expired_leases(engine: AsyncEngine, lease_seconds: int) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE tasks SET status = 'queued', locked_at = NULL, locked_by = NULL, "
                "updated_at = now() WHERE status = 'running' "
                "AND locked_at < now() - make_interval(secs => :lease)"
            ),
            {"lease": lease_seconds},
        )
    return result.rowcount
