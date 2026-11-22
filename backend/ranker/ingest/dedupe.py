"""Cross-source duplicate grouping (D-18). Fuzzy matching (ING-10) is Phase 6.

``dedupe_key`` already holds normalised company + title (seniority kept) + city/remote, so
grouping is simply by that key across ALL sources. The canonical row is the newest posted,
then longest description, then highest id. Nothing is deleted; non-canonical rows only point
at the canonical one, and the canonical row lists every source of its active group.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_GROUPED = """
    SELECT id, source, dedupe_key,
           first_value(id) OVER (
               PARTITION BY dedupe_key
               ORDER BY posted_at DESC NULLS LAST, length(description_clean) DESC, id DESC
           ) AS canon
    FROM jobs
    WHERE status = 'active' AND dedupe_key = ANY(CAST(:keys AS text[]))
"""


@dataclass
class DedupeStats:
    changed: int
    duplicates: int


async def recompute_dedupe(conn: AsyncConnection, keys: Sequence[str]) -> DedupeStats:
    unique = sorted(set(keys))
    if not unique:
        return DedupeStats(0, 0)
    params = {"keys": unique}
    changed = 0
    # Rows that are no longer active carry no group state.
    cleared = await conn.execute(
        text(
            "UPDATE jobs SET canonical_job_id = NULL, group_sources = '{}' "
            "WHERE status <> 'active' AND dedupe_key = ANY(CAST(:keys AS text[])) "
            "AND (canonical_job_id IS NOT NULL OR group_sources <> '{}')"
        ),
        params,
    )
    changed += int(cleared.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
    pointers = await conn.execute(
        text(
            f"""
            UPDATE jobs j SET canonical_job_id = NULLIF(c.canon, j.id)
            FROM ({_GROUPED}) c
            WHERE j.id = c.id AND j.canonical_job_id IS DISTINCT FROM NULLIF(c.canon, j.id)
            """  # noqa: S608 - module constant only
        ),
        params,
    )
    changed += int(pointers.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
    sources = await conn.execute(
        text(
            f"""
            WITH a AS ({_GROUPED}),
            g AS (
                SELECT canon, array_agg(DISTINCT source ORDER BY source) AS srcs
                FROM a GROUP BY canon
            )
            UPDATE jobs j SET group_sources =
                CASE WHEN a.id = a.canon THEN g.srcs ELSE CAST('{{}}' AS text[]) END
            FROM a JOIN g ON g.canon = a.canon
            WHERE j.id = a.id AND j.group_sources IS DISTINCT FROM
                CASE WHEN a.id = a.canon THEN g.srcs ELSE CAST('{{}}' AS text[]) END
            """  # noqa: S608 - module constant only
        ),
        params,
    )
    changed += int(sources.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
    dup = (
        await conn.execute(
            text(
                "SELECT count(*) FROM jobs WHERE status = 'active' "
                "AND canonical_job_id IS NOT NULL "
                "AND dedupe_key = ANY(CAST(:keys AS text[]))"
            ),
            params,
        )
    ).scalar_one()
    return DedupeStats(changed, int(dup))
