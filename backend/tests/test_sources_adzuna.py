import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    get_adapter_factory,
    registered_sources,
)
from ranker.ingest.sources.adzuna import AdzunaAdapter
from ranker.settings import Settings

pytestmark = pytest.mark.usefixtures("clean_db")

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SEARCH = "https://api.adzuna.com/v1/api/jobs/in/search/"
PAGE1 = json.loads((FIX / "adzuna_search_page1.json").read_text())
PAGE2 = json.loads((FIX / "adzuna_search_page2.json").read_text())

IT = {"name": "it", "category": "it-jobs", "max_days_old": 2, "max_pages": 40}
DATA = {"name": "data_ml", "what_or": "data scientist", "max_days_old": 2, "max_pages": 5}


def _settings(*, keys: bool = True) -> Settings:
    extra: dict[str, Any] = {"adzuna_app_id": "ID123", "adzuna_app_key": "KEY456"} if keys else {}
    return Settings(database_url="postgresql+psycopg://x/x", session_secret="s", **extra)  # pyright: ignore[reportArgumentType]


def _config(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "expiry_policy": "ttl",
        "results_per_page": 3,
        "min_interval_seconds": 2.5,
        "quota": {"calls_per_day": 100, "calls_per_month": 1000},
        "queries": [IT, DATA],
        "backfill": {"auto": False, "max_days_old": 30, "max_pages": 200},
    }
    cfg.update(over)
    return cfg


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _adapter(
    engine: AsyncEngine,
    sleeps: Sleeps,
    *,
    mode: str = "incremental",
    settings: Settings | None = None,
    **over: Any,
) -> AdzunaAdapter:
    ctx = AdapterContext(
        settings=settings or _settings(),
        engine=engine,
        config=BaseSourceConfig.model_validate(_config(**over)),
        mode=cast(Any, mode),
        now=NOW,
    )
    return AdzunaAdapter(ctx, sleep=sleeps)


async def _add_run(
    engine: AsyncEngine,
    started: datetime,
    calls: int,
    notes: dict[str, Any] | None = None,
    mode: str = "incremental",
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ingest_runs (source, mode, started_at, status, calls_used, notes) "
                "VALUES ('adzuna', :mode, :started, 'succeeded', :calls, CAST(:notes AS jsonb))"
            ),
            {"mode": mode, "started": started, "calls": calls, "notes": json.dumps(notes or {})},
        )


def _short(job_id: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "id": job_id,
                "title": "Data Scientist",
                "redirect_url": f"https://www.adzuna.in/land/ad/{job_id}",
                "company": {"display_name": "Hooli"},
            }
        ]
    }


def _router(request: httpx.Request) -> httpx.Response:
    page = int(request.url.path.rsplit("/", 1)[1])
    if "what_or" in request.url.params:
        return httpx.Response(200, json=_short("9100"))
    return httpx.Response(200, json=PAGE1 if page == 1 else PAGE2)


def test_registered_and_configured(engine: AsyncEngine) -> None:
    assert "adzuna" in registered_sources()
    assert _adapter(engine, Sleeps()).configured()
    assert not _adapter(engine, Sleeps(), settings=_settings(keys=False)).configured()
    ctx = AdapterContext(
        settings=_settings(keys=False),
        engine=engine,
        config=BaseSourceConfig.model_validate(_config()),
        mode="incremental",
        now=NOW,
    )
    assert not get_adapter_factory("adzuna")(ctx).configured()


@respx.mock
async def test_paging_slices_and_mapping(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(side_effect=_router)
    sleeps = Sleeps()
    a = _adapter(engine, sleeps)
    jobs = {j.source_job_id: j async for j in a.fetch()}
    assert set(jobs) == {"9001", "9002", "9004", "9100"}
    r = a.result()
    assert r.calls_used == 3 and route.call_count == 3
    assert r.scope_counts == {"search": 4}
    assert r.complete_scopes == set()
    assert r.invalid_items == 1
    assert r.failed_scopes == {}
    assert sleeps.calls == [2.5, 2.5]

    first = jobs["9001"]
    assert str(first.url) == "https://www.adzuna.in/land/ad/9001?se=synthetic"
    assert first.title == "Senior Python Developer"
    assert first.company_name == "Acme Payments"
    assert first.company_canonical_hint == "acme payments"
    assert first.location_text == "Gurgaon, Haryana"
    assert first.location_parts == ["India", "Haryana", "Gurgaon"]
    assert first.country_hint == "IN"
    assert first.posted_at == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    assert first.salary_is_estimate is True
    assert (first.salary_min, first.salary_max) == (1200000.0, 1800000.0)
    assert first.salary_currency == "INR" and first.salary_period_hint == "year"
    assert first.employment_type_hint == "full_time"
    assert first.contract_type_hint == "permanent"
    assert first.category_hint == "it-jobs"
    assert first.source_scope == "search" and first.source == "adzuna"
    assert first.description_truncated is True

    second = jobs["9002"]
    assert second.description_truncated is False
    assert second.salary_currency is None and second.salary_is_estimate is False

    assert jobs["9004"].salary_is_estimate is False  # integer 0


@respx.mock
async def test_credentials_only_in_query_params(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(side_effect=_router)
    a = _adapter(engine, Sleeps(), queries=[IT])
    _ = [j async for j in a.fetch()]
    for call in route.calls:
        request = cast(httpx.Request, call.request)
        assert request.url.params["app_id"] == "ID123"
        assert request.url.params["app_key"] == "KEY456"
        assert "ID123" not in request.url.path and "KEY456" not in request.url.path
        assert request.url.params["sort_by"] == "date"
        assert request.url.params["category"] == "it-jobs"
        assert request.url.params["max_days_old"] == "2"


@respx.mock
async def test_retries_count_against_quota(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=_short("9100")),
        ]
    )
    a = _adapter(engine, Sleeps(), queries=[IT])
    jobs = [j async for j in a.fetch()]
    assert len(jobs) == 1
    assert a.result().calls_used == 3 and route.call_count == 3


@respx.mock
async def test_daily_budget_limits_calls(engine: AsyncEngine) -> None:
    await _add_run(engine, datetime(2026, 11, 22, 1, 0, tzinfo=UTC), 4)
    full = httpx.Response(200, json=PAGE1)
    route = respx.get(url__startswith=SEARCH).mock(return_value=full)
    a = _adapter(engine, Sleeps(), quota={"calls_per_day": 5, "calls_per_month": 100})
    _ = [j async for j in a.fetch()]
    assert route.call_count == 1 and a.result().calls_used == 1
    assert a.result().extra_notes["budget_exhausted"] is True


@respx.mock
async def test_monthly_cap_blocks_all_calls(engine: AsyncEngine) -> None:
    # yesterday counts toward the month but not today's day budget
    await _add_run(engine, datetime(2026, 11, 21, 23, 59, tzinfo=UTC), 10)
    route = respx.get(url__startswith=SEARCH).mock(return_value=httpx.Response(200, json=PAGE1))
    a = _adapter(engine, Sleeps(), quota={"calls_per_day": 100, "calls_per_month": 10})
    assert [j async for j in a.fetch()] == []
    assert route.call_count == 0 and a.result().calls_used == 0
    assert a.result().extra_notes["budget_exhausted"] is True


@respx.mock
async def test_zero_budget_makes_no_calls(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(return_value=httpx.Response(200, json=PAGE1))
    a = _adapter(engine, Sleeps(), quota={"calls_per_day": 0, "calls_per_month": 0})
    assert [j async for j in a.fetch()] == []
    assert route.call_count == 0


@respx.mock
async def test_429_ends_crawl_partial(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(
        side_effect=[httpx.Response(200, json=PAGE1)] + [httpx.Response(429)] * 5
    )
    a = _adapter(engine, Sleeps())
    jobs = [j async for j in a.fetch()]
    assert {j.source_job_id for j in jobs} == {"9001", "9002"}
    r = a.result()
    assert r.failed_scopes == {"search": "HTTPStatusError:429"}
    assert route.call_count == 6 and r.calls_used == 6  # data_ml slice never started


@respx.mock
async def test_backfill_resumes_from_cursor(engine: AsyncEngine) -> None:
    await _add_run(
        engine,
        datetime(2026, 11, 21, 22, 0, tzinfo=UTC),
        3,
        notes={"backfill_cursor": {"it": 2, "data_ml": 1}},
        mode="backfill",
    )
    seen: list[tuple[str, str, str]] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.path.rsplit("/", 1)[1],
                request.url.params["max_days_old"],
                "data" if "what_or" in request.url.params else "it",
            )
        )
        if "what_or" in request.url.params:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json=PAGE2)

    respx.get(url__startswith=SEARCH).mock(side_effect=route)
    a = _adapter(engine, Sleeps(), mode="backfill")
    jobs = [j async for j in a.fetch()]
    assert [j.source_job_id for j in jobs] == ["9004"]
    assert seen == [("2", "30", "it"), ("1", "30", "data")]
    notes = a.result().extra_notes
    assert notes["backfill_cursor"] == {"it": 0, "data_ml": 0}
    assert notes["backfill_complete"] is True


@respx.mock
async def test_backfill_cursor_advances_when_budget_ends(engine: AsyncEngine) -> None:
    respx.get(url__startswith=SEARCH).mock(return_value=httpx.Response(200, json=PAGE1))
    a = _adapter(
        engine,
        Sleeps(),
        mode="backfill",
        quota={"calls_per_day": 2, "calls_per_month": 100},
    )
    _ = [j async for j in a.fetch()]
    notes = a.result().extra_notes
    assert notes["backfill_cursor"] == {"it": 3, "data_ml": 1}
    assert notes["backfill_complete"] is False


@respx.mock
async def test_auto_backfill_until_complete(engine: AsyncEngine) -> None:
    route = respx.get(url__startswith=SEARCH).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    over = {"backfill": {"auto": True, "max_days_old": 30, "max_pages": 200}}
    a = _adapter(engine, Sleeps(), backfill=over["backfill"])
    _ = [j async for j in a.fetch()]
    # two incremental slices plus two backfill slices, all short pages
    assert route.call_count == 4
    assert a.result().extra_notes["backfill_complete"] is True

    await _add_run(
        engine,
        datetime(2026, 11, 22, 3, 0, tzinfo=UTC),
        0,
        notes={"backfill_complete": True, "backfill_cursor": {"it": 0, "data_ml": 0}},
    )
    route.reset()
    b = _adapter(engine, Sleeps(), backfill=over["backfill"])
    _ = [j async for j in b.fetch()]
    assert route.call_count == 2
    assert "backfill_complete" not in b.result().extra_notes
