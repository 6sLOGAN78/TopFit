import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from ranker import cli
from ranker.settings import get_settings

runner = CliRunner()


def _sql(url: str, stmt: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def go() -> list[Any]:
        eng = create_async_engine(url)
        try:
            async with eng.begin() as conn:
                res = await conn.execute(text(stmt), params or {})
                return list(res.mappings().all()) if res.returns_rows else []
        finally:
            await eng.dispose()

    return asyncio.run(go())


@pytest.fixture
def db(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    _sql(database_url, "TRUNCATE jobs, companies, ingest_runs RESTART IDENTITY CASCADE")
    insert = (
        "INSERT INTO jobs (source, source_job_id, url, title, description_raw, "
        "description_clean, content_hash, dedupe_key, normalizer_version, skills, skills_text, "
        "location_text, salary_text, posted_at, status) VALUES (:s, :sid, 'https://x.test/j', "
        "'Real title', 'raw', 'clean', 'h', :k, '1', ARRAY['python'], 'python', 'Pune', '10 LPA', "
        ":p, :st)"
    )
    rows = [
        ("adzuna", "1", "k", "2026-01-02", "active"),
        ("adzuna", "2", "k2", "2026-01-02", "expired"),
        ("remotive", "9", "k", "2026-01-01", "active"),
    ]
    for s, sid, k, p, st in rows:
        _sql(database_url, insert.replace(":p", f"'{p}'"), {"s": s, "sid": sid, "k": k, "st": st})
    # Group them as dedupe would: adzuna is canonical.
    _sql(
        database_url,
        "UPDATE jobs SET canonical_job_id = (SELECT id FROM jobs WHERE source = 'adzuna' "
        "AND source_job_id = '1') WHERE source = 'remotive'",
    )
    yield database_url
    get_settings.cache_clear()


def test_requires_yes_changes_nothing(db: str) -> None:
    res = runner.invoke(cli.app, ["purge-source", "adzuna"])
    assert res.exit_code == 2
    n = _sql(db, "SELECT count(*) AS n FROM jobs WHERE status <> 'removed'")[0]["n"]
    assert n == 3


def test_unknown_source_exit_2(db: str) -> None:
    assert runner.invoke(cli.app, ["purge-source", "nope", "--yes"]).exit_code == 2
    assert runner.invoke(cli.app, ["expire-ttl", "nope"]).exit_code == 2


def test_purge_clears_content_and_promotes_other_source(db: str) -> None:
    res = runner.invoke(cli.app, ["purge-source", "adzuna", "--yes"])
    assert res.exit_code == 0
    assert json.loads(res.stdout.strip().splitlines()[-1]) == {"purged": 2}
    rows = _sql(db, "SELECT * FROM jobs WHERE source = 'adzuna'")
    assert len(rows) == 2
    for r in rows:
        assert r["status"] == "removed" and r["title"] == "[removed]" and r["url"] == ""
        assert r["description_raw"] == "" and r["description_clean"] == ""
        assert r["skills"] == [] and r["skills_text"] == "" and r["location_text"] is None
        assert r["salary_text"] is None and r["embedding"] is None
        assert r["ghost_reasons"] == [] and r["group_sources"] == []
    other = _sql(db, "SELECT * FROM jobs WHERE source = 'remotive'")[0]
    assert other["status"] == "active" and other["title"] == "Real title"
    assert other["canonical_job_id"] is None  # remotive is now canonical
    assert other["group_sources"] == ["remotive"]


def test_expire_ttl_command(db: str) -> None:
    res = runner.invoke(cli.app, ["expire-ttl", "adzuna"])
    assert res.exit_code == 0
    # Both active rows are older than ttl_days relative to now (posted January 2026).
    assert json.loads(res.stdout.strip().splitlines()[-1]) == {"expired": 1}
    assert runner.invoke(cli.app, ["expire-ttl", "remotive"]).exit_code == 2
