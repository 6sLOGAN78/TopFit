import json
from typing import Literal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.embeddings.service import vector_literal
from ranker.search.sql import apply_vector_mode, build_hybrid_sql
from tests.fixtures.search.jobs_seed import seed_bulk

pytestmark = pytest.mark.usefixtures("clean_db")

PUNE = "AND j.city = 'Pune'"


def _params() -> dict[str, object]:
    return {
        "qvec": vector_literal(FakeEmbedder().embed(["backend engineer"])[0]),
        "vec_k": 100,
        "rrf_k": 60,
        "fused_k": 1000,
    }


async def _run(
    engine: AsyncEngine, mode: Literal["exact", "hnsw"], filter_sql: str
) -> tuple[int, str]:
    stmt = build_hybrid_sql(filter_sql, legs="vec")
    async with engine.begin() as conn:
        await apply_vector_mode(conn, mode)
        if mode == "hnsw":
            await conn.execute(text("SELECT set_config('enable_seqscan', 'off', true)"))
        rows = (await conn.execute(stmt, _params())).all()
        plan = (
            await conn.execute(text("EXPLAIN (FORMAT JSON) " + stmt.text), _params())
        ).scalar_one()
    return len(rows), json.dumps(plan)


@pytest.mark.parametrize("filter_sql", ["", PUNE])
async def test_exact_returns_full_k_without_index_or_cte(
    engine: AsyncEngine, filter_sql: str
) -> None:
    await seed_bulk(engine, 1000, city_every=10)
    n, plan = await _run(engine, "exact", filter_sql)
    assert n == 100
    assert "CTE Scan" not in plan
    assert "jobs_embedding_hnsw" not in plan


@pytest.mark.parametrize("filter_sql", ["", PUNE])
async def test_hnsw_mode_returns_full_k(engine: AsyncEngine, filter_sql: str) -> None:
    await seed_bulk(engine, 1000, city_every=10)
    n, plan = await _run(engine, "hnsw", filter_sql)
    assert n == 100
    assert "CTE Scan" not in plan
    if not filter_sql:
        assert "jobs_embedding_hnsw" in plan


async def test_default_ef_search_would_truncate(engine: AsyncEngine) -> None:
    """Documents why hnsw mode sets ef_search: the default 40 caps an index scan."""
    await seed_bulk(engine, 1000, city_every=10)
    stmt = build_hybrid_sql("", legs="vec")
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('enable_seqscan', 'off', true)"))
        n = len((await conn.execute(stmt, _params())).all())
    assert n <= 100
