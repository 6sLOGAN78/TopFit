"""Greenhouse public job-board adapter (ING-13): one GET per board, each board its own scope.

Endpoint: ``https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`` returns every
posting with no pagination. ``content`` is HTML-entity-escaped HTML (Pitfall 6), flagged on the
RawJob so the cleaner unescapes before sanitising.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError

from ranker.ingest.board_registry import Board, checked_url, sync_seeds
from ranker.ingest.sources.base import AdapterContext, RawJob, SourceAdapter, register
from ranker.ingest.sources.boardbase import BoardAdapter, company_from_slug, s, seeds_for

HOST = "boards-api.greenhouse.io"
PATH = "/v1/boards/{slug}/jobs"


def _parse_dt(value: object) -> datetime | None:
    raw = s(value)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _names(value: object, key: str) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for entry in cast(list[Any], value):
            if isinstance(entry, dict):
                name = s(cast(dict[str, Any], entry).get(key))
                if name:
                    out.append(name)
    return out


def _to_raw(item: dict[str, Any], slug: str) -> RawJob:
    location: Any = item.get("location")
    location_text = (
        s(cast(dict[str, Any], location).get("name")) if isinstance(location, dict) else None
    )
    departments = _names(item.get("departments"), "name")
    parts = [
        n for n in _names(item.get("offices"), "location") or _names(item.get("offices"), "name")
    ]
    content = s(item.get("content"))
    return RawJob(
        source="greenhouse",
        source_job_id=str(item["id"]),
        source_scope=slug,
        url=item["absolute_url"],
        title=item["title"],
        company_name=s(item.get("company_name")) or company_from_slug(slug),
        description_raw=content or "",
        description_is_html=content is not None,
        description_entity_escaped=content is not None,
        location_text=location_text,
        location_parts=parts,
        category_hint=departments[0] if departments else None,
        posted_at=_parse_dt(item.get("first_published")) or _parse_dt(item.get("updated_at")),
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
            jobs.append(_to_raw(cast(dict[str, Any], item), slug))
        except KeyError, TypeError, ValueError, ValidationError:
            invalid += 1
    return jobs, invalid


def board_request(slug: str, host: str = "global") -> tuple[str, dict[str, str]]:
    return checked_url(HOST, PATH, slug), {"content": "true"}


class GreenhouseAdapter(BoardAdapter):
    name = "greenhouse"
    ats = "greenhouse"

    async def fetch(self) -> AsyncIterator[RawJob]:
        await sync_seeds(self._engine, seeds_for(self.ats, self._cfg))
        async for job in self.fetch_boards():
            yield job

    def request(self, board: Board) -> tuple[str, dict[str, str]]:
        return board_request(board.slug)

    def parse(self, payload: object, slug: str) -> tuple[list[RawJob], int]:
        return parse_board(payload, slug)


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return GreenhouseAdapter(ctx)


register("greenhouse", _factory)
