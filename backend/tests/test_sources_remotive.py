import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import respx

from ranker.ingest import http as ingest_http
from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    get_adapter_factory,
    registered_sources,
)
from ranker.ingest.sources.remotive import RemotiveAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
URL = "https://remotive.com/api/remote-jobs"


def _adapter() -> RemotiveAdapter:
    ctx = AdapterContext(
        settings=cast(Any, None),
        engine=cast(Any, None),
        config=BaseSourceConfig.model_validate({"url": URL}),
        mode="incremental",
        now=datetime.now(UTC),
    )
    return cast(RemotiveAdapter, get_adapter_factory("remotive")(ctx))


def test_registered() -> None:
    assert "remotive" in registered_sources()


@respx.mock
async def test_fetch_fixture() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text())))
    a = _adapter()
    jobs = [j async for j in a.fetch()]
    assert len(jobs) == 8
    first = next(j for j in jobs if j.source_job_id == "1001")
    assert first.posted_at is not None and first.posted_at.tzinfo is not None
    assert first.posted_at.utcoffset() == UTC.utcoffset(None)
    assert "remotive.com" in str(first.url)
    assert first.workplace_hint == "remote"
    assert first.region_hint == "Worldwide"
    assert first.salary_text == "$35,3k- $52k"
    assert first.description_is_html is True
    assert first.category_hint == "Software Development"
    assert first.employment_type_hint == "full_time"
    # duplicate id: last wins
    assert next(j for j in jobs if j.source_job_id == "1003").title.endswith("(updated)")
    r = a.result()
    assert r.complete_scopes == {"all"} and r.calls_used == 1
    assert r.scope_counts == {"all": 8} and r.failed_scopes == {}


@respx.mock
async def test_invalid_item_skipped() -> None:
    payload = {"jobs": [{"id": 1, "url": "https://remotive.com/x", "company_name": "c"}]}
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))
    a = _adapter()
    assert [j async for j in a.fetch()] == []
    assert a.result().invalid_items == 1


@respx.mock
async def test_500_after_retries(monkeypatch: Any) -> None:
    async def _nosleep(_: float) -> None:
        return None

    real = ingest_http.get_json

    async def fast(*args: Any, **kw: Any) -> object:
        return await real(*args, sleep=_nosleep, **kw)

    monkeypatch.setattr("ranker.ingest.sources.remotive.get_json", fast)
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    a = _adapter()
    assert [j async for j in a.fetch()] == []
    r = a.result()
    assert r.complete_scopes == set()
    assert r.failed_scopes == {"all": "HTTPStatusError:500"}
    assert r.calls_used == 5 and route.call_count == 5
