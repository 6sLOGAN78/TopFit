import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.embeddings.service import vector_literal
from ranker.search.sql import apply_vector_mode, build_hybrid_sql, rrf_score
from tests.fixtures.search.jobs_seed import add_job, seed_decoys


def test_rrf_both_legs_rank_one() -> None:
    assert rrf_score([1, 1]) == pytest.approx(2 / 61)
    assert rrf_score([1, None]) == pytest.approx(1 / 61)
    assert rrf_score([None, None]) == 0.0
    assert rrf_score([2, 1], k=10) == pytest.approx(1 / 12 + 1 / 11)


def _params(q: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "qvec": vector_literal(FakeEmbedder().embed([q])[0]),
        "lex_text": q,
        "skill_tokens": [],
        "w_skill": 0.5,
        "lex_k": 100,
        "vec_k": 100,
        "rrf_k": 60,
        "fused_k": 100,
    }
    base.update(over)
    return base


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize("lex_first", [True, False])
async def test_ties_break_by_id(engine: AsyncEngine, lex_first: bool) -> None:
    lex_only = {"title": "zebrafish", "embed_text": "other"}
    vec_only = {"title": "unrelated", "embed_text": "zebrafish"}
    first, second = (lex_only, vec_only) if lex_first else (vec_only, lex_only)
    a = await add_job(engine, **first)  # pyright: ignore[reportArgumentType]
    b = await add_job(engine, **second)  # pyright: ignore[reportArgumentType]
    async with engine.begin() as conn:
        await apply_vector_mode(conn, "exact")
        rows = (await conn.execute(build_hybrid_sql(), _params("zebrafish", vec_k=1))).all()
    assert [r.id for r in rows] == [a, b]
    assert rows[0].rrf == pytest.approx(rows[1].rrf)


@pytest.mark.usefixtures("clean_db")
async def test_both_legs_outrank_single_leg(engine: AsyncEngine) -> None:
    ids = await seed_decoys(engine)
    async with engine.begin() as conn:
        await apply_vector_mode(conn, "exact")
        rows = (
            await conn.execute(
                build_hybrid_sql(),
                _params("C++ developer", lex_text="", skill_tokens=["cplusplus"]),
            )
        ).all()
    top = rows[0]
    assert top.id == ids["cpp"]
    assert top.lex_rank == 1
    assert top.vec_rank == 1
    assert float(top.rrf) == pytest.approx(2 / 61)
    assert all(float(r.rrf) < float(top.rrf) for r in rows[1:])
