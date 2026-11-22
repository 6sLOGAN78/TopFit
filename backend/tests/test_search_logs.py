import pytest
from ranker.search.service import SearchRequest, search
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import seed_decoys

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_one_log_row_per_search(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    resp = await search(
        engine, FakeEmbedder(), None, get_settings(), SearchRequest(q="senior python backend")
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT * FROM search_logs"))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == resp.search_log_id
    assert row["query_text"] == "senior python backend"
    assert row["pipeline_version"]
    assert row["pipeline_version"] == resp.pipeline_version
    assert row["latency_ms"] >= 0
    assert row["degraded"] is False
    assert len(row["results"]) == len(resp.items) > 0
    for entry in row["results"]:
        assert {"rrf", "lex_rank", "vec_rank", "cos"} <= set(entry["features"])
    assert row["ranked_ids"]
    assert row["filters"] == {} or isinstance(row["filters"], dict)


async def test_no_embedder_degrades_but_logs(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    resp = await search(engine, None, None, get_settings(), SearchRequest(q="python kafka"))
    assert resp.degraded is True
    assert resp.items
    async with engine.connect() as conn:
        degraded = (await conn.execute(text("SELECT degraded FROM search_logs"))).scalar_one()
    assert degraded is True


async def test_query_text_capped(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    await search(engine, FakeEmbedder(), None, get_settings(), SearchRequest(q="python " * 200))
    async with engine.connect() as conn:
        n = (await conn.execute(text("SELECT length(query_text) FROM search_logs"))).scalar_one()
    assert n <= 500
