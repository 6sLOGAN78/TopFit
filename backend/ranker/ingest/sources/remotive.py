"""Remotive adapter: one GET of the public feed per run (<= 4/day, credit + link back)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, cast

import structlog
from pydantic import ValidationError

from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.sources.base import (
    AdapterContext,
    BaseSourceConfig,
    FetchResult,
    RawJob,
    SourceAdapter,
    register,
)

log = structlog.get_logger("ranker.ingest.remotive")

DEFAULT_URL = "https://remotive.com/api/remote-jobs"


class RemotiveConfig(BaseSourceConfig):
    url: str = DEFAULT_URL


def _s(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _parse_dt(value: object) -> datetime | None:
    raw = _s(value)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _to_raw(item: dict[str, Any]) -> RawJob:
    return RawJob(
        source="remotive",
        source_job_id=str(item["id"]),
        source_scope="all",
        url=item["url"],
        title=item["title"],
        company_name=item["company_name"],
        description_raw=item.get("description") or "",
        description_is_html=True,
        workplace_hint="remote",
        region_hint=_s(item.get("candidate_required_location")),
        category_hint=_s(item.get("category")),
        posted_at=_parse_dt(item.get("publication_date")),
        salary_text=_s(item.get("salary")),
        employment_type_hint=_s(item.get("job_type")),
    )


class RemotiveAdapter:
    name = "remotive"
    expiry_policy: Literal["absence", "ttl"] = "absence"

    def __init__(self, ctx: AdapterContext) -> None:
        self._url = RemotiveConfig.model_validate(ctx.config.model_dump()).url
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        try:
            async with build_client() as client:
                payload = await get_json(client, self._url, on_attempt=self._count_attempt)
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
        for item in jobs:  # pyright: ignore[reportUnknownVariableType]
            try:
                if not isinstance(item, dict):
                    raise TypeError
                job = _to_raw(item)  # pyright: ignore[reportUnknownArgumentType]
            except KeyError, TypeError, ValueError, ValidationError:
                self._result.invalid_items += 1
                continue
            unique[job.source_job_id] = job  # last wins
        self._result.complete_scopes.add("all")
        self._result.scope_counts["all"] = len(unique)
        log.info(
            "source_fetch_done",
            source=self.name,
            fetched=len(unique),
            invalid=self._result.invalid_items,
            calls=self._result.calls_used,
        )
        for job in unique.values():
            yield job

    def result(self) -> FetchResult:
        return self._result


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return RemotiveAdapter(ctx)


register("remotive", _factory)
