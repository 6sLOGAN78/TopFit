"""RemoteOK adapter: one GET of the capped latest-jobs window, TTL expiry (ING-08).

Attribution (ADR 0001): credit RemoteOK and link to its own ``url`` (not apply_url).
The first element of the response is a legal notice with no ``position``; it is skipped.
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

log = structlog.get_logger("ranker.ingest.remoteok")

DEFAULT_URL = "https://remoteok.com/api"


class RemoteOKConfig(BaseSourceConfig):
    url: str = DEFAULT_URL


def _to_raw(item: dict[str, Any]) -> RawJob:
    return RawJob(
        source="remoteok",
        source_job_id=str(item["id"]),
        source_scope="all",
        url=item["url"],
        title=item["position"],
        company_name=item["company"],
        description_raw=item.get("description") or "",
        description_is_html=True,
        workplace_hint="remote",
        region_hint=apiutil.s(item.get("location")),
        posted_at=apiutil.parse_iso(item.get("date")) or apiutil.parse_epoch(item.get("epoch")),
        salary_min=apiutil.safe_amount(item.get("salary_min")),
        salary_max=apiutil.safe_amount(item.get("salary_max")),
        salary_currency="USD",
        salary_period_hint="year",
        skills_hint=apiutil.str_list(item.get("tags")),
    )


class RemoteOKAdapter:
    name = "remoteok"
    expiry_policy: Literal["absence", "ttl"] = "ttl"

    def __init__(self, ctx: AdapterContext) -> None:
        self._url = RemoteOKConfig.model_validate(ctx.config.model_dump()).url
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        try:
            async with build_client() as client:
                payload = await get_json(
                    client,
                    self._url,
                    sleep=lambda sec: apiutil.sleep(sec),
                    on_attempt=self._count_attempt,
                )
        except SourceHTTPError as exc:
            self._result.failed_scopes["all"] = f"{exc.error_type}:{exc.status}"
            log.warning("source_fetch_failed", source=self.name, error=str(exc))
            return
        if not isinstance(payload, list):
            self._result.failed_scopes["all"] = "InvalidPayload:None"
            return
        unique: dict[str, RawJob] = {}
        for item in cast(list[Any], payload):
            if not isinstance(item, dict) or "position" not in item:
                continue  # legal notice / non-job element
            try:
                job = _to_raw(cast(dict[str, Any], item))
            except KeyError, TypeError, ValueError, ValidationError:
                self._result.invalid_items += 1
                continue
            unique[job.source_job_id] = job
        self._result.scope_counts["all"] = len(unique)
        for job in unique.values():
            yield job

    def result(self) -> FetchResult:
        return self._result


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return RemoteOKAdapter(ctx)


register("remoteok", _factory)
