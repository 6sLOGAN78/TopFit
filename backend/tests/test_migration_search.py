import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_skills_tsv_and_search_logs(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        cols = {
            r[0]
            for r in await conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs'")
            )
        }
        idx = {
            r[0]: r[1]
            for r in await conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            )
        }
        logs = (
            await conn.execute(text("SELECT to_regclass('public.search_logs') IS NOT NULL"))
        ).scalar_one()
    assert "skills_tsv" in cols
    assert "USING gin (skills_tsv)" in idx["jobs_skills_tsv_gin"]
    assert logs is True
    assert "search_logs_created" in idx


@pytest.mark.usefixtures("clean_db")
async def test_skills_tsv_is_simple_config(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, url, title, description_raw, "
                "description_clean, content_hash, dedupe_key, normalizer_version, skills_text) "
                "VALUES ('s', '1', 'u', 't', 'r', 'c', 'h', 'd', 'v', 'cplusplus golang')"
            )
        )
        hit = (
            await conn.execute(
                text("SELECT count(*) FROM jobs WHERE skills_tsv @@ to_tsquery('simple', 'golang')")
            )
        ).scalar_one()
    assert hit == 1
