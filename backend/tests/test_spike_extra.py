from collections.abc import Callable

import httpx

from ranker.spike.common import BOT_UA
from ranker.spike.extra import (
    Politeness,
    parse_internshala_page,
    run_extra,
)
from ranker.spike.sources import BoardSeed, run_spike

CARD = """<div class="container-fluid individual_internship x" id="i1"
 data-href='/job/detail/foo'><a class="job-title-href">T</a><p class="company-name">C</p>
 <p class="row-1-item  locations"><a>Pune</a></p><i class="ic-16-money"></i>
 <i class="ic-16-briefcase"></i><div class='job_skill'>Python</div>
 <div class="status-inactive"><span>2 weeks ago</span></div></div>"""
LISTING = f"<title>Python Jobs - Find 159 Vacancies in Nov 2026</title>{CARD}{CARD}"
ROBOTS = "User-agent: *\nDisallow: /*?*\nDisallow: /job/details/\nDisallow: /login\n"


def _handler(calls: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        host, path = req.url.host, req.url.path
        if host == "www.arbeitnow.com":
            page = int(req.url.params.get("page", "1"))
            nxt = {"next": "x"} if page == 1 else {}
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"title": "Python Developer", "location": "Berlin", "remote": True},
                        {"title": "Cook", "location": "Berlin", "remote": False},
                    ],
                    "links": nxt,
                },
            )
        if host == "himalayas.app":
            return httpx.Response(
                200,
                json={
                    "totalCount": 500,
                    "jobs": [
                        {"title": "Backend Engineer", "locationRestrictions": []},
                        {"title": "Data Engineer", "locationRestrictions": ["Germany"]},
                    ],
                },
            )
        if host == "jobicy.com":
            return httpx.Response(
                200, json={"jobs": [{"jobTitle": "DevOps Engineer", "jobGeo": "Anywhere"}]}
            )
        if host == "remoteok.com" and path == "/api":
            return httpx.Response(
                200, json=[{"legal": "x"}, {"position": "SRE", "location": "Worldwide"}]
            )
        if host == "hn.algolia.com" and "search_by_date" in path:
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {"title": "Ask HN: Who wants to be hired?", "objectID": "1"},
                        {
                            "title": "Ask HN: Who is hiring? (November 2026)",
                            "objectID": "42",
                            "num_comments": 3,
                        },
                    ]
                },
            )
        if host == "hn.algolia.com":
            return httpx.Response(
                200, json={"children": [{"text": "Acme | Remote | Dev"}, {"text": "Foo | NYC"}]}
            )
        if path == "/robots.txt":
            if host == "in.indeed.com":
                return httpx.Response(200, text="User-agent: *\nDisallow: /jobs\n")
            if host == "www.naukri.com":
                return httpx.Response(403)
            return httpx.Response(200, text=ROBOTS)
        if host == "internshala.com":
            return httpx.Response(200, text=LISTING)
        if host == "www.linkedin.com":
            return httpx.Response(200, text="<html>Please solve this captcha</html>")
        if host == "wellfound.com":
            return httpx.Response(403)
        return httpx.Response(500)

    return h


async def test_run_extra_measures_everything_politely() -> None:
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as c:
        rep = await run_extra(
            c, delay_s=0, min_interval_s=0, internshala_paths=["/jobs/python-jobs/", "/jobs/x?q=1"]
        )
    by = {a.name: a for a in rep.free_apis}
    assert by["arbeitnow"].total == 4 and by["arbeitnow"].pages_fetched == 2
    assert by["arbeitnow"].in_scope_india_eligible_sample == 2
    assert by["himalayas"].total == 500 and by["himalayas"].india_eligible_sample == 1
    assert by["jobicy"].total == 1 and by["remoteok"].total == 1
    assert rep.hn.title is not None and "Who is hiring" in rep.hn.title
    assert rep.hn.top_level_comments == 2 and rep.hn.mentions_india_or_remote == 1
    ish = rep.internshala.categories
    assert ish[0].cards == 2 and ish[0].total_listed == 159 and ish[0].pages_available == 4
    assert ish[0].fields_present.posted_age == 2 and ish[0].fields_present.salary == 2
    assert ish[1].status == "skipped_not_allowed_shape"
    probes = {p.site: p for p in rep.probes}
    assert probes["naukri"].verdict == "blocked, not used" and probes["naukri"].status is None
    assert probes["indeed"].robots == "disallowed" and probes["indeed"].status is None
    assert probes["linkedin_guest"].verdict == "blocked, not used"
    assert probes["wellfound"].status == 403
    # no detail pages, no query-string fetches against internshala, honest UA everywhere
    assert not any(r.url.host == "internshala.com" and "detail" in r.url.path for r in calls)
    assert not any(r.url.host == "internshala.com" and r.url.query for r in calls)
    assert all(r.headers["user-agent"] == BOT_UA for r in calls)
    robots_hits = [
        r for r in calls if r.url.host == "internshala.com" and r.url.path == "/robots.txt"
    ]
    assert len(robots_hits) == 1


def test_parse_internshala_page_fields() -> None:
    res = parse_internshala_page(LISTING)
    assert (res.cards, res.fields_present.skills, res.fields_present.detail_link) == (2, 2, 2)


async def test_politeness_spaces_requests_per_host() -> None:
    p = Politeness(0.05)
    import time

    t0 = time.monotonic()
    await p.wait("a")
    await p.wait("a")
    await p.wait("b")
    assert 0.045 <= time.monotonic() - t0 < 0.5


async def test_ashby_seed_counts_in_scope() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.ashbyhq.com":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": "Backend Engineer", "location": "Bengaluru", "isRemote": False},
                        {"title": "Backend Engineer", "location": "Paris", "isRemote": False},
                    ]
                },
            )
        return httpx.Response(200, json={"jobs": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
        rep = await run_spike(
            c,
            adzuna_app_id=None,
            adzuna_app_key=None,
            seeds=[BoardSeed(ats="ashby", slug="ramp")],
            delay_s=0,
        )
    assert (rep.boards[0].status, rep.boards[0].total, rep.boards[0].in_scope_estimate) == (
        "200",
        2,
        1,
    )
    assert rep.extra is None
