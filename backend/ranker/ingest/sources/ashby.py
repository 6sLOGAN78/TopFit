"""Ashby public job-board adapter (ING-13): one GET per board, each board its own scope.

Endpoint (public, unauthenticated): ``https://api.ashbyhq.com/posting-api/job-board/{slug}``
with ``includeCompensation=true``. Plain-text descriptions are preferred (no HTML to sanitise).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError

from ranker.ingest.board_registry import Board, checked_url, sync_seeds
from ranker.ingest.sources.base import AdapterContext, RawJob, SourceAdapter, register
from ranker.ingest.sources.boardbase import BoardAdapter, company_from_slug, number, s, seeds_for

HOST = "api.ashbyhq.com"
PATH = "/posting-api/job-board/{slug}"

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}
_EMPLOYMENT = {
    "fulltime": "full_time",
    "parttime": "part_time",
    "intern": "internship",
    "contract": "contract",
    "temporary": "temporary",
}
_COUNTRY = {"india": "IN", "in": "IN"}


def _parse_dt(value: object) -> datetime | None:
    raw = s(value)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _salary(item: dict[str, Any]) -> tuple[float | None, float | None, str | None, str | None]:
    comp = item.get("compensation")
    if not isinstance(comp, dict):
        return None, None, None, None
    components: Any = cast(dict[str, Any], comp).get("summaryComponents")
    if not isinstance(components, list):
        return None, None, None, None
    for c in cast(list[Any], components):
        if not isinstance(c, dict):
            continue
        comp_d = cast(dict[str, Any], c)
        if comp_d.get("compensationType") != "Salary":
            continue
        interval = s(comp_d.get("interval"))
        if interval is None or interval.upper() != "1 YEAR":
            return None, None, None, None
        lo, hi = number(comp_d.get("minValue")), number(comp_d.get("maxValue"))
        if lo is None and hi is None:
            return None, None, None, None
        return lo, hi, s(comp_d.get("currencyCode")), "year"
    return None, None, None, None


def _to_raw(item: dict[str, Any], slug: str) -> RawJob | None:
    if item.get("isListed") is False:
        return None
    workplace = _WORKPLACE.get((s(item.get("workplaceType")) or "").lower())
    if item.get("isRemote") is True:
        workplace = "remote"
    parts: list[str] = []
    secondary: Any = item.get("secondaryLocations")
    if isinstance(secondary, list):
        for sec in cast(list[Any], secondary):
            name = s(cast(dict[str, Any], sec).get("location")) if isinstance(sec, dict) else s(sec)
            if name:
                parts.append(name)
    primary = s(item.get("location"))
    if primary:
        parts.insert(0, primary)
    country: str | None = None
    addr: Any = item.get("address")
    if isinstance(addr, dict):
        postal: Any = cast(dict[str, Any], addr).get("postalAddress")
        if isinstance(postal, dict):
            raw_country = s(cast(dict[str, Any], postal).get("addressCountry"))
            if raw_country:
                country = _COUNTRY.get(raw_country.lower(), raw_country)
    plain = s(item.get("descriptionPlain"))
    html = s(item.get("descriptionHtml"))
    lo, hi, currency, period = _salary(item)
    return RawJob(
        source="ashby",
        source_job_id=str(item["id"]),
        source_scope=slug,
        url=item["jobUrl"],
        title=item["title"],
        company_name=company_from_slug(slug),
        description_raw=plain or html or "",
        description_is_html=plain is None and html is not None,
        location_text=primary,
        location_parts=parts,
        country_hint=country,
        workplace_hint=workplace,
        category_hint=s(item.get("department")),
        posted_at=_parse_dt(item.get("publishedAt")),
        salary_min=lo,
        salary_max=hi,
        salary_currency=currency,
        salary_period_hint=period,
        employment_type_hint=_EMPLOYMENT.get((s(item.get("employmentType")) or "").lower()),
    )


def parse_board(payload: object, slug: str) -> tuple[list[RawJob], int]:
    jobs: list[RawJob] = []
    invalid = 0
    items: Any = cast(dict[str, Any], payload).get("jobs") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return jobs, invalid
    for item in cast(list[Any], items):
        try:
            if not isinstance(item, dict):
                raise TypeError
            raw = _to_raw(cast(dict[str, Any], item), slug)
        except KeyError, TypeError, ValueError, ValidationError:
            invalid += 1
            continue
        if raw is not None:
            jobs.append(raw)
    return jobs, invalid


def board_request(slug: str, host: str = "global") -> tuple[str, dict[str, str]]:
    return checked_url(HOST, PATH, slug), {"includeCompensation": "true"}


class AshbyAdapter(BoardAdapter):
    name = "ashby"
    ats = "ashby"

    async def fetch(self) -> AsyncIterator[RawJob]:
        await sync_seeds(self._engine, seeds_for(self.ats, self._cfg))
        async for job in self.fetch_boards():
            yield job
            del job  # do not pin the last posting while the next board is fetched (T-02-75)

    def request(self, board: Board) -> tuple[str, dict[str, str]]:
        return board_request(board.slug)

    def parse(self, payload: object, slug: str) -> tuple[list[RawJob], int]:
        return parse_board(payload, slug)


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return AshbyAdapter(ctx)


register("ashby", _factory)
