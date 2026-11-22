import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.search.config import pipeline_version
from ranker.search.models import SearchResponse
from ranker.search.service import PROMPT_VERSION, SearchOptions, SearchRequest, search
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import add_job, seed_decoys

pytestmark = pytest.mark.usefixtures("clean_db")


async def _run(engine: AsyncEngine, q: str, opts: SearchOptions | None = None) -> SearchResponse:
    return await search(
        engine,
        FakeEmbedder(),
        None,
        get_settings(),
        SearchRequest(q=q),
        options=opts or SearchOptions(),
    )


async def test_logged_features_and_pipeline_version(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    resp = await _run(engine, "senior python backend")
    assert resp.pipeline_version == pipeline_version(PROMPT_VERSION)
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT results, pipeline_version FROM search_logs"))).one()
    assert row.pipeline_version == resp.pipeline_version
    assert row.results
    for entry in row.results:
        assert "score" in entry
        assert {
            "rrf",
            "lex_rank",
            "vec_rank",
            "cos",
            "recency",
            "region",
            "salary_known",
            "ghost",
        } <= set(entry["features"])


async def test_legs_lex_and_vec(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    await add_job(engine, title="Gardener", description="plants trees lawn", skills=[])
    lex = await _run(engine, "kafka python", SearchOptions(legs="lex", log=False))
    assert lex.items
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM search_logs"))).scalar_one() == 0
    assert lex.search_log_id is None
    # lexical only never returns a job with no term match
    assert "Gardener" not in {i.title for i in lex.items}
    vec = await _run(engine, "kafka python", SearchOptions(legs="vec", apply_signals=False))
    assert "Gardener" in {i.title for i in vec.items}  # vector leg returns everything embedded
    async with engine.connect() as conn:
        res = (await conn.execute(text("SELECT results FROM search_logs"))).scalar_one()
    assert all(e["features"]["lex_rank"] is None for e in res)
    assert all(e["features"]["vec_rank"] is not None for e in res)


async def test_signals_off_is_pure_rrf_order(engine: AsyncEngine) -> None:
    await seed_decoys(engine)
    resp = await _run(engine, "python backend", SearchOptions(apply_signals=False))
    async with engine.connect() as conn:
        res = (await conn.execute(text("SELECT results FROM search_logs"))).scalar_one()
    scores = [e["score"] for e in res]
    assert scores == sorted(scores, reverse=True)
    assert len(resp.items) == len(res)


async def test_ghost_job_present_but_ranked_lower(engine: AsyncEngine) -> None:
    ghost = await add_job(
        engine,
        title="Backend Engineer",
        skills=["Python"],
        description="python backend",
        company="GhostCo",
        ghost_reasons=["stale_60d"],
    )
    clean = await add_job(
        engine,
        title="Backend Engineer",
        skills=["Python"],
        description="python backend",
        company="RealCo",
    )
    resp = await _run(engine, "python backend")
    ids = [i.id for i in resp.items]
    assert ids == [clean, ghost]
    assert resp.items[1].ghost_reasons


async def test_company_cap_in_search(engine: AsyncEngine) -> None:
    for i in range(5):
        await add_job(engine, title=f"Python Dev {i}", skills=["Python"], company="Big")
    for i in range(10):
        await add_job(engine, title=f"Python Dev X{i}", skills=["Python"], company=f"Small{i}")
    resp = await _run(engine, "python dev")
    top10 = resp.items[:10]
    assert sum(1 for i in top10 if i.company == "Big") <= 2
    assert sum(1 for i in resp.items if i.company == "Big") == 5
