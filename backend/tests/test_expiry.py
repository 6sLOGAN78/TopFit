import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
import time_machine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.expiry import (
    ExpiryConfig,
    expire_absent_after_run,
    expire_ttl,
    reactivate_run,
    volume_guard,
)
from ranker.ingest.pipeline import RunSummary, run_ingest
from ranker.ingest.sources.base import FetchResult, RawJob
from ranker.settings import get_settings

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


# ---- end to end through run_ingest (Remotive fixtures, one run per simulated day) ----------

FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
URL = "https://remotive.com/api/remote-jobs"
DAY0 = datetime(2026, 11, 20, 6, tzinfo=UTC)


def _payload(drop: set[int] | None = None, only: set[int] | None = None) -> dict[str, Any]:
    data = cast(dict[str, Any], json.loads(FIXTURE.read_text()))
    data["jobs"] = [
        j
        for j in data["jobs"]
        if j["id"] not in (drop or set()) and (only is None or j["id"] in only)
    ]
    return data


class Daily:
    """Runs ingest on consecutive simulated days (25 h apart) so the 4/day cap never trips."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.n = 0

    async def run(self, payload: dict[str, Any] | None = None, status: int = 200) -> RunSummary:
        get_settings.cache_clear()
        respx.clear()
        respx.get(URL).mock(
            return_value=httpx.Response(status, json=payload if payload is not None else _payload())
        )
        when = DAY0 + timedelta(hours=25 * self.n)
        self.n += 1
        with time_machine.travel(when, tick=False):
            return await run_ingest(self.engine, get_settings(), "remotive")


async def _run_row(engine: AsyncEngine, run_id: int) -> dict[str, Any]:
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT * FROM ingest_runs WHERE id = :i"), {"i": run_id})
        return dict(r.mappings().one())


async def _job(engine: AsyncEngine, sid: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT * FROM jobs WHERE source = 'remotive' AND source_job_id = :s"), {"s": sid}
        )
        return dict(r.mappings().one())


@respx.mock
async def test_e2e_absence_expiry_after_two_runs(engine: AsyncEngine) -> None:
    d = Daily(engine)
    for _ in range(3):
        s = await d.run()
        assert s.status == "succeeded"
    first = await d.run(_payload(drop={1001}))
    assert first.expired == 0
    assert (await _job(engine, "1001"))["missed_runs"] == 1
    second = await d.run(_payload(drop={1001}))
    assert second.expired == 1
    got = await _job(engine, "1001")
    assert got["status"] == "expired" and got["expired_by_run_id"] == second.run_id
    assert (await _run_row(engine, second.run_id))["expired"] == 1
    # Reappearing returns to active.
    await d.run()
    assert (await _job(engine, "1001"))["status"] == "active"


@respx.mock
async def test_e2e_volume_guard_blocks(engine: AsyncEngine) -> None:
    d = Daily(engine)
    for _ in range(3):
        await d.run()
    low = await d.run(_payload(only={1001, 1002, 1003}))  # 3 of 8 fetched
    assert low.status == "succeeded" and low.expired == 0
    notes = (await _run_row(engine, low.run_id))["notes"]
    assert notes["guard"]["allowed"] is False and notes["guard"]["reason"] == "below_50pct"
    assert (await _job(engine, "1004"))["missed_runs"] == 0


@respx.mock
async def test_e2e_cold_start_expires_nothing(engine: AsyncEngine) -> None:
    d = Daily(engine)
    await d.run()
    await d.run()
    s = await d.run(_payload(drop={1001}))
    assert s.expired == 0  # history of 2 allows counting a miss, but not yet expiring
    assert (await _job(engine, "1001"))["missed_runs"] == 1
    assert (await _job(engine, "1001"))["status"] == "active"


@respx.mock
async def test_e2e_failed_run_changes_nothing(engine: AsyncEngine) -> None:
    d = Daily(engine)
    for _ in range(3):
        await d.run()
    s = await d.run(status=500)
    assert s.status != "succeeded" and s.expired == 0
    assert (await _job(engine, "1001"))["missed_runs"] == 0


class _Stub:
    name = "remotive"
    expiry_policy = "absence"

    def __init__(self) -> None:
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result.failed_scopes["all"] = "HTTPStatusError:500"
        yield RawJob.model_validate(
            {
                "source": "remotive", "source_job_id": "9", "url": "https://remotive.com/x",
                "title": "Backend Engineer", "company_name": "Foo",
                "description_raw": "<p>Python</p>", "description_is_html": True,
                "workplace_hint": "remote", "region_hint": "Worldwide",
            }
        )  # fmt: skip

    def result(self) -> FetchResult:
        return self._result


@respx.mock
async def test_e2e_partial_run_never_expires(engine: AsyncEngine) -> None:
    d = Daily(engine)
    for _ in range(3):
        await d.run()
    get_settings.cache_clear()
    with time_machine.travel(DAY0 + timedelta(days=30), tick=False):
        s = await run_ingest(engine, get_settings(), "remotive", adapter=cast(Any, _Stub()))
    assert s.status == "partial" and s.expired == 0
    assert (await _job(engine, "1001"))["missed_runs"] == 0 and (await _job(engine, "1001"))[
        "status"
    ] == "active"


class _TtlStub(_Stub):
    name = "adzuna"
    expiry_policy = "ttl"

    async def fetch(self) -> AsyncIterator[RawJob]:
        if False:
            yield  # pragma: no cover
        return

    def result(self) -> FetchResult:
        return FetchResult()


async def test_e2e_ttl_source_counts_expired(engine: AsyncEngine) -> None:
    get_settings.cache_clear()
    for _ in range(2):
        await add_run(engine, "adzuna", 1, finished=NOW - timedelta(days=3))
    old = await add_job(engine, "old", source="adzuna", posted=NOW - timedelta(days=60))
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE ingest_runs SET fetched = 0"))
    with time_machine.travel(NOW, tick=False):
        s = await run_ingest(engine, get_settings(), "adzuna", adapter=cast(Any, _TtlStub()))
    # fetched 0 vs median 0: guard compares 0 >= 0 -> allowed
    assert s.status == "succeeded" and s.expired == 1
    assert (await job(engine, old))["status"] == "expired"


@respx.mock
async def test_e2e_duplicates_reposts_ghosts(engine: AsyncEngine) -> None:
    d = Daily(engine)
    p = _payload()
    twin = dict(p["jobs"][0])
    twin["id"] = 2001
    p["jobs"].append(twin)  # same company/title/location under a second id
    stale = next(j for j in p["jobs"] if j["id"] == 1002)
    stale["publication_date"] = "2026-06-01T00:00:00"
    s1 = await d.run(p)
    assert s1.duplicates_grouped == 1
    row1 = await _run_row(engine, s1.run_id)
    assert row1["duplicates_grouped"] == 1 and row1["reposts"] == 0
    assert (await _job(engine, "1002"))["ghost_reasons"] == ["stale_60d"]
    assert row1["ghost_flagged"] >= 1

    # Job 1004 comes back under a new id on a later day: a repost.
    p2 = _payload(drop={1004})
    clone = next(j for j in _payload()["jobs"] if j["id"] == 1004)
    clone["id"] = 3004
    p2["jobs"].append(clone)
    await d.run(p)  # day 2 (adds nothing new)
    s3 = await d.run(p2)
    # 1004 was first seen in run 1; 3004 is new in this run
    assert s3.reposts == 1
    assert (await _job(engine, "3004"))["repost_count"] == 1
    async with engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE source = 'remotive' AND status = 'active' "
                    "AND cardinality(ghost_reasons) > 0"
                )
            )
        ).scalar_one()
    assert (await _run_row(engine, s3.run_id))["ghost_flagged"] == n
