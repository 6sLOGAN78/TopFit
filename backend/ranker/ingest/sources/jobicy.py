"""Jobicy adapter: one GET of the latest-N window, TTL expiry (ING-08).

Attribution (ADR 0001): credit Jobicy and link to its ``url``. ``geo=india`` returned HTTP 400
on 2026-11-22, so no geo filter is sent by default; scope rules decide eligibility.
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

log = structlog.get_logger("ranker.ingest.jobicy")

DEFAULT_URL = "https://jobicy.com/api/v2/remote-jobs"


class JobicyConfig(BaseSourceConfig):
    url: str = DEFAULT_URL
    count: int = 50
    # "india" returned HTTP 400 on 2026-11-22; leave None unless Jobicy documents a valid value.
    geo: str | None = None


def _to_raw(item: dict[str, Any]) -> RawJob:
    description = item.get("jobDescription") or item.get("jobExcerpt") or ""
    level = apiutil.s(item.get("jobLevel"), 100)
    if level:
        description = f"{description}<p>Level: {level}</p>"
    return RawJob(
        source="jobicy",
        source_job_id=str(item["id"]),
        source_scope="all",
        url=item["url"],
        title=item["jobTitle"],
        company_name=item["companyName"],
        description_raw=description,
        description_is_html=True,
        workplace_hint="remote",
        region_hint=apiutil.first_str(item.get("jobGeo")),
        posted_at=apiutil.parse_iso(item.get("pubDate")),
        salary_min=apiutil.safe_amount(item.get("annualSalaryMin")),
        salary_max=apiutil.safe_amount(item.get("annualSalaryMax")),
        salary_currency=apiutil.s(item.get("salaryCurrency"), 10),
        salary_period_hint="year",
        employment_type_hint=apiutil.first_str(item.get("jobType")),
        industry_hint=apiutil.first_str(item.get("jobIndustry")),
    )


class JobicyAdapter:
    name = "jobicy"
    expiry_policy: Literal["absence", "ttl"] = "ttl"

    def __init__(self, ctx: AdapterContext) -> None:
        self._cfg = JobicyConfig.model_validate(ctx.config.model_dump())
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        params: dict[str, str | int] = {"count": self._cfg.count}
        if self._cfg.geo:
            params["geo"] = self._cfg.geo
        try:
            async with build_client() as client:
                payload = await get_json(
                    client,
                    self._cfg.url,
                    params=params,
                    sleep=lambda sec: apiutil.sleep(sec),
                    on_attempt=self._count_attempt,
                )
        except SourceHTTPError as exc:
            self._result.failed_scopes["all"] = f"{exc.error_type}:{exc.status}"
            log.warning("source_fetch_failed", source=self.name, error=str(exc))
            return
        jobs: object = (
            cast(dict[str, object], payload).get("jobs") if isinstance(payload, dict) else None
        )
        if not isinstance(jobs, list):
            self._result.failed_scopes["all"] = "InvalidPayload:None"
            return
        unique: dict[str, RawJob] = {}
        for item in cast(list[Any], jobs):
            try:
                if not isinstance(item, dict):
                    raise TypeError
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
    return JobicyAdapter(ctx)


register("jobicy", _factory)
