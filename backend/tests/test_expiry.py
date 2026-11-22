import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.expiry import (
    ExpiryConfig,
    expire_absent_after_run,
    expire_ttl,
    reactivate_run,
    volume_guard,
)

pytestmark = pytest.mark.usefixtures("clean_db")

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)
CFG = ExpiryConfig(ttl_days=45, unseen_days=30)


async def add_run(
    engine: AsyncEngine,
    source: str,
    fetched: int,
    *,
    status: str = "succeeded",
    scope_counts: dict[str, int] | None = None,
    finished: datetime | None = None,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO ingest_runs (source, status, fetched, started_at, finished_at, notes) "
                "VALUES (:s, :st, :f, :t, :t, CAST(:n AS jsonb)) RETURNING id"
            ),
            {
                "s": source, "st": status, "f": fetched, "t": finished or NOW,
                "n": json.dumps({"scope_counts": scope_counts or {}}),
            },
        )  # fmt: skip
        return int(row.scalar_one())


async def add_job(
    engine: AsyncEngine,
    sid: str,
    *,
    source: str = "remotive",
    scope: str = "all",
    last_run: int | None = None,
    posted: datetime | None = None,
    last_seen: datetime = NOW,
    first_seen: datetime = NOW,
    missed: int = 0,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, source_scope, url, title, "
                "description_raw, description_clean, content_hash, dedupe_key, "
                "normalizer_version, last_seen_run_id, posted_at, last_seen_at, first_seen_at, "
                "missed_runs) VALUES (:s, :sid, :sc, 'https://x.test', 't', '', 'd', 'h', :sid, "
                "'1', :lr, :p, :ls, :fs, :m) RETURNING id"
            ),
            {
                "s": source, "sid": sid, "sc": scope, "lr": last_run, "p": posted,
                "ls": last_seen, "fs": first_seen, "m": missed,
            },
        )  # fmt: skip
        return int(row.scalar_one())


async def job(engine: AsyncEngine, i: int) -> dict[str, Any]:
    async with engine.connect() as conn:
        r = await conn.execute(
            text(
                "SELECT status, missed_runs, expired_by_run_id, expires_at FROM jobs WHERE id = :i"
            ),
            {"i": i},
        )
        return dict(r.mappings().one())


@pytest.mark.parametrize(
    ("fetched", "allowed", "reason"),
    [(49, False, "below_50pct"), (50, True, "ok")],
)
async def test_volume_guard(engine: AsyncEngine, fetched: int, allowed: bool, reason: str) -> None:
    for n in (100, 98, 102, 99, 101):
        await add_run(engine, "remotive", n)
    await add_run(engine, "remotive", 5, status="failed")  # only succeeded runs count
    await add_run(engine, "remotive", 5, status="partial")
    async with engine.connect() as conn:
        d = await volume_guard(conn, "remotive", None, fetched, CFG)
    assert (d.allowed, d.reason) == (allowed, reason)
    assert d.normal == 100


async def test_volume_guard_cold_start_and_scope(engine: AsyncEngine) -> None:
    await add_run(engine, "remotive", 100, scope_counts={"acme": 10})
    async with engine.connect() as conn:
        d = await volume_guard(conn, "remotive", None, 100, CFG)
    assert (d.allowed, d.reason) == (False, "cold_start")
    await add_run(engine, "remotive", 100, scope_counts={"acme": 20})
    async with engine.connect() as conn:
        low = await volume_guard(conn, "remotive", "acme", 7, CFG)
        ok = await volume_guard(conn, "remotive", "acme", 8, CFG)
        missing = await volume_guard(conn, "remotive", "other", 8, CFG)
    assert low.normal == 15 and not low.allowed and ok.allowed
    assert not missing.allowed and missing.reason == "cold_start"


async def test_absence_two_runs_and_scopes(engine: AsyncEngine) -> None:
    r1 = await add_run(engine, "greenhouse", 3)
    x = await add_job(engine, "x", source="greenhouse", scope="beta", last_run=r1)
    y = await add_job(engine, "y", source="greenhouse", scope="beta", last_run=r1)
    z = await add_job(engine, "z", source="greenhouse", scope="acme", last_run=r1)
    r2 = await add_run(engine, "greenhouse", 3)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET last_seen_run_id = :r WHERE id = :i"), {"r": r2, "i": y}
        )
        n = await expire_absent_after_run(
            conn, source="greenhouse", run_id=r2, scopes=["beta"], cfg=CFG
        )  # acme failed this run
    assert n == 0
    assert (await job(engine, x))["missed_runs"] == 1 and (await job(engine, x))[
        "status"
    ] == "active"
    assert (await job(engine, z))["missed_runs"] == 0
    r3 = await add_run(engine, "greenhouse", 3)
    async with engine.begin() as conn:
        n = await expire_absent_after_run(
            conn, source="greenhouse", run_id=r3, scopes=["beta"], cfg=CFG
        )
    got = await job(engine, x)
    assert n == 1 and got["status"] == "expired" and got["expired_by_run_id"] == r3
    assert (await job(engine, z))["status"] == "active"
    assert await reactivate_run(engine, r3) >= 1
    back = await job(engine, x)
    assert back["status"] == "active" and back["missed_runs"] == 0


async def test_expire_ttl(engine: AsyncEngine) -> None:
    old = await add_job(
        engine, "old", source="adzuna", posted=NOW - timedelta(days=46), last_seen=NOW
    )
    unseen = await add_job(
        engine, "unseen", source="adzuna", posted=NOW - timedelta(days=10),
        last_seen=NOW - timedelta(days=31),
    )  # fmt: skip
    fresh = await add_job(engine, "fresh", source="adzuna", posted=NOW - timedelta(days=10))
    noposted = await add_job(engine, "np", source="adzuna", first_seen=NOW - timedelta(days=2))
    # No recent succeeded run: unseen rule must not fire, posted-age still does.
    async with engine.begin() as conn:
        n = await expire_ttl(conn, source="adzuna", cfg=CFG, now=NOW)
    assert n == 1
    assert (await job(engine, old))["status"] == "expired"
    assert (await job(engine, unseen))["status"] == "active"
    assert (await job(engine, fresh))["expires_at"] == NOW - timedelta(days=10) + timedelta(days=45)
    assert (await job(engine, noposted))["expires_at"] == NOW - timedelta(days=2) + timedelta(
        days=45
    )
    await add_run(engine, "adzuna", 10, finished=NOW - timedelta(hours=3))
    n = await expire_ttl(engine, source="adzuna", cfg=CFG, now=NOW)
    assert n == 1 and (await job(engine, unseen))["status"] == "expired"
    assert (await job(engine, fresh))["status"] == "active"
