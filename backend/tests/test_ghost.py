from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.ghost import REPOST_FLAG_AT, STALE_DAYS, detect_reposts, refresh_ghost_signals

pytestmark = pytest.mark.usefixtures("clean_db")

NOW = datetime(2026, 6, 1, tzinfo=UTC)


async def add(
    engine: AsyncEngine,
    source: str,
    sid: str,
    key: str,
    *,
    first_seen: datetime,
    run: int | None = None,
    posted: datetime | None = None,
    url: str | None = "https://x.test/j",
    status: str = "active",
    reposts: int = 0,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, url, title, description_raw, "
                "description_clean, content_hash, dedupe_key, normalizer_version, first_seen_at, "
                "first_seen_run_id, posted_at, status, repost_count) "
                "VALUES (:s, :sid, :url, 't', '', 'd', 'h', :k, '1', :fs, :run, :p, :st, :rc) "
                "RETURNING id"
            ),
            {
                "s": source, "sid": sid, "url": url, "k": key, "fs": first_seen, "run": run,
                "p": posted, "st": status, "rc": reposts,
            },
        )  # fmt: skip
        return int(row.scalar_one())


async def row(engine: AsyncEngine, i: int) -> tuple[int, list[str]]:
    async with engine.connect() as conn:
        r = (
            await conn.execute(
                text("SELECT repost_count, ghost_reasons FROM jobs WHERE id = :i"), {"i": i}
            )
        ).one()
    return int(r[0]), list(r[1])


async def test_detect_reposts(engine: AsyncEngine) -> None:
    old = NOW - timedelta(days=10)
    await add(engine, "greenhouse", "a", "k", first_seen=old - timedelta(days=5), run=1)
    await add(engine, "greenhouse", "b", "k", first_seen=old, run=2)
    other = await add(engine, "lever", "z", "k", first_seen=old - timedelta(days=9), run=1)
    new = await add(engine, "greenhouse", "c", "k", first_seen=NOW, run=3)
    solo = await add(engine, "greenhouse", "d", "solo", first_seen=NOW, run=3)
    older = await add(engine, "greenhouse", "e", "k", first_seen=NOW + timedelta(days=1), run=4)
    async with engine.begin() as conn:
        n = await detect_reposts(conn, run_id=3, source="greenhouse")
    assert n == 1
    assert (await row(engine, new))[0] == 2
    assert (await row(engine, solo))[0] == 0
    assert (await row(engine, older))[0] == 0  # first seen in another run: untouched
    assert (await row(engine, other))[0] == 0  # other sources never count


async def test_ghost_signals(engine: AsyncEngine) -> None:
    stale = await add(engine, "s", "1", "k1", first_seen=NOW - timedelta(days=STALE_DAYS + 1))
    old_post = await add(
        engine, "s", "2", "k2", first_seen=NOW, posted=NOW - timedelta(days=STALE_DAYS + 5)
    )
    fresh = await add(engine, "s", "3", "k3", first_seen=NOW - timedelta(days=5))
    nourl = await add(engine, "s", "4", "k4", first_seen=NOW, url="")
    rep = await add(engine, "s", "5", "k5", first_seen=NOW, reposts=REPOST_FLAG_AT - 1)
    both = await add(engine, "s", "6", "k6", first_seen=NOW - timedelta(days=90), url="", reposts=2)
    expired = await add(
        engine, "s", "7", "k7", first_seen=NOW - timedelta(days=99), status="expired"
    )
    foreign = await add(engine, "t", "8", "k8", first_seen=NOW - timedelta(days=99))
    with time_machine.travel(NOW, tick=False):
        async with engine.begin() as conn:
            count = await refresh_ghost_signals(conn, source="s", now=datetime.now(UTC))
    assert count == 5
    assert (await row(engine, stale))[1] == ["stale_60d"]
    assert (await row(engine, old_post))[1] == ["stale_60d"]
    assert (await row(engine, fresh))[1] == []
    assert (await row(engine, nourl))[1] == ["no_apply_url"]
    assert (await row(engine, rep))[1] == ["reposted_3x"]
    assert (await row(engine, both))[1] == ["no_apply_url", "reposted_3x", "stale_60d"]
    assert (await row(engine, expired))[1] == []
    assert (await row(engine, foreign))[1] == []
    # A cleared condition removes its reason; status never changes.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET url = 'https://x.test' WHERE id = :i"), {"i": both}
        )
        await refresh_ghost_signals(conn, source="s", now=NOW)
    assert (await row(engine, both))[1] == ["reposted_3x", "stale_60d"]
    async with engine.connect() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM jobs WHERE status = 'active'"))
        ).scalar_one()
    assert n == 7
