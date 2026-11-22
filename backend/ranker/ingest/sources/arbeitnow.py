"""Arbeitnow adapter: paginated full feed, absence expiry per complete scope (ING-08).

Attribution (ADR 0001): credit Arbeitnow and link to the listing ``url``.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal, cast

import structlog
from pydantic import ValidationError

from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.sources import apiutil
from ranker.ingest.sources.base import (
    AdapterContext,
    BaseSourceConfig,
    FetchResult,
    RawJob,
    SourceAdapter,
    register,
)

log = structlog.get_logger("ranker.ingest.arbeitnow")

DEFAULT_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowConfig(BaseSourceConfig):
    url: str = DEFAULT_URL
    max_pages: int = 40
    min_interval_seconds: float = 1.0


def _to_raw(item: dict[str, Any]) -> RawJob:
    job_types = apiutil.first_str(item.get("job_types"))
    return RawJob(
        source="arbeitnow",
        source_job_id=str(item["slug"]),
        source_scope="all",
        url=item["url"],
        title=item["title"],
        company_name=item["company_name"],
        description_raw=item.get("description") or "",
        description_is_html=True,
        location_text=apiutil.s(item.get("location")),
        workplace_hint="remote" if item.get("remote") is True else None,
        posted_at=apiutil.parse_epoch(item.get("created_at")),
        employment_type_hint=job_types,
        skills_hint=apiutil.str_list(item.get("tags")),
    )


class ArbeitnowAdapter:
    name = "arbeitnow"
    expiry_policy: Literal["absence", "ttl"] = "absence"

    def __init__(self, ctx: AdapterContext) -> None:
        self._cfg = ArbeitnowConfig.model_validate(ctx.config.model_dump())
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        seen: dict[str, RawJob] = {}
        page = 1
        more = True
        try:
            async with build_client() as client:
                while more:
                    if page > self._cfg.max_pages:
                        self._result.failed_scopes["all"] = "page_cap"
                        break
                    if page > 1 and self._cfg.min_interval_seconds > 0:
                        await apiutil.sleep(self._cfg.min_interval_seconds)
                    payload = await get_json(
                        client,
                        self._cfg.url,
                        params={"page": page},
                        sleep=lambda sec: apiutil.sleep(sec),
                        on_attempt=self._count_attempt,
                    )
                    body = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
                    data: object = body.get("data")
                    if not isinstance(data, list):
                        raise SourceHTTPError(None, "InvalidPayload")
                    items = cast(list[Any], data)
                    for item in items:
                        try:
                            if not isinstance(item, dict):
                                raise TypeError
                            job = _to_raw(cast(dict[str, Any], item))
                        except KeyError, TypeError, ValueError, ValidationError:
                            self._result.invalid_items += 1
                            continue
                        seen[job.source_job_id] = job
                    links: object = body.get("links")
                    nxt = (
                        cast(dict[str, Any], links).get("next") if isinstance(links, dict) else None
                    )
                    more = bool(nxt) and bool(items)
                    page += 1
        except SourceHTTPError as exc:
            self._result.failed_scopes["all"] = f"{exc.error_type}:{exc.status}"
            log.warning("source_fetch_failed", source=self.name, error=str(exc))
        if "all" not in self._result.failed_scopes:
            self._result.complete_scopes.add("all")
        self._result.scope_counts["all"] = len(seen)
        for job in seen.values():
            yield job

    def result(self) -> FetchResult:
        return self._result


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return ArbeitnowAdapter(ctx)


register("arbeitnow", _factory)
