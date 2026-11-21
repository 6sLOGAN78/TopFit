"""ING-00 spike extension (D-14..D-16): free remote APIs, HN, Internshala, blocked-site probes.

Politeness rules (D-15): honest User-Agent, robots.txt checked at runtime for every HTML site,
at most one request per ``min_interval_s`` (3 s) per HTML host, no login, no retries, no
anti-bot evasion, no proxies. Internshala detail pages are never fetched (robots-disallowed).
"""

import asyncio
import math
import re
import time
from collections.abc import Callable
from typing import Any, Literal
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from ranker.spike.common import (
    BOT_UA,
    INDIA_RE,
    as_dict,
    as_list,
    exc_tag,
    in_scope,
)

ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
HIMALAYAS_URL = "https://himalayas.app/jobs/api"
JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK_URL = "https://remoteok.com/api"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
INTERNSHALA = "https://internshala.com"
ARBEITNOW_MAX_PAGES = 5

INTERNSHALA_PATHS: list[str] = [
    "/jobs/python-jobs/",
    "/jobs/software-development-jobs/",
    "/jobs/web-development-jobs/",
    "/jobs/data-science-jobs/",
    "/jobs/java-jobs/",
    "/internships/python-internship/",
    "/internships/web-development-internship/",
    "/internships/software-development-internship/",
    "/internships/data-science-internship/",
    "/internships/machine-learning-internship/",
]

PROBE_TARGETS: list[tuple[str, str]] = [
    ("naukri", "https://www.naukri.com/python-jobs"),
    ("indeed", "https://in.indeed.com/jobs?q=python+developer"),
    (
        "linkedin_guest",
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPosts/search"
        "?keywords=python&location=India",
    ),
    ("wellfound", "https://wellfound.com/jobs"),
]

CHALLENGE_RE = re.compile(r"captcha|challenge|access denied|are you a robot|unusual traffic", re.I)
INDIA_ONLY_RE = re.compile(
    r"india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai"
    r"|kolkata|worldwide|anywhere|apac|asia",
    re.I,
)
HN_TITLE_RE = re.compile(r"who is hiring", re.I)


class FreeApiResult(BaseModel):
    name: str
    status: str = "not_run"
    total: int = 0
    sampled: int = 0
    in_scope_sample: int = 0
    india_eligible_sample: int = 0
    in_scope_india_eligible_sample: int = 0
    pages_fetched: int = 0
    terms_note: str = ""
    error: str | None = None


class HnSection(BaseModel):
    status: str = "not_run"
    title: str | None = None
    story_id: int | None = None
    num_comments: int | None = None
    top_level_comments: int = 0
    mentions_india_or_remote: int = 0
    error: str | None = None


class CardFields(BaseModel):
    title: int = 0
    company: int = 0
    location: int = 0
    salary: int = 0
    experience: int = 0
    skills: int = 0
    posted_age: int = 0
    detail_link: int = 0


class CategoryResult(BaseModel):
    path: str
    status: str = "not_run"
    cards: int = 0
    total_listed: int | None = None
    pages_available: int | None = None
    fields_present: CardFields = Field(default_factory=CardFields)


class InternshalaSection(BaseModel):
    robots: str = "not_run"
    categories: list[CategoryResult] = Field(default_factory=list[CategoryResult])
    requests_made: int = 0


class SiteProbe(BaseModel):
    site: str
    url_path: str
    robots: str = "not_run"
    status: int | None = None
    verdict: str = "not_run"
    error: str | None = None


class ExtraReport(BaseModel):
    free_apis: list[FreeApiResult] = Field(default_factory=list[FreeApiResult])
    hn: HnSection = Field(default_factory=HnSection)
    internshala: InternshalaSection = Field(default_factory=InternshalaSection)
    probes: list[SiteProbe] = Field(default_factory=list[SiteProbe])


RobotsVerdict = Literal["allowed", "disallowed", "unavailable"]


class Politeness:
    """Enforces a minimum interval between requests to the same host."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | RobotsVerdict] = {}

    async def wait(self, host: str) -> None:
        if self.min_interval_s > 0 and host in self._last:
            gap = time.monotonic() - self._last[host]
            if gap < self.min_interval_s:
                await asyncio.sleep(self.min_interval_s - gap)
        self._last[host] = time.monotonic()


async def check_robots(
    client: httpx.AsyncClient, polite: Politeness, origin: str, url: str
) -> RobotsVerdict:
    """Fetch robots.txt once per origin at runtime and decide whether BOT_UA may fetch ``url``."""
    cached = polite.robots.get(origin)
    if cached is None:
        await polite.wait(httpx.URL(origin).host)
        cached = "unavailable"
        try:
            resp = await client.get(f"{origin}/robots.txt", headers={"User-Agent": BOT_UA})
            if resp.status_code in (401, 403):
                cached = "disallowed"
            elif resp.status_code == 404:
                cached = "allowed"
            elif resp.status_code < 400:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                cached = parser
        except Exception:
            cached = "unavailable"
        polite.robots[origin] = cached
    if isinstance(cached, str):
        return cached
    return "allowed" if cached.can_fetch(BOT_UA, url) else "disallowed"


# --- free remote-job APIs ---------------------------------------------------------------


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, str | int]) -> Any:
    resp = await client.get(url, params=params, headers={"User-Agent": BOT_UA})
    if resp.status_code != 200:
        raise httpx.HTTPStatusError("status", request=resp.request, response=resp)
    return resp.json()


def _tally(
    res: FreeApiResult,
    jobs: list[dict[str, Any]],
    title_of: Callable[[dict[str, Any]], str],
    eligible_of: Callable[[dict[str, Any]], bool],
) -> None:
    for j in jobs:
        res.sampled += 1
        scope = in_scope(title_of(j))
        elig = eligible_of(j)
        res.in_scope_sample += scope
        res.india_eligible_sample += elig
        res.in_scope_india_eligible_sample += scope and elig


def _text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return str(value or "")


async def _arbeitnow(client: httpx.AsyncClient, delay_s: float) -> FreeApiResult:
    res = FreeApiResult(
        name="arbeitnow",
        terms_note="Free API, no key; attribute Arbeitnow and link back to the listing url.",
    )
    page = 1
    try:
        while page <= ARBEITNOW_MAX_PAGES:
            body = as_dict(await _get_json(client, ARBEITNOW_URL, {"page": page}))
            jobs = as_list(body.get("data"))
            res.pages_fetched += 1
            res.total += len(jobs)
            _tally(
                res,
                jobs,
                lambda j: str(j.get("title", "")),
                lambda j: (
                    bool(j.get("remote")) or bool(INDIA_ONLY_RE.search(_text(j.get("location"))))
                ),
            )
            if not as_dict(body.get("links")).get("next") or not jobs:
                break
            page += 1
            if delay_s > 0:
                await asyncio.sleep(delay_s)
        res.status = "200"
    except Exception as exc:
        res.status, res.error = "error", exc_tag(exc)
    return res


async def _himalayas(client: httpx.AsyncClient) -> FreeApiResult:
    res = FreeApiResult(
        name="himalayas",
        terms_note="Free API (max 20 per request); must link back to Himalayas and credit it.",
    )
    try:
        body = as_dict(await _get_json(client, HIMALAYAS_URL, {"limit": 20, "offset": 0}))
        jobs = as_list(body.get("jobs"))
        total = body.get("totalCount")
        res.total = total if isinstance(total, int) else len(jobs)
        res.pages_fetched = 1

        def eligible(j: dict[str, Any]) -> bool:
            restrictions = j.get("locationRestrictions")
            if not restrictions:
                return True
            return bool(INDIA_ONLY_RE.search(_text(restrictions)))

        _tally(res, jobs, lambda j: str(j.get("title", "")), eligible)
        res.status = "200"
    except Exception as exc:
        res.status, res.error = "error", exc_tag(exc)
    return res


async def _jobicy(client: httpx.AsyncClient, delay_s: float) -> FreeApiResult:
    res = FreeApiResult(
        name="jobicy",
        terms_note="Free API; link back to Jobicy and credit it as the source.",
    )
    try:
        body = as_dict(await _get_json(client, JOBICY_URL, {"count": 100}))
        jobs = as_list(body.get("jobs"))
        res.total = len(jobs)
        res.pages_fetched = 1
        _tally(
            res,
            jobs,
            lambda j: str(j.get("jobTitle", "")),
            lambda j: bool(INDIA_ONLY_RE.search(_text(j.get("jobGeo")))),
        )
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        geo = as_dict(await _get_json(client, JOBICY_URL, {"count": 100, "geo": "india"}))
        res.terms_note += f" geo=india filter returned {len(as_list(geo.get('jobs')))} jobs."
        res.pages_fetched += 1
        res.status = "200"
    except Exception as exc:
        res.status, res.error = "error", exc_tag(exc)
    return res


async def _remoteok(client: httpx.AsyncClient) -> FreeApiResult:
    res = FreeApiResult(
        name="remoteok",
        terms_note="Free API; must link back to the RemoteOK URL and credit RemoteOK as source.",
    )
    try:
        data = as_list(await _get_json(client, REMOTEOK_URL, {}))
        jobs = [j for j in data if "position" in j]
        res.total = len(jobs)
        res.pages_fetched = 1
        _tally(
            res,
            jobs,
            lambda j: str(j.get("position", "")),
            lambda j: not j.get("location") or bool(INDIA_ONLY_RE.search(_text(j.get("location")))),
        )
        res.status = "200"
    except Exception as exc:
        res.status, res.error = "error", exc_tag(exc)
    return res


# --- HN Who is hiring --------------------------------------------------------------------


async def _measure_hn(client: httpx.AsyncClient) -> HnSection:
    sec = HnSection()
    try:
        body = as_dict(
            await _get_json(
                client,
                HN_SEARCH_URL,
                {"tags": "story,author_whoishiring", "hitsPerPage": 10},
            )
        )
        hits = [
            h
            for h in as_list(body.get("hits"))
            if HN_TITLE_RE.search(str(h.get("title", "")))
            and "wants to be" not in str(h.get("title"))
        ]
        if not hits:
            sec.status = "no_thread_found"
            return sec
        hit = hits[0]
        sec.title = str(hit.get("title"))
        sid = hit.get("story_id") or hit.get("objectID")
        sec.story_id = int(sid) if sid else None
        nc = hit.get("num_comments")
        sec.num_comments = nc if isinstance(nc, int) else None
        if sec.story_id is not None:
            item = as_dict(await _get_json(client, HN_ITEM_URL.format(id=sec.story_id), {}))
            kids = as_list(item.get("children"))
            sec.top_level_comments = len(kids)
            sec.mentions_india_or_remote = sum(
                1 for k in kids if INDIA_RE.search(str(k.get("text") or ""))
            )
        sec.status = "200"
    except Exception as exc:
        sec.status, sec.error = "error", exc_tag(exc)
    return sec


# --- Internshala listings ----------------------------------------------------------------

CARD_SPLIT = "container-fluid individual_internship"
TOTAL_RE = re.compile(r"Find ([\d,]+) [A-Za-z ]*?(?:Vacancies|Internships?|Jobs)", re.I)
PAGE_RE = re.compile(r"/page-(\d+)")
AGE_RE = re.compile(r"<span>[^<]*(?:ago|Just now|Today|Few hours)[^<]*</span>", re.I)


def parse_internshala_page(html: str) -> CategoryResult:
    """Count cards and card fields on a listing page (no detail pages are involved)."""
    res = CategoryResult(path="", status="200")
    chunks = html.split(CARD_SPLIT)[1:]
    res.cards = len(chunks)
    f = res.fields_present
    for c in chunks:
        f.title += "job-title-href" in c
        f.company += "company-name" in c
        f.location += "locations" in c
        f.salary += "ic-16-money" in c
        f.experience += "ic-16-briefcase" in c
        f.skills += "job_skill" in c
        f.posted_age += bool(AGE_RE.search(c))
        f.detail_link += "data-href=" in c
    m = TOTAL_RE.search(html)
    if m:
        res.total_listed = int(m.group(1).replace(",", ""))
        res.pages_available = max(1, math.ceil(res.total_listed / 50))
    pages = [int(p) for p in PAGE_RE.findall(html)]
    if pages and res.pages_available is None:
        res.pages_available = max(pages)
    return res


async def _measure_internshala(
    client: httpx.AsyncClient, polite: Politeness, paths: list[str]
) -> InternshalaSection:
    sec = InternshalaSection()
    sec.robots = "checked"
    host = "internshala.com"
    for path in paths:
        cat = CategoryResult(path=path)
        sec.categories.append(cat)
        if "?" in path or "," in path or "/detail" in path:
            cat.status = "skipped_not_allowed_shape"
            continue
        verdict = await check_robots(client, polite, INTERNSHALA, f"{INTERNSHALA}{path}")
        if verdict != "allowed":
            cat.status = f"robots_{verdict}"
            continue
        await polite.wait(host)
        try:
            resp = await client.get(f"{INTERNSHALA}{path}", headers={"User-Agent": BOT_UA})
        except Exception as exc:
            cat.status = exc_tag(exc)
            continue
        sec.requests_made += 1
        if resp.status_code != 200:
            cat.status = str(resp.status_code)
            continue
        parsed = parse_internshala_page(resp.text)
        parsed.path = path
        sec.categories[-1] = parsed
    if sec.categories and all(c.status.startswith("robots_") for c in sec.categories):
        sec.robots = "disallowed"
    return sec


# --- blocked-site probes -----------------------------------------------------------------


async def _probe_site(
    client: httpx.AsyncClient, polite: Politeness, site: str, url: str
) -> SiteProbe:
    u = httpx.URL(url)
    origin = f"{u.scheme}://{u.host}"
    path = u.path + (f"?{u.query.decode()}" if u.query else "")
    probe = SiteProbe(site=site, url_path=path)
    verdict = await check_robots(client, polite, origin, url)
    probe.robots = verdict
    if verdict != "allowed":
        probe.verdict = "blocked, not used"
        return probe
    await polite.wait(u.host)
    try:
        resp = await client.get(url, headers={"User-Agent": BOT_UA})
    except Exception as exc:
        probe.error = exc_tag(exc)
        probe.verdict = "blocked, not used"
        return probe
    probe.status = resp.status_code
    if resp.status_code != 200 or CHALLENGE_RE.search(resp.text[:20000]):
        probe.verdict = "blocked, not used"
    else:
        probe.verdict = "reachable, no adapter planned (robots allow, HTTP 200)"
    return probe


async def run_extra(
    client: httpx.AsyncClient,
    *,
    delay_s: float,
    min_interval_s: float,
    internshala_paths: list[str] | None = None,
) -> ExtraReport:
    report = ExtraReport()
    report.free_apis = [
        await _arbeitnow(client, delay_s),
        await _himalayas(client),
        await _jobicy(client, delay_s),
        await _remoteok(client),
    ]
    report.hn = await _measure_hn(client)
    polite = Politeness(min_interval_s)
    report.internshala = await _measure_internshala(
        client, polite, INTERNSHALA_PATHS if internshala_paths is None else internshala_paths
    )
    for site, url in PROBE_TARGETS:
        report.probes.append(await _probe_site(client, polite, site, url))
    return report
