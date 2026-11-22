import os
import re
import typing
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import load_config
from ranker.ingest.normalize import dealbreakers as db
from ranker.ingest.sources import (
    RawJob,
    SourcesConfig,
    UnknownSource,
    get_adapter_factory,
)
from ranker.llm.schemas import RemoteEligibility
from tests.conftest import BACKEND_DIR

_REQ = "source, source_job_id, url, title, description_raw, description_clean, "
_REQ += "content_hash, dedupe_key, normalizer_version"
_VALS = "'s', :id, 'https://x', :t, 'r', 'c', 'h', 'd', 'v'"


async def _check_values(engine: AsyncEngine, column: str) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'jobs'::regclass AND contype = 'c'"
            )
        )
        for (d,) in rows:
            if re.match(rf"CHECK \(\(?{column} = ANY", d) or d.startswith(f"CHECK ({column} "):
                return set(re.findall(r"'([^']+)'::text", d))
    raise AssertionError(column)


@pytest.mark.usefixtures("clean_db")
async def test_indexes_and_no_source_check(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        idx = {
            r[0]: r[1]
            for r in await conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'jobs'")
            )
        }
        for name in (
            "jobs_embedding_hnsw",
            "jobs_tsv_gin",
            "jobs_skills_gin",
            "jobs_title_trgm",
            "jobs_source_scope_active",
        ):
            assert name in idx
        hnsw = idx["jobs_embedding_hnsw"]
        assert "USING hnsw" in hnsw
        assert "status = 'active'" in hnsw and "canonical_job_id IS NULL" in hnsw
        defs = [
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'jobs'::regclass AND contype = 'c'"
                )
            )
        ]
        assert not any("source" in d and "source_job_id" not in d for d in defs)


async def test_check_sets_match_literals(engine: AsyncEngine) -> None:
    pairs = {
        "remote_eligibility": RemoteEligibility,
        "industry": db.Industry,
        "company_stage": db.CompanyStage,
        "company_size": db.CompanySize,
        "visa_sponsorship": db.VisaSponsorship,
        "relocation": db.Relocation,
    }
    for col, lit in pairs.items():
        assert await _check_values(engine, col) == set(typing.get_args(lit)), col


@pytest.mark.usefixtures("clean_db")
async def test_generated_tsv_and_defaults(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(f"INSERT INTO jobs ({_REQ}, skills_text) VALUES ({_VALS}, 'go nodejs')"),  # noqa: S608
            {"id": "1", "t": "Go developer"},
        )
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT tsv IS NOT NULL AS has_tsv, industry, company_stage, "
                        "company_size, visa_sponsorship, relocation, onsite_days_per_week, "
                        "repost_count, ghost_reasons, group_sources FROM jobs"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["has_tsv"] is True
        for k in ("industry", "company_stage", "company_size", "visa_sponsorship", "relocation"):
            assert row[k] == "unknown"
        assert row["onsite_days_per_week"] is None
        assert row["repost_count"] == 0
        assert row["ghost_reasons"] == [] and row["group_sources"] == []
        run = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO ingest_runs (source, status) VALUES ('s', 'running') "
                        "RETURNING in_scope, duplicates_grouped, ghost_flagged, reposts"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert dict(run) == {
            "in_scope": 0,
            "duplicates_grouped": 0,
            "ghost_flagged": 0,
            "reposts": 0,
        }


@pytest.mark.usefixtures("clean_db")
async def test_onsite_days_out_of_range_rejected(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(f"INSERT INTO jobs ({_REQ}, onsite_days_per_week) VALUES ({_VALS}, 8)"),  # noqa: S608
                {"id": "2", "t": "x"},
            )


def test_round_trip_downgrade_upgrade(database_url: str) -> None:
    assert os.environ["DATABASE_URL"] == database_url
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        command.downgrade(cfg, "0003")
        command.upgrade(cfg, "head")
    finally:
        command.upgrade(cfg, "head")


def test_rawjob_contract() -> None:
    base = {
        "source": "anything",
        "source_job_id": "1",
        "url": "https://example.com/j",
        "title": "t",
        "company_name": "c",
    }
    with pytest.raises(ValidationError):
        RawJob.model_validate({**base, "posted_at": datetime(2026, 1, 1)})
    job = RawJob.model_validate({**base, "posted_at": datetime(2026, 1, 1, tzinfo=UTC)})
    assert job.industry_hint is None and job.skills_hint == []
    assert job.company_size_hint is None and job.company_stage_hint is None
    assert job.description_raw == ""


def test_sources_config_and_registry() -> None:
    cfg = load_config("sources.yaml", SourcesConfig)
    assert cfg.root["remotive"].max_runs_per_day == 4
    assert [s.batch for s in cfg.root["remotive"].schedule] == ["morning", "intraday"]
    with pytest.raises(UnknownSource):
        get_adapter_factory("nope")
