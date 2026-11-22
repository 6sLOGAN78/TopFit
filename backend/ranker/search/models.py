"""Search API contract (request filters and response models).

The whole contract lives here from the first search plan so later plans change behaviour
without changing the OpenAPI schema. Value sets are imported, never redefined.
"""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ranker.ingest.normalize.location import RemoteType
from ranker.llm.schemas import EmploymentType, RemoteEligibility, Seniority

WorkMode = Literal["remote", "hybrid", "onsite"]
SeniorityFilter = Literal["intern", "junior", "mid", "senior", "lead"]
ParseModel = Literal["haiku", "jev"]
ChipSource = Literal["rules", "haiku", "jev", "user"]
ChipField = Literal["city", "min_salary", "work_mode", "seniority"]
SetAsideKey = Literal["geo", "city", "work_mode", "seniority", "min_salary", "estimates"]
ParseSource = Literal["rules", "rules+haiku", "rules+jev"]
GhostReason = Literal["stale_60d", "reposted_3x", "no_apply_url"]

MAX_QUERY_CHARS = 500
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SPACES = re.compile(r"\s+")


def clean_query(q: str) -> str:
    """Strip C0/C1 control characters (incl. NUL, which Postgres rejects) and collapse spaces."""
    return _SPACES.sub(" ", _CONTROL.sub(" ", q)).strip()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFilters(_Model):
    cities: list[str] = Field(default=[], max_length=10)
    remote_ok: bool = False
    work_modes: list[WorkMode] = []
    seniority: list[SeniorityFilter] = []
    min_salary_lpa: int | None = None
    exclude_estimates: bool = False


class Chip(_Model):
    field: ChipField
    value: str
    label: str
    source: ChipSource


class ParsedSummary(_Model):
    chips: list[Chip] = []
    parse_source: ParseSource = "rules"
    skills: list[str] = []
    filters: SearchFilters
    cached: bool = False


class JobResult(_Model):
    id: int
    title: str
    company: str | None
    source: str
    source_display_name: str
    url: str
    city: str | None
    cities: list[str]
    country_code: str | None
    remote_type: RemoteType
    remote_eligibility: RemoteEligibility
    onsite_days_per_week: int | None
    seniority: Seniority
    employment_type: EmploymentType
    salary_min_lpa: float | None
    salary_max_lpa: float | None
    salary_is_estimate: bool
    skills: list[str]
    matched_skills: list[str]
    ghost_reasons: list[GhostReason]
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    why_matched: str


class SetAsideCount(_Model):
    filter: SetAsideKey
    count: int


class RelaxOption(_Model):
    filter: SetAsideKey
    count: int


class SearchResponse(_Model):
    items: list[JobResult]
    next_cursor: str | None
    search_log_id: UUID | None
    parsed: ParsedSummary
    set_aside: list[SetAsideCount]
    set_aside_universe: int
    relax: list[RelaxOption]
    degraded: bool
    pipeline_version: str


class MetaResponse(_Model):
    active_jobs: int
    parse_models: list[ParseModel]
    cities: list[str]


class ErrorBody(_Model):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(_Model):
    error: ErrorBody
