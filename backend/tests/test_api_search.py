import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from ranker.search.models import MetaResponse, SearchResponse
from tests.fixtures.search.jobs_seed import seed_decoys


@pytest.fixture
def seeded(database_url: str) -> dict[str, int]:
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE jobs, companies, search_logs RESTART IDENTITY CASCADE"))
    eng.dispose()

    async def _seed() -> dict[str, int]:
        a_eng = create_async_engine(database_url)
        try:
            return await seed_decoys(a_eng)
        finally:
            await a_eng.dispose()

    return asyncio.run(_seed())


def test_search_returns_typed_response(client: TestClient, seeded: dict[str, int]) -> None:
    r = client.get("/api/jobs/search", params={"q": "backend python"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = SearchResponse.model_validate(r.json())
    assert body.items
    first = body.items[0]
    assert first.source_display_name
    assert first.url.startswith("https://")
    assert first.first_seen_at and first.last_seen_at
    assert body.search_log_id is not None
    assert body.degraded is False


def test_search_log_row_written(
    client: TestClient, seeded: dict[str, int], database_url: str
) -> None:
    client.get("/api/jobs/search", params={"q": "kafka"})
    eng = create_engine(database_url)
    with eng.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM search_logs")).scalar_one()
    eng.dispose()
    assert n == 1


def test_query_too_long_is_422(client: TestClient, seeded: dict[str, int]) -> None:
    assert client.get("/api/jobs/search", params={"q": "a" * 501}).status_code == 422


def test_missing_query_is_422(client: TestClient) -> None:
    assert client.get("/api/jobs/search").status_code == 422


def test_nul_byte_does_not_500(client: TestClient, seeded: dict[str, int]) -> None:
    r = client.get("/api/jobs/search", params={"q": "py\x00thon\x01 backend"})
    assert r.status_code == 200


def test_control_only_query_returns_empty(client: TestClient, seeded: dict[str, int]) -> None:
    r = client.get("/api/jobs/search", params={"q": "\x00\x01"})
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_meta(client: TestClient, seeded: dict[str, int]) -> None:
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = MetaResponse.model_validate(r.json())
    assert body.active_jobs == len(seeded)
    assert any("haiku" in m for m in body.parse_models)
    assert "Bengaluru" in body.cities


def test_nul_in_city_is_not_500(client: TestClient, seeded: dict[str, int]) -> None:
    """Schemathesis found NUL in ?city= reaching Postgres (500); it is now cleaned like q."""
    r = client.get(
        "/api/jobs/search",
        params=[("q", "backend\x00"), ("city", "Pune\x00"), ("city", ""), ("city", "\x01")],
    )
    assert r.status_code == 200
