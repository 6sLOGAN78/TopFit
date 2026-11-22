import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import clear_config_cache, load_config
from ranker.ingest.boards import BoardsConfig, boards_report
from ranker.ingest.sources import ashby
from ranker.ingest.sources.ashby import AshbyAdapter, parse_board
from ranker.ingest.sources.base import AdapterContext, RawJob, SourcesConfig
from ranker.ingest.sources.boardbase import board_company_name
from ranker.settings import Settings, get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ashby_board.json"
ACME = "https://api.ashbyhq.com/posting-api/job-board/acme"
BETA = "https://api.ashbyhq.com/posting-api/job-board/beta"


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def _adapter(
    engine: AsyncEngine, seeds: list[str], sleeps: list[float] | None = None
) -> AshbyAdapter:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    ctx = AdapterContext(
        settings=_settings(),
        engine=engine,
        config=BoardsConfig(seeds=seeds, request_interval_seconds=1.0),
        mode="incremental",
        now=datetime.now(UTC),
    )
    return AshbyAdapter(ctx, sleep=sleep)


async def _collect(adapter: AshbyAdapter) -> list[RawJob]:
    return [job async for job in adapter.fetch()]


# --- mapping --------------------------------------------------------------------------------


def _by_suffix() -> dict[str, RawJob]:
    jobs, invalid = parse_board(_payload(), "acme")
    assert invalid == 0
    return {j.source_job_id[-1]: j for j in jobs}


def test_unlisted_jobs_skipped_and_ids_urls() -> None:
    jobs = _by_suffix()
    assert set(jobs) == {"1", "2", "3", "4", "6", "7"}  # 5 is isListed false
    j = jobs["1"]
    assert j.source == "ashby" and j.source_scope == "acme"
    assert str(j.url) == "https://jobs.ashbyhq.com/acme/a0000000-0000-4000-8000-000000000001"
    assert j.source_job_id == "a0000000-0000-4000-8000-000000000001"
    assert j.posted_at == datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
    assert j.category_hint == "Engineering"
    assert j.description_raw.startswith("Build Python") and j.description_is_html is False


def test_workplace_country_and_locations() -> None:
    jobs = _by_suffix()
    assert jobs["1"].workplace_hint == "onsite" and jobs["1"].country_hint == "IN"
    assert jobs["2"].workplace_hint == "remote" and jobs["2"].country_hint == "IN"
    assert jobs["2"].location_parts == ["Remote - India", "Mumbai", "Hyderabad"]
    assert jobs["3"].workplace_hint == "hybrid"
    assert jobs["4"].workplace_hint is None  # absent workplaceType and isRemote false
    assert jobs["7"].country_hint == "USA"


def test_employment_and_description_fallback() -> None:
    jobs = _by_suffix()
    assert jobs["1"].employment_type_hint == "full_time"
    assert jobs["3"].employment_type_hint == "part_time"
    assert jobs["4"].employment_type_hint == "internship"
    assert jobs["4"].description_is_html is True and "<b>Go</b>" in jobs["4"].description_raw


def test_salary_only_for_yearly_salary_components() -> None:
    jobs = _by_suffix()
    j = jobs["1"]
    assert (j.salary_min, j.salary_max, j.salary_currency) == (2_400_000, 3_600_000, "INR")
    assert j.salary_period_hint == "year"
    for k in ("2", "3", "4", "6"):  # empty, monthly interval, none, none
        assert jobs[k].salary_min is None and jobs[k].salary_max is None
        assert jobs[k].salary_period_hint is None


def test_hostile_values_do_not_crash() -> None:
    payload = _payload()
    payload["jobs"][0]["compensation"]["summaryComponents"][0]["maxValue"] = 10**400
    payload["jobs"][1]["publishedAt"] = "not-a-date"
    payload["jobs"].append({"id": "x"})  # missing title and url
    payload["jobs"].append("junk")
    jobs, invalid = parse_board(payload, "acme")
    assert invalid == 2
    by_id = {j.source_job_id[-1]: j for j in jobs}
    assert by_id["1"].salary_max is None and by_id["1"].salary_min == 2_400_000
    assert by_id["2"].posted_at is None


# --- fetch ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_db")
async def test_per_board_isolation(engine: AsyncEngine) -> None:
    sleeps: list[float] = []
    adapter = _adapter(engine, ["acme", "beta"], sleeps)
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        router.get(BETA).mock(return_value=httpx.Response(500))
        jobs = await _collect(adapter)
    result = adapter.result()
    assert {j.source_scope for j in jobs} == {"acme"} and len(jobs) == 6
    assert result.complete_scopes == {"acme"}
    assert result.failed_scopes == {"beta": "HTTPStatusError:500"}
    assert result.scope_counts == {"acme": 6}
    assert result.calls_used == 1 + 5  # one good call, five attempts on the failing board
    assert 1.0 in sleeps  # request spacing between boards
    rows = {b.slug: b for b in await boards_report(engine)}
    assert rows["acme"].status == "active" and rows["acme"].in_scope_count >= 3
    assert rows["beta"].status == "seed"  # a 500 never drops a board


@pytest.mark.usefixtures("clean_db")
async def test_empty_registry_fetches_yaml_seeds(engine: AsyncEngine) -> None:
    called: list[str] = []
    real = ashby.sync_seeds

    async def spy(eng: AsyncEngine, seeds: Any) -> int:
        called.append("sync")
        return await real(eng, seeds)

    adapter = _adapter(engine, ["acme"])
    with respx.mock(assert_all_called=True) as router:
        route = router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        ashby.sync_seeds = spy  # type: ignore[assignment]
        try:
            jobs = await _collect(adapter)
        finally:
            ashby.sync_seeds = real
    assert called == ["sync"] and len(jobs) == 6
    assert route.calls.last.request.url.params["includeCompensation"] == "true"
    assert [b.slug for b in await boards_report(engine)] == ["acme"]


@pytest.mark.usefixtures("clean_db")
async def test_404_drops_board(engine: AsyncEngine) -> None:
    adapter = _adapter(engine, ["acme"])
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(404))
        assert await _collect(adapter) == []
    assert adapter.result().failed_scopes == {"acme": "HTTPStatusError:404"}
    assert adapter.result().complete_scopes == set()
    assert (await boards_report(engine))[0].status == "dropped"


@pytest.mark.usefixtures("clean_db")
async def test_invalid_payload_is_failed_scope(engine: AsyncEngine) -> None:
    adapter = _adapter(engine, ["acme"])
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(200, json={"oops": 1}))
        await _collect(adapter)
    assert adapter.result().failed_scopes == {"acme": "InvalidPayload:None"}
    assert adapter.result().complete_scopes == set()


# --- display names (plan 02-18) ---------------------------------------------------------------


def test_configured_company_name_and_slug_fallback() -> None:
    jobs, _ = parse_board(_payload(), "sarvam", company_name="Sarvam AI")
    assert jobs and {j.company_name for j in jobs} == {"Sarvam AI"}
    jobs, _ = parse_board(_payload(), "acme-labs")
    assert {j.company_name for j in jobs} == {"Acme Labs"}


@pytest.mark.usefixtures("clean_db")
async def test_adapter_uses_configured_company_names(engine: AsyncEngine) -> None:
    async def sleep(_: float) -> None:
        return None

    ctx = AdapterContext(
        settings=_settings(),
        engine=engine,
        config=BoardsConfig(seeds=["acme"], company_names={"acme": "ACME Corp"}),
        mode="incremental",
        now=datetime.now(UTC),
    )
    adapter = AshbyAdapter(ctx, sleep=sleep)
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        jobs = await _collect(adapter)
    assert jobs and {j.company_name for j in jobs} == {"ACME Corp"}


def test_sources_yaml_company_names_cover_seeds() -> None:
    clear_config_cache()
    sections = load_config("sources.yaml", SourcesConfig).root
    for ats in ("greenhouse", "lever", "ashby"):
        cfg = BoardsConfig.model_validate(sections[ats].model_dump())
        assert set(cfg.company_names) >= set(cfg.seeds), ats
    ashby_cfg = BoardsConfig.model_validate(sections["ashby"].model_dump())
    assert ashby_cfg.company_names["sarvam"] == "Sarvam AI"


def test_company_names_sanitised() -> None:
    cfg = BoardsConfig(
        company_names={
            "ok": "  Okay Inc  ",
            "../bad": "Bad",
            "Upper": "Bad",
            "empty": "   ",
            "long": "x" * 81,
            "ctrl": "Evil\nCorp",
            "max": "y" * 80,
        }
    )
    assert cfg.company_names == {"ok": "Okay Inc", "max": "y" * 80}
    assert board_company_name("ok", cfg.company_names) == "Okay Inc"
    assert board_company_name("acme-labs", cfg.company_names) == "Acme Labs"
