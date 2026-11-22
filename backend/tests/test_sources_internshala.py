from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from ranker.ingest.sources import AdapterContext, BaseSourceConfig, apiutil, registered_sources
from ranker.ingest.sources.base import RawJob
from ranker.ingest.sources.internshala import (
    InternshalaAdapter,
    is_listing_path,
    parse_age,
    parse_listing_page,
)

FIX = Path(__file__).parent / "fixtures"
HOST = "https://internshala.com"
NOW = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)


def _text(name: str) -> str:
    return (FIX / name).read_text()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(apiutil, "sleep", fake)
    return sleeps


def _adapter(**config: Any) -> InternshalaAdapter:
    ctx = AdapterContext(
        settings=cast(Any, None),
        engine=cast(Any, None),
        config=BaseSourceConfig.model_validate(config),
        mode="incremental",
        now=NOW,
    )
    return InternshalaAdapter(ctx)


async def _collect(a: InternshalaAdapter) -> list[RawJob]:
    return [j async for j in a.fetch()]


def _serve_robots() -> None:
    respx.get(f"{HOST}/robots.txt").mock(
        return_value=httpx.Response(200, text=_text("internshala_robots.txt"))
    )


def test_registered() -> None:
    assert "internshala" in registered_sources()


@pytest.mark.parametrize(
    ("target", "ok"),
    [
        ("/jobs/python-jobs/", True),
        ("/jobs/python-jobs/page-2/", True),
        ("/internships/machine-learning-internship/page-12/", True),
        (f"{HOST}/jobs/java-jobs/", True),
        ("/job/details/x1", False),
        ("/job/detail/x1", False),
        ("/jobs/python-jobs/?page=2", False),
        ("/jobs/python,java-jobs/", False),
        ("/jobs/python-jobs", False),
        ("/jobs/python-internship/", False),
        ("https://evil.example/jobs/python-jobs/", False),
        ("/login", False),
    ],
)
def test_path_guard(target: str, ok: bool) -> None:
    assert is_listing_path(target) is ok


def test_parse_listing_page_jobs() -> None:
    page = parse_listing_page(_text("internshala_jobs_p1.html"), "jobs")
    assert [c.source_job_id for c in page.cards] == ["1001", "1002", "1003"]  # ad skipped
    assert page.invalid == 0 and page.total_listed == 75 and page.pages_available == 2
    c = page.cards[0]
    assert c.url == f"{HOST}/job/detail/python-developer-job-at-acme-labs1001"
    assert (c.title, c.company) == ("Python Developer", "Acme Labs")
    assert c.locations == ["Bangalore", "Pune"]
    assert c.salary == "₹ 3,00,000 - 4,50,000 /year"
    assert c.experience == "1-2 years" and c.skills == ["Python", "Django"]
    assert c.posted_age == "3 days ago" and not c.part_time
    assert page.cards[2].part_time


def test_parse_listing_page_internship_has_no_experience() -> None:
    page = parse_listing_page(_text("internshala_internships_p1.html"), "internships")
    assert [c.source_job_id for c in page.cards] == ["2001", "2002", "2003"]
    assert all(c.experience is None for c in page.cards)


def test_parse_age() -> None:
    assert parse_age("Just now", NOW) == NOW
    assert parse_age("Few hours ago", NOW) == NOW
    assert parse_age("3 days ago", NOW) == NOW - timedelta(days=3)
    assert parse_age("1 week ago", NOW) == NOW - timedelta(days=7)
    assert parse_age("30+ days ago", NOW) == NOW - timedelta(days=30)
    assert parse_age("gibberish", NOW) is None and parse_age(None, NOW) is None


def test_parse_junk_html_does_not_raise() -> None:
    page = parse_listing_page("<div class='container-fluid individual_internship'><b>", "jobs")
    assert page.cards == []
    assert parse_listing_page("", "jobs").cards == []


@respx.mock
async def test_fetch_two_pages_maps_fields(no_sleep: list[float]) -> None:
    _serve_robots()
    p1 = respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p1.html"))
    )
    p2 = respx.get(f"{HOST}/jobs/python-jobs/page-2/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p2.html"))
    )
    respx.get(f"{HOST}/internships/python-internship/").mock(
        return_value=httpx.Response(200, text=_text("internshala_internships_p1.html"))
    )
    detail = respx.get(url__regex=r".*/(job|internship)/detail.*")
    a = _adapter(job_categories=["python-jobs"], internship_categories=["python-internship"])
    jobs = await _collect(a)
    assert p1.call_count == p2.call_count == 1 and detail.call_count == 0
    res = a.result()
    assert res.complete_scopes == {"jobs/python-jobs", "internships/python-internship"}
    assert res.failed_scopes == {}
    assert res.scope_counts == {"jobs/python-jobs": 4, "internships/python-internship": 3}
    assert res.calls_used == 4  # robots + 3 listing pages
    assert len(no_sleep) == 3 and all(0 < s <= 3.0 for s in no_sleep)
    assert [j.source_job_id for j in jobs] == [
        "1001",
        "1002",
        "1003",
        "1004",
        "2001",
        "2002",
        "2003",
    ]
    by = {j.source_job_id: j for j in jobs}
    j1 = by["1001"]
    assert j1.source_scope == "jobs/python-jobs" and j1.country_hint == "IN"
    assert j1.region_hint == "India" and j1.employment_type_hint == "full_time"
    assert j1.salary_text == "₹ 3,00,000 - 4,50,000 /year" and j1.salary_currency == "INR"
    assert j1.skills_hint == ["Python", "Django"] and j1.location_parts == ["Bangalore", "Pune"]
    assert j1.posted_at == NOW - timedelta(days=3) and not j1.description_is_html
    assert "ago" not in j1.description_raw and "Experience: 1-2 years" in j1.description_raw
    assert by["1002"].workplace_hint == "remote" and by["1002"].location_text is None
    assert by["1003"].employment_type_hint == "part_time" and by["1003"].salary_text is None
    assert by["1004"].posted_at == NOW - timedelta(days=30)
    assert by["1001"].category_hint == "software development"
    assert by["2001"].employment_type_hint == "internship"
    assert by["2001"].source_scope == "internships/python-internship"
    assert by["2002"].workplace_hint == "remote" and by["2002"].salary_text is None
    assert all(str(j.url).startswith(f"{HOST}/") for j in jobs)


@respx.mock
async def test_same_card_in_two_categories_first_wins() -> None:
    _serve_robots()
    respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p2.html").replace("75", "10"))
    )
    respx.get(f"{HOST}/jobs/java-jobs/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p2.html").replace("75", "10"))
    )
    a = _adapter(job_categories=["python-jobs", "java-jobs"], internship_categories=[])
    jobs = await _collect(a)
    assert [(j.source_job_id, j.source_scope) for j in jobs] == [
        ("1004", "jobs/python-jobs"),
        ("1001", "jobs/python-jobs"),
    ]
    assert a.result().complete_scopes == {"jobs/python-jobs", "jobs/java-jobs"}


@respx.mock
async def test_refusal_stops_everything_without_retry() -> None:
    _serve_robots()
    respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p1.html"))
    )
    p2 = respx.get(f"{HOST}/jobs/python-jobs/page-2/").mock(return_value=httpx.Response(403))
    java = respx.get(f"{HOST}/jobs/java-jobs/").mock(return_value=httpx.Response(200, text="x"))
    a = _adapter(
        job_categories=["python-jobs", "java-jobs"], internship_categories=["python-internship"]
    )
    jobs = await _collect(a)
    assert [j.source_job_id for j in jobs] == ["1001", "1002", "1003"]  # page 1 still yielded
    res = a.result()
    assert res.complete_scopes == set()
    assert res.failed_scopes == {
        "jobs/python-jobs": "FetchRefused:403",
        "jobs/java-jobs": "host_refused",
        "internships/python-internship": "host_refused",
    }
    assert p2.call_count == 1 and java.call_count == 0
    assert res.calls_used == 3  # robots, page 1, refused page 2


@respx.mock
async def test_robots_unavailable_fails_closed() -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(503))
    page = respx.get(f"{HOST}/jobs/python-jobs/").mock(return_value=httpx.Response(200, text="x"))
    a = _adapter(job_categories=["python-jobs"], internship_categories=[])
    assert await _collect(a) == []
    assert a.result().failed_scopes == {"jobs/python-jobs": "RobotsDisallowed"}
    assert page.call_count == 0


@respx.mock
async def test_page_cap_marks_scope_failed() -> None:
    _serve_robots()
    respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text=_text("internshala_jobs_p1.html"))
    )
    a = _adapter(job_categories=["python-jobs"], internship_categories=[], max_pages_per_category=1)
    assert len(await _collect(a)) == 3
    assert a.result().failed_scopes == {"jobs/python-jobs": "page_cap"}


@respx.mock
async def test_config_slug_outside_allow_list_is_not_fetched() -> None:
    _serve_robots()
    a = _adapter(job_categories=["python-jobs?x=1"], internship_categories=[])
    assert await _collect(a) == []
    assert a.result().failed_scopes == {"jobs/python-jobs?x=1": "path_not_allowed"}
