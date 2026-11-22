from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.dedupe import recompute_dedupe

pytestmark = pytest.mark.usefixtures("clean_db")

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def insert_job(
    engine: AsyncEngine,
    *,
    source: str,
    sid: str,
    key: str,
    posted_days: int | None = 0,
    desc: str = "d",
    status: str = "active",
    url: str = "https://x.test/j",
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, url, title, description_raw, "
                "description_clean, content_hash, dedupe_key, normalizer_version, posted_at, "
                "status) VALUES (:s, :sid, :url, 't', '', :d, 'h', :k, '1', :p, :st) RETURNING id"
            ),
            {
                "s": source,
                "sid": sid,
                "url": url,
                "d": desc,
                "k": key,
                "st": status,
                "p": None if posted_days is None else T0 + timedelta(days=posted_days),
            },
        )
        return int(row.scalar_one())


async def state(engine: AsyncEngine) -> dict[int, tuple[int | None, list[str]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT id, canonical_job_id, group_sources FROM jobs"))
        return {int(r[0]): (r[1], list(r[2])) for r in rows}


async def test_canonical_and_promotion(engine: AsyncEngine) -> None:
    a = await insert_job(engine, source="remotive", sid="1", key="k", posted_days=1)
    b = await insert_job(engine, source="remotive", sid="2", key="k", posted_days=3)
    c = await insert_job(engine, source="remotive", sid="3", key="k", posted_days=2)
    solo = await insert_job(engine, source="remotive", sid="4", key="other")
    async with engine.begin() as conn:
        stats = await recompute_dedupe(conn, ["k", "other"])
    assert stats.duplicates == 2
    st = await state(engine)
    assert st[b][0] is None and st[a][0] == b and st[c][0] == b and st[solo][0] is None
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE jobs SET status = 'expired' WHERE id = :i"), {"i": b})
        await recompute_dedupe(conn, ["k"])
    st = await state(engine)
    assert st[b] == (None, []) and st[c][0] is None and st[a][0] == c


async def test_tie_break_description_then_id(engine: AsyncEngine) -> None:
    a = await insert_job(engine, source="r", sid="1", key="k", posted_days=None, desc="short")
    b = await insert_job(engine, source="r", sid="2", key="k", posted_days=None, desc="longer one")
    c = await insert_job(engine, source="r", sid="3", key="k", posted_days=None, desc="longer one")
    async with engine.begin() as conn:
        await recompute_dedupe(conn, ["k"])
    st = await state(engine)
    assert st[c][0] is None and st[a][0] == c and st[b][0] == c


async def test_cross_source_group_sources(engine: AsyncEngine) -> None:
    a = await insert_job(engine, source="remotive", sid="1", key="k", posted_days=1)
    b = await insert_job(engine, source="greenhouse", sid="9", key="k", posted_days=2)
    junior = await insert_job(engine, source="remotive", sid="5", key="k-junior")
    async with engine.begin() as conn:
        stats = await recompute_dedupe(conn, ["k", "k-junior"])
    st = await state(engine)
    assert stats.duplicates == 1
    assert st[b] == (None, ["greenhouse", "remotive"])
    assert st[a] == (b, [])
    assert st[junior] == (None, ["remotive"])  # singleton group lists itself
    # Idempotent.
    async with engine.begin() as conn:
        again = await recompute_dedupe(conn, ["k", "k-junior"])
    assert again.changed == 0 and again.duplicates == 1
