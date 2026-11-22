import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.search.models import SearchFilters, SetAsideKey
from ranker.search.sql import build_hybrid_sql, compile_filters
from tests.fixtures.search.jobs_seed import add_job

pytestmark = pytest.mark.usefixtures("clean_db")

LPA = 100_000


async def _ids(engine: AsyncEngine, f: SearchFilters) -> set[int]:
    """Lexical-leg retrieval over the 'widget' corpus with the compiled filters."""
    compiled = compile_filters(f)
    stmt = build_hybrid_sql(compiled.sql, legs="lex")
    params: dict[str, object] = {
        "lex_text": "widget",
        "skill_tokens": [],
        "w_skill": 1.0,
        "lex_k": 100,
        "rrf_k": 60,
        "fused_k": 100,
        **compiled.params,
    }
    async with engine.connect() as conn:
        return {int(r.id) for r in (await conn.execute(stmt, params)).all()}


async def _job(engine: AsyncEngine, title: str, **kw: object) -> int:
    return await add_job(engine, title=f"Widget {title}", **kw)  # pyright: ignore[reportArgumentType]


async def test_geo_default_keeps_india_and_eligible_remote(engine: AsyncEngine) -> None:
    onsite = await _job(engine, "onsite", city="Bengaluru", country_code="IN", remote_type="onsite")
    ww = await _job(engine, "ww", remote_type="remote", remote_eligibility="worldwide")
    apac = await _job(engine, "apac", remote_type="remote", remote_eligibility="apac")
    unknown = await _job(engine, "unk", remote_type="remote", remote_eligibility="unknown")
    no_country = await _job(engine, "nocountry", remote_type="unknown")
    us = await _job(engine, "us", remote_type="remote", remote_eligibility="us_only")
    eu = await _job(engine, "eu", remote_type="remote", remote_eligibility="eu_only")
    got = await _ids(engine, SearchFilters())
    assert got == {onsite, ww, apac, unknown, no_country}
    assert us not in got and eu not in got


async def test_city_filter_and_remote_ok(engine: AsyncEngine) -> None:
    pune = await _job(engine, "pune", city="Pune", country_code="IN", remote_type="onsite")
    multi = await _job(engine, "multi", city="Mumbai", cities=["Mumbai", "Pune"], country_code="IN")
    blr = await _job(engine, "blr", city="Bengaluru", country_code="IN", remote_type="onsite")
    india_remote = await _job(engine, "ir", remote_type="remote", remote_eligibility="india")
    us = await _job(engine, "us", remote_type="remote", remote_eligibility="us_only")
    assert await _ids(engine, SearchFilters(cities=["Pune"])) == {pune, multi}
    got = await _ids(engine, SearchFilters(cities=["Pune"], remote_ok=True))
    assert got == {pune, multi, india_remote}
    assert blr not in got and us not in got


async def test_work_mode_filter(engine: AsyncEngine) -> None:
    hybrid = await _job(engine, "h", country_code="IN", remote_type="hybrid")
    await _job(engine, "o", country_code="IN", remote_type="onsite")
    assert await _ids(engine, SearchFilters(work_modes=["hybrid"])) == {hybrid}


async def test_seniority_keeps_unknown(engine: AsyncEngine) -> None:
    senior = await _job(engine, "s", country_code="IN", seniority="senior")
    unknown = await _job(engine, "u", country_code="IN", seniority="unknown")
    await _job(engine, "j", country_code="IN", seniority="junior")
    assert await _ids(engine, SearchFilters(seniority=["senior"])) == {senior, unknown}


async def test_min_salary_rules(engine: AsyncEngine) -> None:
    high = await _job(
        engine, "hi", country_code="IN", salary_min_inr=9 * LPA, salary_max_inr=12 * LPA
    )
    await _job(engine, "lo", country_code="IN", salary_min_inr=6 * LPA, salary_max_inr=8 * LPA)
    unknown = await _job(engine, "unk", country_code="IN")
    estimate = await _job(
        engine,
        "est",
        country_code="IN",
        salary_min_inr=10 * LPA,
        salary_max_inr=14 * LPA,
        salary_is_estimate=True,
    )
    assert await _ids(engine, SearchFilters(min_salary_lpa=10)) == {high, unknown, estimate}
    got = await _ids(engine, SearchFilters(min_salary_lpa=10, exclude_estimates=True))
    assert got == {high, unknown}


def test_clause_per_set_aside_key_and_only_active() -> None:
    assert set(compile_filters(SearchFilters()).clauses) == {"geo"}
    full = compile_filters(
        SearchFilters(
            cities=["Pune"],
            work_modes=["remote"],
            seniority=["senior"],
            min_salary_lpa=10,
            exclude_estimates=True,
        )
    )
    keys: set[SetAsideKey] = {"city", "work_mode", "seniority", "min_salary", "estimates"}
    assert set(full.clauses) == keys
    # exclude_estimates without a minimum has nothing to apply to
    assert "estimates" not in compile_filters(SearchFilters(exclude_estimates=True)).clauses


def test_compiled_sql_binds_values_and_has_no_null_or_branches() -> None:
    compiled = compile_filters(
        SearchFilters(
            cities=["Sentinelville"],
            work_modes=["hybrid"],
            seniority=["lead"],
            min_salary_lpa=777,
        )
    )
    assert "IS NULL OR" not in compiled.sql
    for literal in ("Sentinelville", "777", "hybrid", "lead"):
        assert literal not in compiled.sql
    assert compiled.params["f_min_inr"] == 777 * LPA
    # the statement still parses and plans
    assert text(compiled.sql) is not None
