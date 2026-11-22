import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.boards import BoardsConfig, BoardSeed, boards_report, sync_seeds
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.sources import greenhouse
from ranker.ingest.sources.base import AdapterContext, RawJob
from ranker.ingest.sources.greenhouse import GreenhouseAdapter, parse_board
from ranker.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_board.json"
ACME = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
BETA = "https://boards-api.greenhouse.io/v1/boards/beta/jobs"


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _adapter(
    engine: AsyncEngine, seeds: list[str], sleeps: list[float] | None = None
) -> GreenhouseAdapter:
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
    return GreenhouseAdapter(ctx, sleep=sleep)


async def _collect(adapter: GreenhouseAdapter) -> list[RawJob]:
    return [job async for job in adapter.fetch()]


def test_mapping() -> None:
    jobs, invalid = parse_board(_payload(), "acme")
    assert invalid == 1  # the posting without url
    by_id = {j.source_job_id: j for j in jobs}
    assert set(by_id) == {"4001001", "4001002", "4001003", "4001004"}
    j = by_id["4001001"]
    assert j.source == "greenhouse" and j.source_scope == "acme"
    assert str(j.url) == "https://job-boards.greenhouse.io/acme/jobs/4001001"
    assert j.company_name == "Acme Corp"
    assert j.description_is_html and j.description_entity_escaped
    assert j.description_raw.startswith("&lt;p&gt;")
    assert j.category_hint == "Engineering"
    assert j.location_text == "Bengaluru, India"
    assert j.posted_at == datetime(2026, 11, 5, 13, 30, tzinfo=UTC)
    assert j.posted_at is not None and j.posted_at.utcoffset() == timedelta(0)
    # Fallback to updated_at when first_published is absent; company from the slug.
    assert by_id["4001002"].posted_at == datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    assert by_id["4001002"].company_name == "Acme"


def test_entity_escaped_content_cleans_to_text() -> None:
    j = parse_board(_payload(), "acme")[0][0]
    text = clean_description(j.description_raw, is_html=True, entity_escaped=True)
    assert "<" not in text and "&lt;" not in text
    assert "Python" in text and "& APIs" in text


def test_timezone_naive_dates_become_utc() -> None:
    payload = _payload()
    payload["jobs"][0]["first_published"] = "2026-11-05T08:30:00"
    jobs, _ = parse_board(payload, "acme")
    assert jobs[0].posted_at == datetime(2026, 11, 5, 8, 30, tzinfo=UTC)


@pytest.mark.usefixtures("clean_db")
async def test_per_board_isolation(engine: AsyncEngine) -> None:
    sleeps: list[float] = []
    adapter = _adapter(engine, ["acme", "beta"], sleeps)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        router.get(BETA).mock(return_value=httpx.Response(500))
        jobs = await _collect(adapter)
    result = adapter.result()
    assert {j.source_scope for j in jobs} == {"acme"} and len(jobs) == 4
    assert result.complete_scopes == {"acme"}
    assert result.failed_scopes == {"beta": "HTTPStatusError:500"}
    assert result.scope_counts == {"acme": 4}
    assert result.invalid_items == 1
    assert result.calls_used == 1 + 5
    assert 1.0 in sleeps
    assert route.calls.last.request.url.params["content"] == "true"
    assert route.calls.last.request.headers["user-agent"].startswith("RankerBot/")


@pytest.mark.usefixtures("clean_db")
async def test_empty_registry_seeds_and_dropped_not_resurrected(engine: AsyncEngine) -> None:
    called: list[int] = []
    real = greenhouse.sync_seeds

    async def spy(eng: AsyncEngine, seeds: Any) -> int:
        called.append(1)
        return await real(eng, seeds)

    adapter = _adapter(engine, ["acme"])
    greenhouse.sync_seeds = spy  # type: ignore[assignment]
    try:
        with respx.mock(assert_all_called=True) as router:
            router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
            assert len(await _collect(adapter)) == 4
    finally:
        greenhouse.sync_seeds = real
    assert called == [1]
    row = (await boards_report(engine))[0]
    assert row.slug == "acme" and row.status == "active" and row.in_scope_count >= 1

    # A 404 drops the board; the next fetch (which re-syncs the seed) does not resurrect it.
    with respx.mock(assert_all_called=True) as router:
        router.get(ACME).mock(return_value=httpx.Response(404))
        assert await _collect(adapter) == []
    assert (await boards_report(engine))[0].status == "dropped"
    await sync_seeds(engine, [BoardSeed(ats="greenhouse", slug="acme")])
    with respx.mock(assert_all_called=False) as router:
        route = router.get(ACME).mock(return_value=httpx.Response(200, json=_payload()))
        assert await _collect(adapter) == []
        assert not route.called
