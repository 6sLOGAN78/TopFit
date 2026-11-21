import io
import json
from collections.abc import Callable

import httpx
import pytest

from ranker.observability import configure_logging
from ranker.spike import sources
from ranker.spike.sources import BoardSeed, SpikeReport, run_spike

KEY = "SECRET-KEY-VALUE-123"


def _default(calls: list[httpx.Request] | None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        host, path = request.url.host, request.url.path
        if host == "remotive.com":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": "Backend Engineer", "candidate_required_location": "Worldwide"},
                        {"title": "Data Scientist", "candidate_required_location": "India"},
                        {"title": "Sales Lead", "candidate_required_location": "Worldwide"},
                    ]
                },
            )
        if host == "api.adzuna.com":
            if path.endswith("/categories"):
                return httpx.Response(
                    200, json={"results": [{"tag": "it-jobs", "label": "IT Jobs"}]}
                )
            return httpx.Response(
                200,
                headers={"x-ratelimit-remaining": "99"},
                json={
                    "count": 1234,
                    "results": [
                        {
                            "salary_is_predicted": "1",
                            "salary_max": 150000,
                            "full_description": "x" * 900,
                            "description": "d" * 500,
                            "contract_time": "full_time",
                        },
                        {
                            "salary_is_predicted": "0",
                            "salary_max": 3000000,
                            "description": "d" * 300,
                        },
                    ],
                },
            )
        if host == "boards-api.greenhouse.io":
            if "/groww/" in path:
                return httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {"title": "Backend Engineer", "location": {"name": "Bengaluru"}},
                            {"title": "Backend Engineer", "location": {"name": "Luxembourg"}},
                        ]
                    },
                )
            return httpx.Response(404)
        if host == "api.lever.co":
            return httpx.Response(404)
        if host == "api.eu.lever.co":
            return httpx.Response(
                200, json=[{"text": "SRE", "categories": {"location": "Remote - India"}}]
            )
        return httpx.Response(500)

    return handler


def _client(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    calls: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler or _default(calls)))


async def test_no_credentials_skips_adzuna_but_runs_rest() -> None:
    async with _client() as c:
        rep = await run_spike(
            c,
            adzuna_app_id=None,
            adzuna_app_key=None,
            seeds=[BoardSeed(ats="greenhouse", slug="groww")],
            delay_s=0,
        )
    assert rep.adzuna.skipped == "missing_credentials"
    assert rep.remotive.total == 3
    assert rep.boards[0].status == "200"


async def test_remotive_counts_and_histogram() -> None:
    async with _client() as c:
        rep = await run_spike(c, adzuna_app_id=None, adzuna_app_key=None, seeds=[], delay_s=0)
    assert rep.remotive.total == 3
    assert rep.remotive.in_scope_estimate == 2
    assert rep.remotive.candidate_required_location == {"Worldwide": 2, "India": 1}


async def test_adzuna_full_probe_set_and_calls_used() -> None:
    calls: list[httpx.Request] = []
    async with _client(calls=calls) as c:
        rep = await run_spike(c, adzuna_app_id="id", adzuna_app_key=KEY, seeds=[], delay_s=0)
    az = rep.adzuna
    adzuna_calls = [r for r in calls if r.url.host == "api.adzuna.com"]
    assert az.calls_used == len(adzuna_calls) == 9
    assert az.it_category_tag == "it-jobs"
    assert set(az.category_counts_by_days) == {"1", "7", "30"}
    assert az.category_counts_by_days["30"].count == 1234
    assert az.what_or_count is not None and az.what_or_count.count == 1234
    assert az.bangalore is not None and az.bengaluru is not None
    assert az.results_per_page_100 is not None and az.results_per_page_100.returned == 2
    assert az.deep_page_40 is not None and az.deep_page_40.status == 200
    assert az.rate_limit_headers == {"x-ratelimit-remaining": "99"}
    assert az.sample is not None
    assert az.sample.share_full_description == pytest.approx(0.5)  # pyright: ignore[reportUnknownMemberType]
    assert az.sample.share_salary_is_predicted == pytest.approx(0.5)  # pyright: ignore[reportUnknownMemberType]
    assert az.sample.share_contract_time == pytest.approx(0.5)  # pyright: ignore[reportUnknownMemberType]
    assert az.sample.share_salary_max_under_200000 == pytest.approx(0.5)  # pyright: ignore[reportUnknownMemberType]
    assert az.sample.median_description_length == pytest.approx(400.0)  # pyright: ignore[reportUnknownMemberType]


async def test_boards_status_totals_and_lever_eu_fallback() -> None:
    seeds = [
        BoardSeed(ats="greenhouse", slug="groww"),
        BoardSeed(ats="greenhouse", slug="nope"),
        BoardSeed(ats="lever", slug="cred"),
    ]
    async with _client() as c:
        rep = await run_spike(c, adzuna_app_id=None, adzuna_app_key=None, seeds=seeds, delay_s=0)
    gh, missing, lever = rep.boards
    assert (gh.status, gh.total, gh.in_scope_estimate) == ("200", 2, 1)
    assert missing.status == "404"
    assert (lever.status, lever.host, lever.in_scope_estimate) == ("200", "eu", 1)


async def test_bad_slug_rejected_without_request() -> None:
    calls: list[httpx.Request] = []
    seeds = [
        BoardSeed(ats="greenhouse", slug="../etc/passwd"),
        BoardSeed(ats="lever", slug="A"),
    ]
    async with _client(calls=calls) as c:
        rep = await run_spike(c, adzuna_app_id=None, adzuna_app_key=None, seeds=seeds, delay_s=0)
    assert [b.status for b in rep.boards] == ["rejected_slug", "rejected_slug"]
    assert [r.url.host for r in calls] == ["remotive.com"]


async def test_app_key_never_in_report_or_logs_after_adzuna_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.adzuna.com":
            return httpx.Response(500)
        return httpx.Response(200, json={"jobs": []})

    buf = io.StringIO()
    configure_logging(level="DEBUG", stream=buf)
    async with _client(handler) as c:
        rep = await run_spike(c, adzuna_app_id="myid", adzuna_app_key=KEY, seeds=[], delay_s=0)
    assert KEY not in rep.model_dump_json()
    assert KEY not in buf.getvalue()
    assert rep.adzuna.category_counts_by_days["1"].error == "HTTPStatusError:500"


async def test_transport_error_recorded_as_type_name_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    async with _client(handler) as c:
        rep = await run_spike(c, adzuna_app_id="i", adzuna_app_key=KEY, seeds=[], delay_s=0)
    assert rep.remotive.error == "ConnectError:None"
    assert KEY not in rep.model_dump_json()


def test_seed_list_size_and_valid_slugs() -> None:
    assert len(sources.SEED_BOARDS) >= 25
    assert all(sources.SLUG_RE.fullmatch(s.slug) for s in sources.SEED_BOARDS)


def test_report_roundtrips_as_json() -> None:
    assert json.loads(SpikeReport().model_dump_json())["adzuna"]["calls_used"] == 0
