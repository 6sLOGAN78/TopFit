import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.search.models import SearchFilters
from ranker.search.service import SearchRequest, search
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import add_job

LPA = 100_000
Q = "python backend bengaluru 10 lpa"


async def _group(
    engine: AsyncEngine,
    n: int,
    label: str,
    *,
    city: str | None = None,
    country_code: str | None = None,
    remote_type: str = "onsite",
    remote_eligibility: str = "unknown",
    salary_lpa: tuple[int, int] | None = None,
) -> None:
    for i in range(n):
        await add_job(
            engine,
            title=f"Backend Engineer {label}{i}",
            skills=["Python"],
            description="python backend services",
            city=city,
            country_code=country_code,
            remote_type=remote_type,
            remote_eligibility=remote_eligibility,
            salary_min_inr=salary_lpa[0] * LPA if salary_lpa else None,
            salary_max_inr=salary_lpa[1] * LPA if salary_lpa else None,
        )


async def _seed(engine: AsyncEngine) -> None:
    await _group(engine, 10, "B", city="Bengaluru", country_code="IN", salary_lpa=(12, 16))
    await _group(engine, 6, "Low", city="Bengaluru", country_code="IN", salary_lpa=(5, 8))
    await _group(engine, 4, "Pune", city="Pune", country_code="IN", salary_lpa=(12, 16))
    await _group(engine, 3, "US", remote_type="remote", remote_eligibility="us_only")


@pytest.mark.usefixtures("clean_db")
async def test_set_aside_and_relax_counts(engine: AsyncEngine) -> None:
    await _seed(engine)
    started = time.perf_counter()
    resp = await search(engine, FakeEmbedder(), None, get_settings(), SearchRequest(q=Q))
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert len(resp.items) == 10
    assert {i.city for i in resp.items} == {"Bengaluru"}
    assert resp.set_aside_universe == 200
    sa = {s.filter: s.count for s in resp.set_aside}
    # Pune (4) and US-only remote (3) fail the city filter; the 6 low-salary Bengaluru fail salary
    assert sa == {"city": 7, "min_salary": 6}
    rx = {r.filter: r.count for r in resp.relax}
    # US-only rows have no salary so they pass the salary clause: removing city adds 4 + 3
    assert rx == {"city": 7, "min_salary": 6}
    assert [r.count for r in resp.relax] == sorted((r.count for r in resp.relax), reverse=True)
    print(f"set-aside search latency at 23 seeded jobs: {elapsed_ms:.0f} ms")


@pytest.mark.usefixtures("clean_db")
async def test_no_city_geo_counts_us_only_and_relax_omits_geo(engine: AsyncEngine) -> None:
    await _seed(engine)
    resp = await search(
        engine, FakeEmbedder(), None, get_settings(), SearchRequest(q="python backend")
    )
    assert {s.filter: s.count for s in resp.set_aside} == {"geo": 3}
    assert resp.relax == []
    assert len(resp.items) == 20


@pytest.mark.usefixtures("clean_db")
async def test_zero_matches_still_returns_relax(engine: AsyncEngine) -> None:
    await _seed(engine)
    resp = await search(
        engine,
        FakeEmbedder(),
        None,
        get_settings(),
        SearchRequest(q="python backend", filters=SearchFilters(cities=["Chennai"])),
    )
    assert resp.items == []
    assert {s.filter for s in resp.set_aside} == {"city"}
    assert resp.relax and resp.relax[0].filter == "city"


@pytest.mark.usefixtures("clean_db")
async def test_zero_counts_are_omitted(engine: AsyncEngine) -> None:
    await _seed(engine)
    resp = await search(
        engine,
        FakeEmbedder(),
        None,
        get_settings(),
        SearchRequest(q="python backend", filters=SearchFilters(seniority=["senior"])),
    )
    # every seeded job has unknown seniority, which passes the filter
    assert "seniority" not in {s.filter for s in resp.set_aside}


@pytest.mark.usefixtures("clean_db")
async def test_search_log_stores_set_aside_and_filters(
    engine: AsyncEngine,
) -> None:
    await _seed(engine)
    await search(engine, FakeEmbedder(), None, get_settings(), SearchRequest(q=Q))
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT filters, parse, set_aside FROM search_logs"))).one()
    assert row.set_aside == {"city": 7, "min_salary": 6}
    assert row.filters["cities"] == ["Bengaluru"]
    assert row.filters["min_salary_lpa"] == 10
    assert row.filters["chip_sources"] == {"city": "rules", "min_salary": "rules"}
    assert row.parse["source"] == "rules"
    assert row.parse["residue_count"] == 0
    assert "residue" not in row.parse


@pytest.mark.usefixtures("clean_db")
async def test_auto_false_uses_explicit_verbatim(engine: AsyncEngine) -> None:
    await _seed(engine)
    resp = await search(
        engine,
        FakeEmbedder(),
        None,
        get_settings(),
        SearchRequest(q=Q, filters=SearchFilters(cities=["Pune"]), auto_filters=False),
    )
    assert resp.parsed.filters == SearchFilters(cities=["Pune"])
    assert {i.city for i in resp.items} == {"Pune"}
    assert {c.source for c in resp.parsed.chips} == {"user"}


def test_demo_query_through_api(client: TestClient, database_url: str) -> None:
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE jobs, companies, search_logs RESTART IDENTITY CASCADE"))
    eng.dispose()
    r = client.get("/api/jobs/search", params={"q": "backend python bengaluru 10 LPA remote ok"})
    assert r.status_code == 200
    body = r.json()
    assert [c["label"] for c in body["parsed"]["chips"]] == [
        "City: Bengaluru + remote",
        "₹10 LPA+",
    ]
    assert body["parsed"]["parse_source"] == "rules"
