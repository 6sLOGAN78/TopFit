"""Adzuna adapter (ING-02, ING-04): date-sorted query slices under a measured daily quota.

Credentials travel only as query parameters through ``get_json`` (whose errors never carry the
URL), so they cannot reach exception text or run records. Search APIs never return the full
set of live jobs, so no scope is ever marked complete and expiry is TTL based (D-13).
"""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.sources.base import (
    AdapterContext,
    BaseSourceConfig,
    FetchResult,
    RawJob,
    SourceAdapter,
    register,
)

log = structlog.get_logger("ranker.ingest.adzuna")

API_BASE = "https://api.adzuna.com/v1/api/jobs"
SCOPE = "search"
MAX_ATTEMPTS = 5
MAX_SALARY = 1e10  # anything larger is garbage, not a salary


class AdzunaQuery(BaseModel):
    name: str
    category: str | None = None
    what_or: str | None = None
    sort_by: str = "date"
    max_days_old: int = 2
    max_pages: int = 5


class AdzunaQuota(BaseModel):
    calls_per_day: int = 0
    calls_per_month: int = 0


class AdzunaBackfill(BaseModel):
    auto: bool = False
    max_days_old: int = 30
    max_pages: int = 200


class AdzunaConfig(BaseSourceConfig):
    country: str = "in"
    results_per_page: int = 50
    quota: AdzunaQuota = Field(default_factory=AdzunaQuota)
    min_interval_seconds: float = 2.5
    salary_period: str = "year"
    queries: list[AdzunaQuery] = Field(default_factory=lambda: list[AdzunaQuery]())
    backfill: AdzunaBackfill = Field(default_factory=AdzunaBackfill)


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


def _money(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0 or number > MAX_SALARY:
        return None
    return number


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true"}


def _sub(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _one_line(value: str) -> str:
    return " ".join(clean_description(value, is_html=True).split())


def _to_raw(item: dict[str, Any], cfg: AdzunaConfig) -> RawJob:
    job_id = item.get("id")
    title = _s(item.get("title"))
    redirect = _s(item.get("redirect_url"))
    if job_id in (None, "") or isinstance(job_id, bool) or not title or not redirect:
        raise ValueError("missing id/title/redirect_url")
    company = _sub(item, "company")
    location = _sub(item, "location")
    category = _sub(item, "category")
    area = location.get("area")
    parts: list[str] = []
    if isinstance(area, list):
        parts = [a for a in cast(list[Any], area) if isinstance(a, str)]
    full = _s(item.get("full_description"))
    smin, smax = _money(item.get("salary_min")), _money(item.get("salary_max"))
    has_salary = smin is not None or smax is not None
    return RawJob.model_validate(
        {
            "source": "adzuna",
            "source_job_id": str(job_id),
            "source_scope": SCOPE,
            "url": redirect,  # mandatory link back to Adzuna (D-13)
            "title": _one_line(title),
            "company_name": _s(company.get("display_name")) or "Unknown",
            "company_canonical_hint": _s(company.get("canonical_name")),
            "description_raw": full or _s(item.get("description")) or "",
            "description_is_html": True,
            "description_truncated": full is None,
            "location_text": _s(location.get("display_name")),
            "location_parts": parts,
            "country_hint": cfg.country.upper(),
            "category_hint": _s(category.get("tag")),
            "posted_at": _parse_dt(item.get("created")),
            "salary_min": smin,
            "salary_max": smax,
            "salary_currency": "INR" if has_salary else None,
            "salary_period_hint": cfg.salary_period if has_salary else None,
            "salary_is_estimate": has_salary and _truthy(item.get("salary_is_predicted")),
            "employment_type_hint": _s(item.get("contract_time")),
            "contract_type_hint": _s(item.get("contract_type")),
        }
    )


class AdzunaAdapter:
    name = "adzuna"
    expiry_policy: Literal["absence", "ttl"] = "ttl"

    def __init__(
        self,
        ctx: AdapterContext,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ctx = ctx
        self._cfg = AdzunaConfig.model_validate(ctx.config.model_dump())
        self._sleep = sleep
        self._result = FetchResult()
        self._first_call = True
        self._stopped = False
        self._used_day = 0
        self._used_month = 0
        self._seen: set[str] = set()

    def configured(self) -> bool:
        s = self._ctx.settings
        return s.adzuna_app_id is not None and s.adzuna_app_key is not None

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    async def _usage(self) -> tuple[int, int]:
        """Calls recorded in ingest_runs for UTC today and this UTC month."""
        now = self._ctx.now
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        sql = text(
            "SELECT COALESCE(SUM(calls_used) FILTER (WHERE started_at >= :day), 0), "
            "COALESCE(SUM(calls_used), 0) FROM ingest_runs "
            "WHERE source = 'adzuna' AND started_at >= :month"
        )
        async with self._ctx.engine.connect() as conn:
            row = (await conn.execute(sql, {"day": day, "month": month})).one()
        return int(row[0]), int(row[1])

    async def _prior_backfill(self) -> tuple[dict[str, int], bool]:
        async with self._ctx.engine.connect() as conn:
            cursor_row = (
                await conn.execute(
                    text(
                        "SELECT notes -> 'backfill_cursor' FROM ingest_runs "
                        "WHERE source = 'adzuna' AND notes -> 'backfill_cursor' IS NOT NULL "
                        "ORDER BY started_at DESC, id DESC LIMIT 1"
                    )
                )
            ).first()
            done_row = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM ingest_runs WHERE source = 'adzuna' "
                        "AND notes ->> 'backfill_complete' = 'true' LIMIT 1"
                    )
                )
            ).first()
        cursor: dict[str, int] = {}
        raw = cursor_row[0] if cursor_row else None
        if isinstance(raw, dict):
            for k, v in cast(dict[str, Any], raw).items():
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    cursor[str(k)] = v
        return cursor, done_row is not None

    def _remaining(self) -> int:
        q = self._cfg.quota
        spent = self._result.calls_used
        return max(
            0,
            min(
                q.calls_per_day - self._used_day - spent,
                q.calls_per_month - self._used_month - spent,
            ),
        )

    def _params(self, query: AdzunaQuery, max_days: int) -> dict[str, str | int]:
        s = self._ctx.settings
        params: dict[str, str | int] = {
            "app_id": s.adzuna_app_id.get_secret_value() if s.adzuna_app_id else "",
            "app_key": s.adzuna_app_key.get_secret_value() if s.adzuna_app_key else "",
            "results_per_page": self._cfg.results_per_page,
            "sort_by": query.sort_by,
            "max_days_old": max_days,
            "content-type": "application/json",
        }
        if query.category:
            params["category"] = query.category
        if query.what_or:
            params["what_or"] = query.what_or
        return params

    async def _crawl(
        self,
        client: httpx.AsyncClient,
        query: AdzunaQuery,
        start: int,
        max_days: int,
        max_pages: int,
    ) -> AsyncIterator[RawJob | int]:
        """Yield jobs, then the next page to fetch (0 = slice finished) as the final int."""
        page = start
        while page <= max_pages:
            remaining = self._remaining()
            if remaining <= 0:
                self._result.extra_notes["budget_exhausted"] = True
                self._stopped = True
                yield page
                return
            if not self._first_call:
                await self._sleep(self._cfg.min_interval_seconds)
            self._first_call = False
            url = f"{API_BASE}/{self._cfg.country}/search/{page}"
            try:
                payload = await get_json(
                    client,
                    url,
                    params=self._params(query, max_days),
                    max_attempts=min(MAX_ATTEMPTS, remaining),
                    sleep=self._sleep,
                    on_attempt=self._count_attempt,
                )
            except SourceHTTPError as exc:
                self._result.failed_scopes[SCOPE] = f"{exc.error_type}:{exc.status}"
                log.warning("source_fetch_failed", source=self.name, error=str(exc))
                self._stopped = True
                yield page
                return
            results: object = (
                cast(dict[str, Any], payload).get("results") if isinstance(payload, dict) else None
            )
            if not isinstance(results, list):
                self._result.failed_scopes[SCOPE] = "InvalidPayload:None"
                self._stopped = True
                yield page
                return
            items = cast(list[Any], results)
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise TypeError
                    job = _to_raw(cast(dict[str, Any], item), self._cfg)
                except KeyError, TypeError, ValueError, ValidationError:
                    self._result.invalid_items += 1
                    continue
                if job.source_job_id in self._seen:
                    continue
                self._seen.add(job.source_job_id)
                yield job
            if len(items) < self._cfg.results_per_page:
                yield 0
                return
            page += 1
        yield 0

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        self._first_call = True
        self._stopped = False
        self._seen = set()
        self._used_day, self._used_month = await self._usage()
        fetched = 0
        mode = self._ctx.mode
        bf = self._cfg.backfill
        async with build_client() as client:
            if mode == "incremental":
                for query in self._cfg.queries:
                    if self._stopped:
                        break
                    async for out in self._crawl(
                        client, query, 1, query.max_days_old, query.max_pages
                    ):
                        if isinstance(out, RawJob):
                            fetched += 1
                            yield out
            cursor: dict[str, int] = {}
            run_backfill = False
            if mode == "backfill" or (mode == "incremental" and bf.auto and not self._stopped):
                cursor, complete = await self._prior_backfill()
                run_backfill = mode == "backfill" or not complete
            if run_backfill:
                next_cursor = {q.name: cursor.get(q.name, 1) for q in self._cfg.queries}
                for query in self._cfg.queries:
                    if self._stopped:
                        break
                    if next_cursor[query.name] == 0:
                        continue
                    async for out in self._crawl(
                        client, query, next_cursor[query.name], bf.max_days_old, bf.max_pages
                    ):
                        if isinstance(out, RawJob):
                            fetched += 1
                            yield out
                        else:
                            next_cursor[query.name] = out
                self._result.extra_notes["backfill_cursor"] = next_cursor
                self._result.extra_notes["backfill_complete"] = all(
                    v == 0 for v in next_cursor.values()
                )
        self._result.scope_counts[SCOPE] = fetched
        log.info(
            "source_fetch_done",
            source=self.name,
            fetched=fetched,
            invalid=self._result.invalid_items,
            calls=self._result.calls_used,
        )

    def result(self) -> FetchResult:
        return self._result


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return AdzunaAdapter(ctx)


register("adzuna", _factory)
