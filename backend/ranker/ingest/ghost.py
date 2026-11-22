"""Repost detection and ghost signals (D-18). Signals only: nothing is expired or deleted."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

STALE_DAYS = 60
REPOST_FLAG_AT = 3  # third appearance of the same source + dedupe_key


async def detect_reposts(conn: AsyncConnection, *, run_id: int, source: str) -> int:
    """Rows first seen in ``run_id`` that match earlier rows of the same source (new id)."""
    result = await conn.execute(
        text(
            """
            UPDATE jobs j SET repost_count = sub.n
            FROM (
                SELECT j1.id, (
                    SELECT count(*) FROM jobs j2
                    WHERE j2.source = j1.source AND j2.dedupe_key = j1.dedupe_key
                      AND j2.source_job_id <> j1.source_job_id
                      AND j2.first_seen_at < j1.first_seen_at
                ) AS n
                FROM jobs j1
                WHERE j1.source = :source AND j1.first_seen_run_id = :run_id
            ) sub
            WHERE j.id = sub.id AND sub.n > 0 AND j.repost_count IS DISTINCT FROM sub.n
            """
        ),
        {"source": source, "run_id": run_id},
    )
    return int(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


_REASONS = """
    array_remove(ARRAY[
        CASE WHEN url IS NULL OR url = '' THEN 'no_apply_url' END,
        CASE WHEN repost_count >= :repost_at - 1 THEN 'reposted_3x' END,
        CASE WHEN :now - LEAST(first_seen_at, COALESCE(posted_at, first_seen_at))
                  > make_interval(days => :stale) THEN 'stale_60d' END
    ], NULL)
"""


async def refresh_ghost_signals(conn: AsyncConnection, *, source: str, now: datetime) -> int:
    params = {
        "source": source,
        "now": now,
        "stale": STALE_DAYS,
        "repost_at": REPOST_FLAG_AT,
    }
    await conn.execute(
        text(
            f"UPDATE jobs SET ghost_reasons = {_REASONS} "  # noqa: S608 - module constant
            "WHERE source = :source AND status = 'active' "
            f"AND ghost_reasons IS DISTINCT FROM {_REASONS}"
        ),
        params,
    )
    count = (
        await conn.execute(
            text(
                "SELECT count(*) FROM jobs WHERE source = :source AND status = 'active' "
                "AND cardinality(ghost_reasons) > 0"
            ),
            {"source": source},
        )
    ).scalar_one()
    return int(count)
