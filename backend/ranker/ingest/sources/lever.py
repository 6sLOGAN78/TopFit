"""Lever public postings adapter (ING-13): one GET per board, each board its own scope.

Endpoint: ``https://api.lever.co/v0/postings/{slug}?mode=json`` (EU sites live on
``api.eu.lever.co`` and 404 on the global host). ``createdAt`` is epoch MILLISECONDS (Pitfall 5).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from pydantic import ValidationError

from ranker.ingest.board_registry import Board, Host, checked_url, sync_seeds
from ranker.ingest.http import SourceHTTPError, get_json
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.sources.base import AdapterContext, RawJob, SourceAdapter, register
from ranker.ingest.sources.boardbase import BoardAdapter, company_from_slug, number, s, seeds_for

HOST = "api.lever.co"
HOST_EU = "api.eu.lever.co"
PATH = "/v0/postings/{slug}"
MAX_EPOCH_MS = 4_102_444_800_000  # 2100-01-01; anything later is garbage

_WORKPLACE = {"onsite": "onsite", "remote": "remote", "hybrid": "hybrid"}
_INTERVAL = {"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour"}


def _created_at(value: object) -> datetime | None:
    ms = number(value, maximum=MAX_EPOCH_MS)
    if ms is None or ms <= 0 or ms > MAX_EPOCH_MS:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except OverflowError, OSError, ValueError:
        return None


def _lists(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw: Any = item.get("lists")
    if not isinstance(raw, list):
        return []
    return [cast(dict[str, Any], x) for x in cast(list[Any], raw) if isinstance(x, dict)]


def _description(item: dict[str, Any]) -> tuple[str, bool]:
    plain = s(item.get("descriptionPlain"))
    if plain is not None:
        chunks = [plain]
        for lst in _lists(item):
            heading = s(lst.get("text"))
            body = s(lst.get("content"))
            if heading:
                chunks.append(heading)
            if body:
                chunks.append(clean_description(body, is_html=True))
        extra = s(item.get("additionalPlain"))
        if extra:
            chunks.append(extra)
        return "\n\n".join(chunks), False
    html_parts: list[str] = []
    for key in ("description",):
        value = s(item.get(key))
        if value:
            html_parts.append(value)
    for lst in _lists(item):
        heading = s(lst.get("text"))
        body = s(lst.get("content"))
        if heading:
            html_parts.append(f"<h3>{heading}</h3>")
        if body:
            html_parts.append(f"<ul>{body}</ul>")
    extra_html = s(item.get("additional"))
    if extra_html:
        html_parts.append(extra_html)
    return "".join(html_parts), True


def _to_raw(item: dict[str, Any], slug: str) -> RawJob:
    cats_raw: Any = item.get("categories")
    cats: dict[str, Any] = cast(dict[str, Any], cats_raw) if isinstance(cats_raw, dict) else {}
    all_locations: Any = cats.get("allLocations")
    parts = (
        [x.strip() for x in cast(list[Any], all_locations) if isinstance(x, str) and x.strip()]
        if isinstance(all_locations, list)
        else []
    )
    workplace = _WORKPLACE.get((s(item.get("workplaceType")) or "").lower().replace("-", ""))
    country = s(item.get("country"))
    description, is_html = _description(item)
    lo = hi = currency = period = None
    sal: Any = item.get("salaryRange")
    if isinstance(sal, dict):
        sal_d = cast(dict[str, Any], sal)
        period = _INTERVAL.get(s(sal_d.get("interval")) or "")
        lo, hi = number(sal_d.get("min")), number(sal_d.get("max"))
        if period is None or (lo is None and hi is None):
            lo = hi = period = None
        else:
            currency = s(sal_d.get("currency"))
    return RawJob(
        source="lever",
        source_job_id=str(item["id"]),
        source_scope=slug,
        url=item["hostedUrl"],
        title=item["text"],
        company_name=company_from_slug(slug),
        description_raw=description,
        description_is_html=is_html,
        location_text=s(cats.get("location")),
        location_parts=parts,
        country_hint=country,
        workplace_hint=workplace,
        category_hint=s(cats.get("department")) or s(cats.get("team")),
        posted_at=_created_at(item.get("createdAt")),
        salary_min=lo,
        salary_max=hi,
        salary_currency=currency,
        salary_period_hint=period,
        employment_type_hint=s(cats.get("commitment")),
    )


def parse_board(payload: object, slug: str) -> tuple[list[RawJob], int]:
    jobs: list[RawJob] = []
    invalid = 0
    if not isinstance(payload, list):
        return jobs, invalid
    for item in cast(list[Any], payload):
        try:
            if not isinstance(item, dict):
                raise TypeError
            jobs.append(_to_raw(cast(dict[str, Any], item), slug))
        except KeyError, TypeError, ValueError, ValidationError:
            invalid += 1
    return jobs, invalid


def board_request(slug: str, host: str = "global") -> tuple[str, dict[str, str]]:
    return checked_url(HOST_EU if host == "eu" else HOST, PATH, slug), {"mode": "json"}


class LeverAdapter(BoardAdapter):
    name = "lever"
    ats = "lever"

    async def fetch(self) -> AsyncIterator[RawJob]:
        await sync_seeds(self._engine, seeds_for(self.ats, self._cfg))
        async for job in self.fetch_boards():
            yield job

    def request(self, board: Board) -> tuple[str, dict[str, str]]:
        return board_request(board.slug, board.host)

    def parse(self, payload: object, slug: str) -> tuple[list[RawJob], int]:
        return parse_board(payload, slug)

    def payload_ok(self, payload: object) -> bool:
        return isinstance(payload, list)

    async def get_payload(
        self, client: httpx.AsyncClient, board: Board
    ) -> tuple[object, Host | None]:
        url, params = self.request(board)
        try:
            payload = await get_json(
                client, url, params=params, sleep=self._sleep, on_attempt=self._count_attempt
            )
        except SourceHTTPError as exc:
            if exc.status != 404 or board.host == "eu":
                raise
            # An EU site 404s on the global host; retry once on the EU host.
            eu_url, eu_params = board_request(board.slug, "eu")
            payload = await get_json(
                client, eu_url, params=eu_params, sleep=self._sleep, on_attempt=self._count_attempt
            )
            return payload, "eu"
        return payload, None


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return LeverAdapter(ctx)


register("lever", _factory)
