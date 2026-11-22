"""Leader election (pg advisory lock) and APScheduler factory. Worker-only module."""

from datetime import time

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from apscheduler.triggers.cron import CronTrigger  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ranker.config_loader import ConfigError, load_config
from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    SourcesConfig,
    get_adapter_factory,
    registered_sources,
)
from ranker.settings import Settings
from ranker.tasks.queue import enqueue, reap_expired_leases

log = structlog.get_logger("ranker.worker.scheduler")

SCHEDULER_LOCK_KEY = 727274

# D-17 morning read: each source's `batch: morning` run must start in this UTC window
# (wrapping midnight) so the whole batch finishes before ~01:00 UTC (06:30 IST).
MORNING_START = time(21, 30)  # 03:00 IST
MORNING_LATEST_START = time(0, 0)  # 05:30 IST
TTL_SWEEP = (0, 30)  # 06:00 IST, after the morning batch
ENRICH_BACKLOG = (1, 30)  # 07:00 IST
ABANDONED_AFTER_HOURS = 2


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


def _in_morning_window(hour: int, minute: int) -> bool:
    start = MORNING_START.hour * 60 + MORNING_START.minute
    latest = MORNING_LATEST_START.hour * 60 + MORNING_LATEST_START.minute + 24 * 60
    t = hour * 60 + minute
    if t < start:
        t += 24 * 60
    return start <= t <= latest


def _validate_source(name: str, cfg: BaseSourceConfig) -> None:
    if cfg.max_runs_per_day is not None and len(cfg.schedule) > cfg.max_runs_per_day:
        raise ConfigError("sources.yaml", f"{name}: more schedule entries than max_runs_per_day")
    morning = [c for c in cfg.schedule if c.batch == "morning"]
    if len(morning) != 1:
        raise ConfigError("sources.yaml", f"{name}: needs exactly one batch: morning entry (D-17)")
    if not _in_morning_window(morning[0].hour, morning[0].minute):
        raise ConfigError("sources.yaml", f"{name}: morning entry outside 21:30-00:00 UTC (D-17)")


async def _enqueue_ingest(engine: AsyncEngine, source: str) -> None:
    await enqueue(
        engine, "ingest", {"source": source, "mode": "incremental"}, dedupe_key=f"ingest:{source}"
    )


async def _enqueue_expire_ttl(engine: AsyncEngine, source: str) -> None:
    await enqueue(engine, "expire_ttl", {"source": source}, dedupe_key=f"expire_ttl:{source}")


async def _enqueue_boards_recheck(engine: AsyncEngine) -> None:
    await enqueue(engine, "boards_recheck", {}, dedupe_key="boards_recheck")


async def _enqueue_backlog(engine: AsyncEngine) -> None:
    await enqueue(engine, "enrich_backlog", {}, dedupe_key="enrich_backlog")


async def reap_abandoned_runs(engine: AsyncEngine) -> int:
    """Mark ingest runs left 'running' by a crashed worker as failed (Pitfall 3)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE ingest_runs SET status = 'failed', finished_at = now(), "
                "errors = CAST(:errors AS jsonb) WHERE status = 'running' "
                "AND started_at < now() - make_interval(hours => :h)"
            ),
            {
                "errors": '[{"source_job_id": null, "error": "abandoned"}]',
                "h": ABANDONED_AFTER_HOURS,
            },
        )
    reaped = int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
    if reaped:
        log.warning("abandoned_runs_reaped", count=reaped)
    return reaped


def _cron(hour: int, minute: int, day_of_week: str | None = None) -> CronTrigger:
    return CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week, timezone="UTC")


def register_source_jobs(
    scheduler: AsyncIOScheduler, engine: AsyncEngine, settings: Settings
) -> None:
    """Schedule every enabled, configured source plus the sweeps. Raises ConfigError."""
    from datetime import UTC, datetime

    sources_cfg = load_config("sources.yaml", SourcesConfig).root
    registered = set(registered_sources())
    for name in sorted(sources_cfg):
        cfg = sources_cfg[name]
        if not cfg.enabled:
            continue
        if name not in registered:
            log.warning("source_not_registered", source=name)
            continue
        adapter = get_adapter_factory(name)(
            AdapterContext(
                settings=settings,
                engine=engine,
                config=cfg,
                mode="incremental",
                now=datetime.now(UTC),
            )
        )
        if not adapter.configured():
            log.warning("source_not_configured", source=name)
            continue
        _validate_source(name, cfg)
        for n, spec in enumerate(cfg.schedule):
            scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
                _enqueue_ingest,
                _cron(spec.hour, spec.minute, spec.day_of_week),
                args=[engine, name],
                id=f"ingest:{name}" if n == 0 else f"ingest:{name}:{n}",
                replace_existing=True,
            )
        if adapter.expiry_policy == "ttl":
            scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
                _enqueue_expire_ttl,
                _cron(*TTL_SWEEP),  # 06:00 IST
                args=[engine, name],
                id=f"expire_ttl:{name}",
                replace_existing=True,
            )
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        _enqueue_boards_recheck,
        _cron(1, 0, "sun"),  # Sunday 06:30 IST, after the D-17 morning read
        args=[engine],
        id="boards_recheck",
        replace_existing=True,
    )
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        _enqueue_backlog,
        _cron(*ENRICH_BACKLOG),  # 07:00 IST
        args=[engine],
        id="enrich_backlog",
        replace_existing=True,
    )
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        reap_abandoned_runs,
        "interval",
        hours=1,
        args=[engine],
        id="reap_abandoned_runs",
        replace_existing=True,
    )
