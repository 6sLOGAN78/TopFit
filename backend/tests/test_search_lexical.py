from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.search.models import SearchResponse
from ranker.search.service import SearchRequest, search
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import seed_decoys

pytestmark = pytest.mark.usefixtures("clean_db")


async def run(engine: AsyncEngine, q: str) -> SearchResponse:
    return await search(engine, FakeEmbedder(), None, get_settings(), SearchRequest(q=q))


async def features(engine: AsyncEngine, log_id: UUID | None) -> dict[int, dict[str, Any]]:
    async with engine.connect() as conn:
        results = (
            await conn.execute(text("SELECT results FROM search_logs WHERE id = :i"), {"i": log_id})
        ).scalar_one()
    return {int(r["job_id"]): r["features"] for r in results}


async def lex_rank(engine: AsyncEngine, q: str, job_id: int) -> int | None:
    resp = await run(engine, q)
    return (await features(engine, resp.search_log_id))[job_id]["lex_rank"]


async def test_cpp_matches_skill_not_c_decoy(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    assert await lex_rank(engine, "C++", ids["cpp"]) == 1
    assert await lex_rank(engine, "C++", ids["c"]) is None
    async with engine.connect() as conn:
        hits = {
            r[0]
            for r in await conn.execute(
                text("SELECT id FROM jobs WHERE skills_tsv @@ to_tsquery('simple', 'cplusplus')")
            )
        }
    assert hits == {ids["cpp"]}


async def test_cpp_developer_ranks_above_c_decoy(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    order = [i.id for i in (await run(engine, "C++ developer")).items]
    assert order.index(ids["cpp"]) < order.index(ids["c"])


@pytest.mark.parametrize("q", ["golang", "Go developer", "golang developer"])
async def test_go_queries_hit_go_job(engine: AsyncEngine, q: str) -> None:
    ids = await seed_decoys(engine)
    assert await lex_rank(engine, q, ids["go"]) is not None
    if q == "golang":
        assert await lex_rank(engine, q, ids["cpp"]) is None


async def test_nodejs_query(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    assert await lex_rank(engine, "node.js backend", ids["node"]) is not None


async def test_csharp_dotnet_query(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    assert await lex_rank(engine, "C# .NET", ids["csharp"]) == 1
    assert await lex_rank(engine, "C# .NET", ids["c"]) is None


async def test_full_sentence_is_or_joined(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    resp = await run(engine, "I want a senior backend role using python and kafka")
    async with engine.connect() as conn:
        lex_hits = (
            await conn.execute(
                text("SELECT lex_hits FROM search_logs WHERE id = :i"), {"i": resp.search_log_id}
            )
        ).scalar_one()
    assert lex_hits > 0
    assert (await features(engine, resp.search_log_id))[ids["senior"]]["lex_rank"] is not None
