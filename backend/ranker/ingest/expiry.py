"""Expiry policies (ING-08): absence after N complete runs, TTL by age, volume guard, purge.

Jobs are never hard-deleted. Every expiry records ``expired_by_run_id`` so a bad run can be
undone with :func:`reactivate_run`. All SQL is set-based with bound parameters only.

Board sources (Greenhouse, Lever, Ashby) add two rules on top of per-board absence expiry:

- A dropped board (a 404, or two empty weekly rechecks) is no longer a complete scope, so its
  jobs would never absence-expire. :func:`expire_dropped_board` expires them on drop with the
  board-maintenance run id, and :func:`expire_stale_board_jobs` catches any dropped board left
  over (e.g. a 404 seen by the daily fetch, which has no maintenance run).
- Age fallback: an active job not listed for ``board_unseen_days`` expires, but only when its
  board answered successfully (``last_checked_at``) within ``BOARD_HEALTHY_HOURS``. A failing
  board, a rejected board, a scope without a registry row, or a failed or guard-blocked run
  expires nothing, so an outage can never empty the index.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ranker.ingest.dedupe import recompute_dedupe


class VolumeGuard(BaseModel):
    ratio: float = 0.5
    window: int = 5
    min_history: int = 2


class ExpiryConfig(BaseModel):
    absence_missed_runs: int = 2
    volume_guard: VolumeGuard = VolumeGuard()
    ttl_days: int | None = None
    unseen_days: int | None = None
    board_unseen_days: int = Field(default=7, ge=1, le=365)


BOARD_HEALTHY_HOURS = 48


@dataclass
class GuardDecision:
    allowed: bool
    normal: float | None
    reason: str

    def as_note(self) -> dict[str, object]:
        return {"allowed": self.allowed, "reason": self.reason, "normal": self.normal}


async def volume_guard(
    conn: AsyncConnection,
    source: str,
    scope: str | None,
    fetched: int,
    cfg: ExpiryConfig,
) -> GuardDecision:
    """Allow expiry only when this run fetched >= ratio x the median of recent succeeded runs."""
    guard = cfg.volume_guard
    value = "r.fetched" if scope is None else "(r.notes->'scope_counts'->>:scope)::int"
    sql = f"""
        SELECT count(v), percentile_cont(0.5) WITHIN GROUP (ORDER BY v)
        FROM (
            SELECT {value} AS v FROM (
                SELECT fetched, notes FROM ingest_runs
                WHERE source = :source AND status = 'succeeded'
                ORDER BY started_at DESC, id DESC LIMIT :window
            ) r
        ) h WHERE v IS NOT NULL
    """  # noqa: S608 - `value` is one of two module-controlled strings
    params: dict[str, object] = {"source": source, "window": guard.window}
    if scope is not None:
        params["scope"] = scope
    row = (await conn.execute(text(sql), params)).one()
    count, normal = int(row[0]), (None if row[1] is None else float(row[1]))
    if count < guard.min_history or normal is None:
        return GuardDecision(False, normal, "cold_start")
    if fetched < guard.ratio * normal:
        return GuardDecision(False, normal, "below_50pct")
    return GuardDecision(True, normal, "ok")


async def expire_absent_after_run(
    conn: AsyncConnection,
    *,
    source: str,
    run_id: int,
    scopes: Sequence[str],
    cfg: ExpiryConfig,
) -> int:
    """Count a miss for active rows of the complete scopes, expire at N misses. Returns expired."""
    if not scopes:
        return 0
    await conn.execute(
        text(
            "UPDATE jobs SET missed_runs = missed_runs + 1 "
            "WHERE source = :source AND status = 'active' "
            "AND source_scope = ANY(CAST(:scopes AS text[])) "
            "AND last_seen_run_id IS DISTINCT FROM :run_id"
        ),
        {"source": source, "scopes": list(scopes), "run_id": run_id},
    )
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = 'expired', expired_by_run_id = :run_id "
            "WHERE source = :source AND status = 'active' "
            "AND source_scope = ANY(CAST(:scopes AS text[])) "
            "AND missed_runs >= :n"
        ),
        {
            "source": source,
            "scopes": list(scopes),
            "run_id": run_id,
            "n": cfg.absence_missed_runs,
        },
    )
    return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def expire_dropped_board(
    conn: AsyncConnection, *, source: str, slug: str, run_id: int
) -> int:
    """Expire the active jobs of one board that is ``dropped`` in the registry. Returns expired."""
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = 'expired', expired_by_run_id = :run_id "
            "WHERE source = :source AND source_scope = :slug AND status = 'active' "
            "AND EXISTS (SELECT 1 FROM source_boards b WHERE b.ats = :source "
            "AND b.slug = :slug AND b.status = 'dropped')"
        ),
        {"source": source, "slug": slug, "run_id": run_id},
    )
    return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def expire_stale_board_jobs(
    conn: AsyncConnection,
    *,
    source: str,
    run_id: int,
    now: datetime,
    cfg: ExpiryConfig,
) -> int:
    """Board-source fallback: expire jobs of dropped boards, and jobs unseen for
    ``board_unseen_days`` whose board answered OK within ``BOARD_HEALTHY_HOURS``.

    Callers run this only after a succeeded run passed the source-level volume guard.
    """
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = 'expired', expired_by_run_id = :run_id "
            "WHERE jobs.source = :source AND jobs.status = 'active' AND ("
            " EXISTS (SELECT 1 FROM source_boards b WHERE b.ats = jobs.source "
            "  AND b.slug = jobs.source_scope AND b.status = 'dropped')"
            " OR (jobs.last_seen_at < :now - make_interval(days => :days)"
            "  AND EXISTS (SELECT 1 FROM source_boards b WHERE b.ats = jobs.source "
            "   AND b.slug = jobs.source_scope AND b.status IN ('seed', 'active') "
            "   AND b.last_checked_at >= :now - make_interval(hours => :hours))))"
        ),
        {
            "source": source,
            "run_id": run_id,
            "now": now,
            "days": cfg.board_unseen_days,
            "hours": BOARD_HEALTHY_HOURS,
        },
    )
    return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def _expire_ttl(
    conn: AsyncConnection,
    source: str,
    cfg: ExpiryConfig,
    now: datetime,
    run_id: int | None,
    healthy: bool | None,
) -> int:
    if cfg.ttl_days is None:
        return 0
    await conn.execute(
        text(
            "UPDATE jobs SET expires_at = COALESCE(posted_at, first_seen_at) "
            "+ make_interval(days => :ttl) "
            "WHERE source = :source AND status = 'active' AND expires_at IS NULL"
        ),
        {"source": source, "ttl": cfg.ttl_days},
    )
    total = (
        await conn.execute(
            text(
                "UPDATE jobs SET status = 'expired', expired_by_run_id = :run_id "
                "WHERE source = :source AND status = 'active' "
                "AND COALESCE(posted_at, first_seen_at) < :now - make_interval(days => :ttl)"
            ),
            {"source": source, "run_id": run_id, "now": now, "ttl": cfg.ttl_days},
        )
    ).rowcount  # pyright: ignore[reportAttributeAccessIssue]
    if cfg.unseen_days is not None:
        if healthy is None:
            healthy = bool(
                (
                    await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM ingest_runs WHERE source = :source "
                            "AND status = 'succeeded' "
                            "AND finished_at >= :now - interval '48 hours')"
                        ),
                        {"source": source, "now": now},
                    )
                ).scalar_one()
            )
        if healthy:
            total += (
                await conn.execute(
                    text(
                        "UPDATE jobs SET status = 'expired', expired_by_run_id = :run_id "
                        "WHERE source = :source AND status = 'active' "
                        "AND last_seen_at < :now - make_interval(days => :unseen)"
                    ),
                    {"source": source, "run_id": run_id, "now": now, "unseen": cfg.unseen_days},
                )
            ).rowcount  # pyright: ignore[reportAttributeAccessIssue]
    return int(total)


async def expire_ttl(
    engine_or_conn: AsyncEngine | AsyncConnection,
    *,
    source: str,
    cfg: ExpiryConfig,
    now: datetime,
    run_id: int | None = None,
    healthy: bool | None = None,
) -> int:
    """Age-based expiry for capped-window feeds.

    The unseen-days rule only fires when the source is healthy (a succeeded run in the last
    48 h, or ``healthy=True`` from a run that just succeeded); the posted-age rule always does.
    """
    if isinstance(engine_or_conn, AsyncEngine):
        async with engine_or_conn.begin() as conn:
            return await _expire_ttl(conn, source, cfg, now, run_id, healthy)
    return await _expire_ttl(engine_or_conn, source, cfg, now, run_id, healthy)


async def reactivate_run(engine: AsyncEngine, run_id: int) -> int:
    """Undo every expiry made by ``run_id`` (recovery from a bad run)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE jobs SET status = 'active', missed_runs = 0, expired_by_run_id = NULL "
                "WHERE expired_by_run_id = :run_id AND status = 'expired'"
            ),
            {"run_id": run_id},
        )
        return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def purge_source(engine: AsyncEngine, source: str) -> int:
    """Irreversibly remove all source-derived content (ToS termination / D-15).

    Row ids stay for referential integrity. Dedupe is recomputed afterwards so another
    source's duplicate becomes canonical.
    """
    async with engine.begin() as conn:
        keys = [
            str(r[0])
            for r in await conn.execute(
                text("SELECT DISTINCT dedupe_key FROM jobs WHERE source = :source"),
                {"source": source},
            )
        ]
        result = await conn.execute(
            text(
                "UPDATE jobs SET status = 'removed', title = '[removed]', url = '', "
                "description_raw = '', description_clean = '', location_text = NULL, "
                "city = NULL, cities = '{}', salary_min = NULL, salary_max = NULL, "
                "salary_currency = NULL, salary_period = NULL, salary_min_inr_annual = NULL, "
                "salary_max_inr_annual = NULL, salary_text = NULL, skills = '{}', "
                "skills_text = '', embedding = NULL, embedding_model = NULL, "
                "canonical_job_id = NULL, ghost_reasons = '{}', group_sources = '{}', "
                "dedupe_key = 'purged:' || id::text, updated_at = now() "
                "WHERE source = :source"
            ),
            {"source": source},
        )
        await recompute_dedupe(conn, keys)
        return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
