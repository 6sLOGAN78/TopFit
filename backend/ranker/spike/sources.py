"""ING-00 source-viability spike: measures Remotive, Adzuna and Greenhouse/Lever boards.

Run: ``uv run python -m ranker.spike.sources --out ../docs/decisions/0001-sources-spike.json``

Credentials are passed only through httpx ``params=`` and are never logged or written to the
report. Errors are recorded as ``<ExceptionType>:<status>`` because exception strings carry URLs.
The in-scope heuristic here is a spike estimate only; production scope rules live in plan 02-04.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, Field

from ranker.observability import configure_logging

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/in"
GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
LEVER_EU_URL = "https://api.eu.lever.co/v0/postings/{slug}?mode=json"
USER_AGENT = "TopFit-jobs-indexer/1.0"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DEFAULT_OUT = "../docs/decisions/0001-sources-spike.json"

TECH_RE = re.compile(
    r"engineer|developer|sde|devops|sre|data|\bml\b|machine learning|\bqa\b|sdet|security"
    r"|android|ios|backend|frontend|full[ -]?stack",
    re.I,
)
NON_TECH_RE = re.compile(r"product manager|designer|sales|recruiter|support|marketing", re.I)
INDIA_RE = re.compile(
    r"india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai"
    r"|kolkata|remote|worldwide|anywhere|apac",
    re.I,
)


class BoardSeed(BaseModel):
    ats: Literal["greenhouse", "lever"]
    slug: str


def _seeds(ats: Literal["greenhouse", "lever"], slugs: str) -> list[BoardSeed]:
    return [BoardSeed(ats=ats, slug=s) for s in slugs.split()]


SEED_BOARDS: list[BoardSeed] = [
    *_seeds("lever", "cred meesho paytm zeta slice jupiter upgrad inmobi"),
    *_seeds(
        "greenhouse",
        "groww zerodha swiggy phonepe sharechat dream11 razorpay freshworks browserstack"
        " postman unacademy rapido zepto urbancompany",
    ),
    *_seeds("greenhouse", "gitlab automattic canonical toptal deel remotecom hashicorp"),
]


class RemotiveSection(BaseModel):
    total: int = 0
    in_scope_estimate: int = 0
    candidate_required_location: dict[str, int] = Field(default_factory=dict[str, int])
    error: str | None = None


class AdzunaSample(BaseModel):
    n: int = 0
    share_salary_is_predicted: float = 0.0
    share_full_description: float = 0.0
    median_description_length: float = 0.0
    share_contract_time: float = 0.0
    share_salary_max_under_200000: float = 0.0


class AdzunaProbe(BaseModel):
    count: int | None = None
    returned: int | None = None
    status: int | None = None
    error: str | None = None


class AdzunaSection(BaseModel):
    skipped: str | None = None
    it_category_tag: str | None = None
    categories: list[str] = Field(default_factory=list[str])
    category_counts_by_days: dict[str, AdzunaProbe] = Field(default_factory=dict[str, AdzunaProbe])
    what_or_count: AdzunaProbe | None = None
    bangalore: AdzunaProbe | None = None
    bengaluru: AdzunaProbe | None = None
    results_per_page_100: AdzunaProbe | None = None
    deep_page_40: AdzunaProbe | None = None
    rate_limit_headers: dict[str, str] = Field(default_factory=dict[str, str])
    sample: AdzunaSample | None = None
    calls_used: int = 0


class BoardResult(BaseModel):
    ats: str
    slug: str
    status: str
    host: str = "global"
    total: int = 0
    in_scope_estimate: int = 0


class SpikeReport(BaseModel):
    measured_at: str = ""
    remotive: RemotiveSection = Field(default_factory=RemotiveSection)
    adzuna: AdzunaSection = Field(default_factory=AdzunaSection)
    boards: list[BoardResult] = Field(default_factory=list[BoardResult])


def _err(exc: BaseException) -> str:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return f"{type(exc).__name__}:{status}"


def _quiet_http_loggers() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _in_scope(title: str) -> bool:
    return bool(TECH_RE.search(title)) and not NON_TECH_RE.search(title)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast("dict[str, Any]", x) for x in cast("list[Any]", value) if isinstance(x, dict)]


def _as_dict(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


# --- Remotive ---------------------------------------------------------------------------


async def _measure_remotive(client: httpx.AsyncClient) -> RemotiveSection:
    section = RemotiveSection()
    try:
        resp = await client.get(REMOTIVE_URL)
        if resp.status_code != 200:
            section.error = f"HTTPStatusError:{resp.status_code}"
            return section
        jobs = _as_list(_as_dict(resp.json()).get("jobs"))
    except Exception as exc:
        section.error = _err(exc)
        return section
    section.total = len(jobs)
    section.in_scope_estimate = sum(1 for j in jobs if _in_scope(str(j.get("title", ""))))
    hist = Counter(str(j.get("candidate_required_location", "")) or "(empty)" for j in jobs)
    section.candidate_required_location = dict(hist)
    return section


# --- Adzuna -----------------------------------------------------------------------------


class _Adzuna:
    def __init__(self, client: httpx.AsyncClient, app_id: str, app_key: str) -> None:
        self.client = client
        self.creds: dict[str, str | int] = {"app_id": app_id, "app_key": app_key}
        self.calls = 0
        self.headers: dict[str, str] = {}
        self.sample_jobs: list[dict[str, Any]] = []

    async def search(self, path: str, params: dict[str, str | int]) -> AdzunaProbe:
        self.calls += 1
        probe = AdzunaProbe()
        try:
            resp = await self.client.get(
                f"{ADZUNA_BASE}/{path}",
                params={**self.creds, "content-type": "application/json", **params},
            )
            probe.status = resp.status_code
            for k, v in resp.headers.items():
                low = k.lower()
                if "rate" in low or "limit" in low or low == "retry-after":
                    self.headers[low] = v
            if resp.status_code != 200:
                probe.error = f"HTTPStatusError:{resp.status_code}"
                return probe
            body = _as_dict(resp.json())
            results = _as_list(body.get("results"))
            count = body.get("count")
            probe.count = count if isinstance(count, int) else None
            probe.returned = len(results)
            if results and path == "search/1":
                self.sample_jobs.extend(results)
        except Exception as exc:
            probe.error = _err(exc)
        return probe

    async def categories(self) -> tuple[str | None, list[str]]:
        self.calls += 1
        try:
            resp = await self.client.get(f"{ADZUNA_BASE}/categories", params=self.creds)
            if resp.status_code != 200:
                return None, []
            results = _as_list(_as_dict(resp.json()).get("results"))
        except Exception:
            return None, []
        tags = [str(r.get("tag", "")) for r in results]
        for r in results:
            if re.search(r"\bIT\b", str(r.get("label", ""))):
                return str(r.get("tag")), tags
        return None, tags


def _sample_stats(jobs: list[dict[str, Any]]) -> AdzunaSample:
    n = len(jobs)
    if n == 0:
        return AdzunaSample()

    def share(pred: Callable[[dict[str, Any]], bool]) -> float:
        return round(sum(1 for j in jobs if pred(j)) / n, 4)

    def under_200k(j: dict[str, Any]) -> bool:
        v = j.get("salary_max")
        return isinstance(v, int | float) and v < 200000

    lengths = [len(str(j.get("description", ""))) for j in jobs]
    return AdzunaSample(
        n=n,
        share_salary_is_predicted=share(
            lambda j: str(j.get("salary_is_predicted")) in ("1", "True")
        ),
        share_full_description=share(lambda j: bool(j.get("full_description"))),
        median_description_length=float(statistics.median(lengths)),
        share_contract_time=share(lambda j: bool(j.get("contract_time"))),
        share_salary_max_under_200000=share(under_200k),
    )


async def _measure_adzuna(client: httpx.AsyncClient, app_id: str, app_key: str) -> AdzunaSection:
    az = _Adzuna(client, app_id, app_key)
    section = AdzunaSection()
    found, tags = await az.categories()
    section.categories = tags
    tag = found or "it-jobs"
    section.it_category_tag = tag
    base: dict[str, str | int] = {"results_per_page": 50, "sort_by": "date"}
    for days in (1, 7, 30):
        section.category_counts_by_days[str(days)] = await az.search(
            "search/1", {**base, "category": tag, "max_days_old": days}
        )
    section.what_or_count = await az.search(
        "search/1",
        {**base, "what_or": "developer engineer software data devops", "max_days_old": 1},
    )
    section.bangalore = await az.search("search/1", {**base, "category": tag, "where": "Bangalore"})
    section.bengaluru = await az.search("search/1", {**base, "category": tag, "where": "Bengaluru"})
    section.results_per_page_100 = await az.search(
        "search/1", {"results_per_page": 100, "sort_by": "date", "category": tag}
    )
    section.deep_page_40 = await az.search(
        "search/40", {"results_per_page": 50, "sort_by": "date", "category": tag}
    )
    section.rate_limit_headers = az.headers
    section.sample = _sample_stats(az.sample_jobs)
    section.calls_used = az.calls
    return section


# --- Greenhouse / Lever -----------------------------------------------------------------


def _board_scope(ats: str, jobs: list[dict[str, Any]]) -> int:
    n = 0
    for j in jobs:
        if ats == "greenhouse":
            title = str(j.get("title", ""))
            loc = str(_as_dict(j.get("location")).get("name", ""))
        else:
            title = str(j.get("text", ""))
            loc = str(_as_dict(j.get("categories")).get("location", ""))
        if _in_scope(title) and INDIA_RE.search(loc):
            n += 1
    return n


async def _fetch_board(client: httpx.AsyncClient, url: str) -> tuple[int | None, Any, str | None]:
    try:
        resp = await client.get(url)
    except Exception as exc:
        return None, None, _err(exc)
    if resp.status_code != 200:
        return resp.status_code, None, None
    try:
        return 200, resp.json(), None
    except ValueError:
        return 200, None, "JSONDecodeError:200"


async def _measure_board(client: httpx.AsyncClient, seed: BoardSeed) -> BoardResult:
    result = BoardResult(ats=seed.ats, slug=seed.slug, status="rejected_slug")
    if not SLUG_RE.fullmatch(seed.slug):
        return result
    if seed.ats == "greenhouse":
        status, body, err = await _fetch_board(client, GREENHOUSE_URL.format(slug=seed.slug))
        jobs = _as_list(_as_dict(body).get("jobs"))
    else:
        status, body, err = await _fetch_board(client, LEVER_URL.format(slug=seed.slug))
        if status == 404:
            status, body, err = await _fetch_board(client, LEVER_EU_URL.format(slug=seed.slug))
            result.host = "eu"
        jobs = _as_list(body)
    result.status = err or str(status)
    if status == 200 and err is None:
        result.total = len(jobs)
        result.in_scope_estimate = _board_scope(seed.ats, jobs)
    return result


async def run_spike(
    client: httpx.AsyncClient,
    *,
    adzuna_app_id: str | None,
    adzuna_app_key: str | None,
    seeds: list[BoardSeed],
    delay_s: float,
) -> SpikeReport:
    _quiet_http_loggers()
    report = SpikeReport(measured_at=datetime.now(UTC).isoformat(timespec="seconds"))
    report.remotive = await _measure_remotive(client)
    if adzuna_app_id and adzuna_app_key:
        report.adzuna = await _measure_adzuna(client, adzuna_app_id, adzuna_app_key)
    else:
        report.adzuna = AdzunaSection(skipped="missing_credentials")
    for seed in seeds:
        report.boards.append(await _measure_board(client, seed))
        if delay_s > 0:
            await asyncio.sleep(delay_s)
    return report


# --- CLI --------------------------------------------------------------------------------


async def _amain() -> SpikeReport:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
        report = await run_spike(
            client,
            adzuna_app_id=os.environ.get("ADZUNA_APP_ID") or None,
            adzuna_app_key=os.environ.get("ADZUNA_APP_KEY") or None,
            seeds=SEED_BOARDS,
            delay_s=1.0,
        )
    return report


def _emit(report: SpikeReport, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2) + "\n")
    print(
        json.dumps(
            {
                "remotive_total": report.remotive.total,
                "adzuna_skipped": report.adzuna.skipped,
                "adzuna_calls": report.adzuna.calls_used,
                "boards_ok": sum(1 for b in report.boards if b.status == "200"),
                "out": str(out),
            }
        )
    )
    return 1 if report.remotive.error else 0


def main() -> int:
    configure_logging()
    _quiet_http_loggers()
    parser = argparse.ArgumentParser(description="ING-00 source spike")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    return _emit(asyncio.run(_amain()), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
