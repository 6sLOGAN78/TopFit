import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest import pipeline
from ranker.ingest.pipeline import (
    RunQuotaReached,
    SourceLocked,
    SourceNotConfigured,
    run_ingest,
)
from ranker.ingest.sources.base import FetchResult, RawJob, UnknownSource
from ranker.settings import Settings, get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
URL = "https://remotive.com/api/remote-jobs"

pytestmark = pytest.mark.usefixtures("clean_db")


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _serve(payload: dict[str, Any] | None = None) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload or _payload()))


def _settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


async def _snapshot(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT source_job_id, title, description_clean, content_hash, skills, "
                "updated_at, normalizer_version, dedupe_key FROM jobs ORDER BY source_job_id"
            )
        )
        return [dict(r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]


@respx.mock
async def test_insert_rerun_and_change(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    _serve()
    summary = await run_ingest(engine, settings, "remotive")
    assert summary.status == "succeeded"
    assert (summary.fetched, summary.inserted, summary.skipped, summary.failed) == (8, 6, 2, 0)

    async with engine.connect() as conn:
        run = (
            (
                await conn.execute(
                    text("SELECT * FROM ingest_runs WHERE id = :i"), {"i": summary.run_id}
                )
            )
            .mappings()
            .one()
        )
        assert run["in_scope"] == 6 and run["inserted"] == 6 and run["skipped"] == 2
        assert run["finished_at"] is not None and run["calls_used"] == 1
        assert len(run["notes"]["skipped_samples"]) == 2
        jobs = (
            (await conn.execute(text("SELECT * FROM jobs ORDER BY source_job_id"))).mappings().all()
        )
    assert len(jobs) == 6
    for j in jobs:
        assert j["source"] == "remotive"
        assert j["url"].startswith("https://remotive.com/")
        assert j["remote_type"] == "remote"
        assert j["status"] == "active"
        assert j["embedding"] is not None  # embedded in the same run (plan 02-09)
        assert j["company_id"] is not None
        assert j["skills"]
        assert j["first_seen_run_id"] == summary.run_id
    by_id = {j["source_job_id"]: j for j in jobs}
    assert by_id["1001"]["remote_eligibility"] == "worldwide"
    assert by_id["1002"]["remote_eligibility"] == "us_only"
    assert by_id["1002"]["salary_min_inr_annual"] is not None
    assert by_id["1002"]["salary_fx_as_of"] is not None

    # Identical re-run: nothing changes and normalize_job is never called.
    before = await _snapshot(engine)
    calls: list[str] = []
    real = pipeline.normalize_job

    def spy(raw: RawJob) -> Any:
        calls.append(raw.source_job_id)
        return real(raw)

    monkeypatch.setattr(pipeline, "normalize_job", spy)
    again = await run_ingest(engine, settings, "remotive")
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 6)
    assert again.skipped == 2  # out-of-scope items are re-evaluated every run
    assert await _snapshot(engine) == before
    assert calls and set(calls) == {"1007", "1008"}  # only the two never-stored skips

    # Changing one description updates exactly that row.
    payload = _payload()
    for item in payload["jobs"]:
        if item["id"] == 1001:
            item["description"] = "<p>Completely new Rust and Kafka text.</p>"
    respx.clear()
    _serve(payload)
    calls.clear()
    changed = await run_ingest(engine, settings, "remotive")
    assert (changed.updated, changed.unchanged, changed.inserted) == (1, 5, 0)
    after = {r["source_job_id"]: r for r in await _snapshot(engine)}
    old = {r["source_job_id"]: r for r in before}
    assert after["1001"]["content_hash"] != old["1001"]["content_hash"]
    assert after["1001"]["description_clean"] != old["1001"]["description_clean"]
    assert after["1001"]["updated_at"] > old["1001"]["updated_at"]
    assert after["1002"] == old["1002"]
    assert "1001" in calls


@respx.mock
async def test_normalizer_version_bump_renormalises(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings()
    _serve()
    await run_ingest(engine, settings, "remotive")
    monkeypatch.setattr(pipeline, "NORMALIZER_VERSION", "2")
    monkeypatch.setattr("ranker.ingest.normalize.job.NORMALIZER_VERSION", "2")
    again = await run_ingest(engine, settings, "remotive")
    assert again.updated == 6 and again.unchanged == 0


async def test_lock_held(engine: AsyncEngine) -> None:
    settings = _settings()
    async with engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(hashtext('ingest:remotive'))"))
        try:
            with pytest.raises(SourceLocked):
                await run_ingest(engine, settings, "remotive")
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(hashtext('ingest:remotive'))"))
    async with engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM ingest_runs"))).scalar_one()
    assert n == 0


async def test_runs_per_day_cap(engine: AsyncEngine) -> None:
    settings = _settings()
    async with engine.begin() as conn:
        for _ in range(4):
            await conn.execute(
                text("INSERT INTO ingest_runs (source, status) VALUES ('remotive', 'succeeded')")
            )
    with pytest.raises(RunQuotaReached):
        await run_ingest(engine, settings, "remotive")


async def test_unknown_source(engine: AsyncEngine) -> None:
    with pytest.raises(UnknownSource):
        await run_ingest(engine, _settings(), "nope")


class _Stub:
    name = "stub"
    expiry_policy = "absence"

    def __init__(self, *, fail: bool = False, failed_scopes: bool = False, ok: bool = True) -> None:
        self._fail, self._failed_scopes, self._ok = fail, failed_scopes, ok
        self._result = FetchResult(calls_used=3)

    def configured(self) -> bool:
        return self._ok

    async def fetch(self) -> AsyncIterator[RawJob]:
        if self._fail:
            raise RuntimeError("secret text that must not be stored")
        if self._failed_scopes:
            self._result.failed_scopes["all"] = "HTTPStatusError:500"
        yield RawJob.model_validate(
            {
                "source": "remotive",
                "source_job_id": "9",
                "url": "https://remotive.com/x",
                "title": "Backend Engineer",
                "company_name": "Foo",
                "description_raw": "<p>Python</p>",
                "description_is_html": True,
                "workplace_hint": "remote",
                "region_hint": "Worldwide",
            }
        )

    def result(self) -> FetchResult:
        return self._result


async def test_partial_status(engine: AsyncEngine) -> None:
    s = await run_ingest(
        engine, _settings(), "remotive", adapter=cast(Any, _Stub(failed_scopes=True))
    )
    assert s.status == "partial" and s.inserted == 1 and s.calls_used == 3
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT notes, finished_at FROM ingest_runs"))).one()
    assert row[0]["failed_scopes"] == {"all": "HTTPStatusError:500"}
    assert row[1] is not None


async def test_failed_status_redacted(engine: AsyncEngine) -> None:
    s = await run_ingest(engine, _settings(), "remotive", adapter=cast(Any, _Stub(fail=True)))
    assert s.status == "failed"
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT errors, finished_at FROM ingest_runs"))).one()
    assert row[1] is not None
    assert "secret" not in json.dumps(row[0])
    assert row[0][0]["error"].startswith("RuntimeError")


async def test_not_configured(engine: AsyncEngine) -> None:
    with pytest.raises(SourceNotConfigured):
        await run_ingest(engine, _settings(), "remotive", adapter=cast(Any, _Stub(ok=False)))
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM ingest_runs"))).scalar_one() == 0
