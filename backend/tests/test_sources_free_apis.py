import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    apiutil,
    get_adapter_factory,
    registered_sources,
)
from ranker.ingest.sources.base import RawJob, SourceAdapter

FIX = Path(__file__).parent / "fixtures"
AN = "https://www.arbeitnow.com/api/job-board-api"
RO = "https://remoteok.com/api"
JB = "https://jobicy.com/api/v2/remote-jobs"


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text())


def _adapter(name: str, **config: Any) -> SourceAdapter:
    ctx = AdapterContext(
        settings=cast(Any, None),
        engine=cast(Any, None),
        config=BaseSourceConfig.model_validate(config),
        mode="incremental",
        now=datetime.now(UTC),
    )
    return get_adapter_factory(name)(ctx)


async def _collect(adapter: SourceAdapter) -> list[RawJob]:
    return [job async for job in adapter.fetch()]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(apiutil, "sleep", fake)
    return sleeps


def test_registered() -> None:
    assert {"arbeitnow", "remoteok", "jobicy"} <= set(registered_sources())
    assert "himalayas" not in registered_sources()


@respx.mock
async def test_arbeitnow_pages_and_mapping(no_sleep: list[float]) -> None:
    route = respx.get(AN).mock(
        side_effect=[
            httpx.Response(200, json=_load("arbeitnow_page1.json")),
            httpx.Response(200, json=_load("arbeitnow_page2.json")),
        ]
    )
    a = _adapter("arbeitnow", min_interval_seconds=1.0)
    jobs = await _collect(a)
    assert route.call_count == 2
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]
    res = a.result()
    assert res.complete_scopes == {"all"} and res.failed_scopes == {}
    assert res.scope_counts == {"all": 3} and res.invalid_items == 1 and res.calls_used == 2
    assert no_sleep == [1.0]
    by = {j.source_job_id: j for j in jobs}
    remote = by["python-dev-remote-2"]
    assert remote.workplace_hint == "remote" and remote.skills_hint == ["Python", "Django"]
    assert by["backend-engineer-acme-1"].workplace_hint is None
    assert by["backend-engineer-acme-1"].location_text == "Berlin"
    assert by["backend-engineer-acme-1"].employment_type_hint == "full time"
    assert by["backend-engineer-acme-1"].posted_at == datetime.fromtimestamp(1763640000, tz=UTC)
    assert str(remote.url) == "https://www.arbeitnow.com/jobs/companies/python-dev-remote-2"
    assert remote.source == "arbeitnow" and remote.description_is_html


@respx.mock
async def test_arbeitnow_page_cap_blocks_expiry() -> None:
    respx.get(AN).mock(return_value=httpx.Response(200, json=_load("arbeitnow_page1.json")))
    a = _adapter("arbeitnow", max_pages=1, min_interval_seconds=0)
    jobs = await _collect(a)
    assert len(jobs) == 2
    res = a.result()
    assert res.complete_scopes == set() and res.failed_scopes == {"all": "page_cap"}


@respx.mock
async def test_arbeitnow_5xx_marks_scope_failed() -> None:
    route = respx.get(AN).mock(return_value=httpx.Response(503))
    a = _adapter("arbeitnow")
    assert await _collect(a) == []
    res = a.result()
    assert res.failed_scopes == {"all": "HTTPStatusError:503"} and res.complete_scopes == set()
    assert res.calls_used == route.call_count == 5


@respx.mock
async def test_remoteok_skips_legal_notice_and_maps() -> None:
    respx.get(RO).mock(return_value=httpx.Response(200, json=_load("remoteok.json")))
    a = _adapter("remoteok")
    jobs = await _collect(a)
    res = a.result()
    assert res.complete_scopes == set() and res.failed_scopes == {}
    assert res.invalid_items == 1 and res.scope_counts == {"all": 3}  # 1004 has no position
    assert a.expiry_policy == "ttl"
    by = {j.source_job_id: j for j in jobs}
    assert set(by) == {"1001", "1002", "1003"}
    j1 = by["1001"]
    assert str(j1.url).startswith("https://remoteok.com/remote-jobs/")
    assert (j1.salary_min, j1.salary_max) == (90000, 130000)
    assert j1.salary_currency == "USD" and j1.salary_period_hint == "year"
    assert j1.skills_hint == ["python", "backend"] and j1.region_hint == "Worldwide"
    assert j1.posted_at == datetime(2025, 11, 20, 12, 0, tzinfo=UTC)
    assert by["1002"].salary_min is None and by["1002"].region_hint == "India"
    assert by["1002"].posted_at == datetime(2025, 11, 21, 8, 30, tzinfo=UTC)
    assert by["1003"].region_hint is None and by["1003"].salary_max is None


@respx.mock
async def test_remoteok_error_is_failed_scope() -> None:
    respx.get(RO).mock(return_value=httpx.Response(500))
    a = _adapter("remoteok")
    assert await _collect(a) == []
    assert a.result().failed_scopes == {"all": "HTTPStatusError:500"}


@respx.mock
async def test_jobicy_params_and_mapping() -> None:
    route = respx.get(JB).mock(return_value=httpx.Response(200, json=_load("jobicy.json")))
    a = _adapter("jobicy")
    jobs = await _collect(a)
    params = route.calls[0].request.url.params
    assert dict(params) == {"count": "50"}  # no geo filter: geo=india returned HTTP 400
    res = a.result()
    assert res.invalid_items == 1 and res.complete_scopes == set() and a.expiry_policy == "ttl"
    by = {j.source_job_id: j for j in jobs}
    j = by["2001"]
    assert j.industry_hint == "Fintech" and j.region_hint == "Anywhere"
    assert j.employment_type_hint == "Full-Time" and "Level: Senior" in j.description_raw
    assert j.posted_at == datetime(2025, 11, 20, 9, 0, tzinfo=UTC)  # naive means UTC
    assert (j.salary_min, j.salary_max, j.salary_currency) == (80000, 120000, "USD")
    assert j.salary_period_hint == "year"
    k = by["2002"]
    assert k.salary_min is None and k.posted_at == datetime(2025, 11, 21, 4, 30, tzinfo=UTC)


def test_sources_yaml_sections_load() -> None:
    import yaml

    raw = yaml.safe_load((Path(__file__).parents[1] / "config" / "sources.yaml").read_text())
    assert "himalayas" not in raw
    for name, policy in (("arbeitnow", "absence"), ("remoteok", "ttl"), ("jobicy", "ttl")):
        cfg = BaseSourceConfig.model_validate(raw[name])
        assert cfg.enabled and cfg.expiry_policy == policy and cfg.max_runs_per_day == 4
        _adapter(name, **raw[name])
