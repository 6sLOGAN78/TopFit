import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.boards import BoardsConfig, boards_report
from ranker.ingest.pipeline import run_ingest
from ranker.ingest.sources import lever
from ranker.ingest.sources.base import AdapterContext, RawJob
from ranker.ingest.sources.lever import LeverAdapter, parse_board
from ranker.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "lever_postings.json"
ACME = "https://api.lever.co/v0/postings/acme"
BETA = "https://api.lever.co/v0/postings/beta"
ACME_EU = "https://api.eu.lever.co/v0/postings/acme"


def _payload() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(FIXTURE.read_text()))


def _adapter(
    engine: AsyncEngine, seeds: list[str], sleeps: list[float] | None = None
) -> LeverAdapter:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    get_settings.cache_clear()
    ctx = AdapterContext(
        settings=get_settings(),
        engine=engine,
        config=BoardsConfig(seeds=seeds, request_interval_seconds=1.0),
        mode="incremental",
        now=datetime.now(UTC),
    )
    return LeverAdapter(ctx, sleep=sleep)


async def _collect(adapter: LeverAdapter) -> list[RawJob]:
    return [job async for job in adapter.fetch()]


def _by_suffix() -> dict[str, RawJob]:
    jobs, invalid = parse_board(_payload(), "acme")
    assert invalid == 0
    return {j.source_job_id[-1]: j for j in jobs}


def test_created_at_is_epoch_milliseconds() -> None:
    j = _by_suffix()["1"]
    assert j.posted_at == datetime(2025, 11, 20, 12, 0, tzinfo=UTC)
    payload = _payload()
    payload[0]["createdAt"] = 10**18  # absurd
    payload[1]["createdAt"] = "1763640000000"  # wrong type
    jobs, _ = parse_board(payload, "acme")
    assert jobs[0].posted_at is None and jobs[1].posted_at is None


def test_workplace_country_locations_employment() -> None:
    jobs = _by_suffix()
    assert jobs["1"].workplace_hint == "onsite" and jobs["2"].workplace_hint == "onsite"
    assert jobs["3"].workplace_hint == "remote"
    assert jobs["6"].workplace_hint is None  # unspecified
    assert jobs["1"].country_hint == "IN" and jobs["4"].country_hint == "LU"
    assert jobs["1"].location_parts == ["Bengaluru", "Pune"]
    assert jobs["1"].employment_type_hint == "Full-time"
    assert jobs["6"].employment_type_hint == "Intern"
    assert jobs["1"].category_hint == "Engineering"
    assert jobs["1"].source_job_id == "11111111-1111-4111-8111-111111111101"


def test_description_plain_with_lists_and_html_fallback() -> None:
    jobs = _by_suffix()
    d = jobs["1"].description_raw
    assert jobs["1"].description_is_html is False
    assert d.startswith("Build Python") and "What you will do" in d
    assert "Design APIs" in d and "<li>" not in d and d.endswith("equal opportunity employer.")
    f = jobs["6"]
    assert f.description_is_html is True
    assert "<b>Spark</b>" in f.description_raw and "<li>SQL</li>" in f.description_raw


def test_salary_mapping() -> None:
    jobs = _by_suffix()
    j = jobs["1"]
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period_hint) == (
        2_000_000,
        3_000_000,
        "INR",
        "year",
    )
    f = jobs["6"]  # unknown interval -> no salary at all
    assert f.salary_min is None and f.salary_max is None
    assert f.salary_currency is None and f.salary_period_hint is None


@pytest.mark.usefixtures("clean_db")
async def test_per_board_isolation(engine: AsyncEngine) -> None:
    sleeps: list[float] = []
    adapter = _adapter(engine, ["acme", "beta"], sleeps)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        router.get(BETA).mock(return_value=httpx.Response(500))
        jobs = await _collect(adapter)
    result = adapter.result()
    assert {j.source_scope for j in jobs} == {"acme"} and len(jobs) == 6
    assert result.complete_scopes == {"acme"}
    assert result.failed_scopes == {"beta": "HTTPStatusError:500"}
    assert result.scope_counts == {"acme": 6}
    assert result.calls_used == 1 + 5
    assert 1.0 in sleeps
    assert route.calls.last.request.url.params["mode"] == "json"


@pytest.mark.usefixtures("clean_db")
async def test_eu_host_fallback_records_host(engine: AsyncEngine) -> None:
    adapter = _adapter(engine, ["acme"])
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(404))
        router.get(ACME_EU).mock(return_value=httpx.Response(200, json=_payload()))
        assert len(await _collect(adapter)) == 6
    row = (await boards_report(engine))[0]
    assert row.host == "eu" and row.status == "active"
    assert adapter.result().complete_scopes == {"acme"}

    # Next run goes straight to the EU host.
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME_EU).mock(return_value=httpx.Response(200, json=_payload()))
        assert len(await _collect(adapter)) == 6


@pytest.mark.usefixtures("clean_db")
async def test_404_on_both_hosts_drops_board(engine: AsyncEngine) -> None:
    adapter = _adapter(engine, ["acme"])
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(404))
        router.get(ACME_EU).mock(return_value=httpx.Response(404))
        assert await _collect(adapter) == []
    assert adapter.result().failed_scopes == {"acme": "HTTPStatusError:404"}
    assert (await boards_report(engine))[0].status == "dropped"


@pytest.mark.usefixtures("clean_db")
async def test_empty_registry_seeds_and_end_to_end(engine: AsyncEngine) -> None:
    called: list[int] = []
    real = lever.sync_seeds

    async def spy(eng: AsyncEngine, seeds: Any) -> int:
        called.append(1)
        return await real(eng, seeds)

    adapter = _adapter(engine, ["acme"])
    lever.sync_seeds = spy  # type: ignore[assignment]
    try:
        with respx.mock(assert_all_called=True) as router:
            router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
            summary = await run_ingest(engine, get_settings(), "lever", adapter=adapter)
    finally:
        lever.sync_seeds = real
    assert called == [1]
    assert summary.status == "succeeded" and summary.fetched == 6
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("SELECT source_job_id, source, source_scope, title FROM jobs")
                )
            )
            .mappings()
            .all()
        )
    titles = {r["title"] for r in rows}
    assert all(r["source"] == "lever" and r["source_scope"] == "acme" for r in rows)
    assert "Backend Engineer" in titles and "Machine Learning Engineer" in titles
    assert "Software Engineer" not in titles  # Luxembourg on-site: geo skip
    assert "Collections Manager" not in titles  # non-tech
    assert summary.skipped == 2
    assert [b.slug for b in await boards_report(engine)] == ["acme"]


# --- display names (plan 02-18) ---------------------------------------------------------------


def test_configured_company_name_and_slug_fallback() -> None:
    jobs, _ = parse_board(_payload(), "sarvam", company_name="Sarvam AI")
    assert jobs and {j.company_name for j in jobs} == {"Sarvam AI"}
    jobs, _ = parse_board(_payload(), "sarvam")
    assert {j.company_name for j in jobs} == {"Sarvam"}


@pytest.mark.usefixtures("clean_db")
async def test_adapter_uses_configured_company_names(engine: AsyncEngine) -> None:
    async def sleep(_: float) -> None:
        return None

    get_settings.cache_clear()
    ctx = AdapterContext(
        settings=get_settings(),
        engine=engine,
        config=BoardsConfig(seeds=["acme"], company_names={"acme": "ACME Corp"}),
        mode="incremental",
        now=datetime.now(UTC),
    )
    adapter = LeverAdapter(ctx, sleep=sleep)
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        jobs = await _collect(adapter)
    assert jobs and {j.company_name for j in jobs} == {"ACME Corp"}
