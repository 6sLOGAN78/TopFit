"""Leader election (pg advisory lock) and APScheduler factory. Worker-only module."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ranker.settings import Settings
from ranker.tasks.queue import reap_expired_leases

SCHEDULER_LOCK_KEY = 727274


async def try_acquire_leader(engine: AsyncEngine) -> AsyncConnection | None:
    """Take the session-level scheduler lock; the returned connection must stay open."""
    conn = await engine.connect()
    try:
        got = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY})
        ).scalar()
    except BaseException:
        await conn.close()
        raise
    if got:
        return conn
    await conn.close()
    return None


def build_scheduler() -> AsyncIOScheduler:
    # misfire_grace_time defaults to 1 s, which silently skips jobs on a busy loop.
    return AsyncIOScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )


def register_core_jobs(
    scheduler: AsyncIOScheduler, engine: AsyncEngine, settings: Settings
) -> None:
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        reap_expired_leases,
        "interval",
        minutes=1,
        args=[engine, settings.task_lease_seconds],
        id="reap_leases",
        replace_existing=True,
    )
