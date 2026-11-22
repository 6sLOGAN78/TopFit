"""SourceAdapter plugin contract (ING-04).

A new source is one module under ``ingest/sources/`` plus a config section; nothing
here (and no CHECK constraint) needs to change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, HttpUrl, RootModel, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.settings import Settings


class RawJob(BaseModel):
    source: str  # not a Literal: new sources must not touch shared code
    source_job_id: str
    source_scope: str = "all"
    url: HttpUrl
    title: str
    company_name: str
    description_raw: str = ""
    description_is_html: bool = False
    description_truncated: bool = False
    description_entity_escaped: bool = False
    location_text: str | None = None
    location_parts: list[str] = []
    country_hint: str | None = None
    workplace_hint: str | None = None
    region_hint: str | None = None
    category_hint: str | None = None
    posted_at: datetime | None = None
    salary_text: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period_hint: str | None = None
    salary_is_estimate: bool = False
    employment_type_hint: str | None = None
    contract_type_hint: str | None = None
    company_canonical_hint: str | None = None
    industry_hint: str | None = None
    company_size_hint: str | None = None
    company_stage_hint: str | None = None
    skills_hint: list[str] = []

    @field_validator("posted_at")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("posted_at must be timezone-aware")
        return v


@dataclass
class FetchResult:
    complete_scopes: set[str] = field(default_factory=lambda: set[str]())
    failed_scopes: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    calls_used: int = 0
    scope_counts: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    invalid_items: int = 0
    extra_notes: dict[str, object] = field(default_factory=lambda: dict[str, object]())


class CronSpec(BaseModel):
    hour: int
    minute: int
    day_of_week: str | None = None
    batch: Literal["morning", "intraday"] = "intraday"


class BaseSourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    expiry_policy: Literal["absence", "ttl"] = "absence"
    max_runs_per_day: int | None = None
    schedule: list[CronSpec] = []


class SourcesConfig(RootModel[dict[str, BaseSourceConfig]]):
    pass


@dataclass
class AdapterContext:
    settings: Settings
    engine: AsyncEngine
    config: BaseSourceConfig
    mode: Literal["incremental", "backfill"]
    now: datetime


class SourceAdapter(Protocol):
    name: str
    expiry_policy: Literal["absence", "ttl"]

    def configured(self) -> bool: ...

    def fetch(self) -> AsyncIterator[RawJob]: ...

    def result(self) -> FetchResult: ...


class UnknownSource(KeyError):
    pass


AdapterFactory = Callable[[AdapterContext], SourceAdapter]
_REGISTRY: dict[str, AdapterFactory] = {}


def register(name: str, factory: AdapterFactory) -> None:
    _REGISTRY[name] = factory


def get_adapter_factory(name: str) -> AdapterFactory:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownSource(name) from None


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)
