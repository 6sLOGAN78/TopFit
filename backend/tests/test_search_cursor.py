import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.search.service import SearchRequest, search
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import add_job

Q = "python backend"


async def _seed45(engine: AsyncEngine) -> None:
    for i in range(45):
        await add_job(
            engine,
            title=f"Backend Engineer {i}",
            skills=["Python"],
            description="python backend services",
            company=f"Co{i}",
            city="Bengaluru",
            country_code="IN",
            posted_days_ago=i / 10,
        )


async def _count_logs(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM search_logs"))).scalar_one())


@pytest.mark.usefixtures("clean_db")
async def test_paging_walks_ranked_ids(engine: AsyncEngine) -> None:
    await _seed45(engine)
    s = get_settings()
    p1 = await search(engine, FakeEmbedder(), None, s, SearchRequest(q=Q))
    assert len(p1.items) == 20
    assert p1.next_cursor
    async with engine.connect() as conn:
        ranked = (await conn.execute(text("SELECT ranked_ids FROM search_logs"))).scalar_one()
    assert [i.id for i in p1.items] == ranked[:20]
    p2 = await search(engine, FakeEmbedder(), None, s, SearchRequest(q=Q, cursor=p1.next_cursor))
    assert [i.id for i in p2.items] == ranked[20:40]
    assert p2.next_cursor
    assert p2.items[0].why_matched
    p3 = await search(engine, FakeEmbedder(), None, s, SearchRequest(q=Q, cursor=p2.next_cursor))
    assert [i.id for i in p3.items] == ranked[40:45]
    assert p3.next_cursor is None
    assert await _count_logs(engine) == 1


@pytest.mark.usefixtures("clean_db")
async def test_expired_job_skipped_without_duplicates(engine: AsyncEngine) -> None:
    await _seed45(engine)
    s = get_settings()
    p1 = await search(engine, FakeEmbedder(), None, s, SearchRequest(q=Q))
    assert p1.next_cursor
    async with engine.connect() as conn:
        ranked = (await conn.execute(text("SELECT ranked_ids FROM search_logs"))).scalar_one()
    victim = ranked[25]
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE jobs SET status = 'expired' WHERE id = :i"), {"i": victim})
    p2 = await search(engine, FakeEmbedder(), None, s, SearchRequest(q=Q, cursor=p1.next_cursor))
    ids = [i.id for i in p2.items]
    assert victim not in ids
    assert len(ids) == 19
    assert len(set(ids)) == len(ids)
    assert not set(ids) & {i.id for i in p1.items}


@pytest.fixture
def seeded45(database_url: str) -> None:
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE jobs, companies, search_logs RESTART IDENTITY CASCADE"))
    eng.dispose()

    async def _seed() -> None:
        a_eng = create_async_engine(database_url)
        try:
            await _seed45(a_eng)
        finally:
            await a_eng.dispose()

    asyncio.run(_seed())


def test_tampered_cursor_is_422_envelope(client: TestClient, seeded45: None) -> None:
    ok = client.get("/api/jobs/search", params={"q": Q}).json()
    cursor = ok["next_cursor"]
    assert cursor
    for bad in (cursor + "x", "garbage", cursor[:-3]):
        r = client.get("/api/jobs/search", params={"q": Q, "cursor": bad})
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["message"] == "invalid_cursor"
    good = client.get("/api/jobs/search", params={"q": Q, "cursor": cursor})
    assert good.status_code == 200
    assert len(good.json()["items"]) == 20
