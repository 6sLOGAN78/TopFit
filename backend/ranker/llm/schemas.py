"""Typed LLM output schemas. No free-form dict fields (Claude returns {} for them)."""

from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ranker.config_loader import load_config
from ranker.ingest.normalize.location import CitiesConfig

RemoteEligibility = Literal[
    "worldwide", "india", "apac", "us_only", "eu_only", "other_region", "unknown"
]
Seniority = Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "unknown"]


class JobEnrichment(BaseModel):
    """One call fills every unknown field; callers only apply fields still unknown."""

    model_config = ConfigDict(extra="forbid")

    remote_eligibility: RemoteEligibility
    seniority: Seniority
    employment_type: EmploymentType


SALARY_BUCKETS_LPA = (3, 4, 5, 6, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50)
WORK_MODE_ANSWERS = ("remote", "hybrid", "onsite", "remote_or_city", "unspecified")
SENIORITY_ANSWERS = ("intern", "junior", "mid", "senior", "lead", "unspecified")
EMPLOYMENT_ANSWERS = ("full_time", "part_time", "contract", "internship", "unspecified")
ROLE_FAMILY_ANSWERS = (
    "backend",
    "frontend",
    "fullstack",
    "data",
    "ml",
    "devops",
    "mobile",
    "qa",
    "other",
    "unspecified",
)


@cache
def city_answers() -> tuple[str, ...]:
    cfg = load_config("cities.yaml", CitiesConfig)
    return (*(c.canonical for c in cfg.cities if c.country == "IN"), "none", "other")


def salary_answers() -> tuple[str, ...]:
    return ("none", *(str(b) for b in SALARY_BUCKETS_LPA))


def _enum_field(values: tuple[str, ...]) -> Any:
    return Field(json_schema_extra={"enum": list(values)})


class QueryParseFallback(BaseModel):
    """Enum-only answers for the query-parse fallback: every property is a closed set, so
    prompt injection in a query cannot produce free text (T-03-14). Shared by Haiku and Jev.
    """

    model_config = ConfigDict(extra="forbid")

    city: str = _enum_field(())
    work_mode: str = _enum_field(WORK_MODE_ANSWERS)
    seniority: str = _enum_field(SENIORITY_ANSWERS)
    employment_type: str = _enum_field(EMPLOYMENT_ANSWERS)
    min_salary_lpa: str = _enum_field(salary_answers())
    role_family: str = _enum_field(ROLE_FAMILY_ANSWERS)

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        if v not in city_answers():
            raise ValueError("city not in roster")
        return v

    @field_validator("work_mode")
    @classmethod
    def _work_mode(cls, v: str) -> str:
        return _check(v, WORK_MODE_ANSWERS)

    @field_validator("seniority")
    @classmethod
    def _seniority(cls, v: str) -> str:
        return _check(v, SENIORITY_ANSWERS)

    @field_validator("employment_type")
    @classmethod
    def _employment(cls, v: str) -> str:
        return _check(v, EMPLOYMENT_ANSWERS)

    @field_validator("min_salary_lpa")
    @classmethod
    def _salary(cls, v: str) -> str:
        return _check(v, salary_answers())

    @field_validator("role_family")
    @classmethod
    def _role(cls, v: str) -> str:
        return _check(v, ROLE_FAMILY_ANSWERS)


def _check(v: str, allowed: tuple[str, ...]) -> str:
    if v not in allowed:
        raise ValueError("value not in enum")
    return v


def _with_city_enum() -> None:
    """The city enum comes from cities.yaml, so it is patched into the schema at import."""
    QueryParseFallback.model_fields["city"].json_schema_extra = {"enum": list(city_answers())}
    QueryParseFallback.model_rebuild(force=True)


_with_city_enum()

__all__ = [
    "SALARY_BUCKETS_LPA",
    "EmploymentType",
    "JobEnrichment",
    "QueryParseFallback",
    "RemoteEligibility",
    "Seniority",
    "lint_schema",
]

_FORBIDDEN_KEYS = ("oneOf", "minimum", "maximum", "minLength", "maxLength", "maxItems")


def _walk(
    node: Any, path: str, defs: dict[str, Any], out: list[str], stack: tuple[str, ...]
) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            _walk(item, f"{path}[{i}]", defs, out, stack)
        return
    if not isinstance(node, dict):
        return
    d: dict[str, Any] = node  # pyright: ignore[reportUnknownVariableType]
    for key in _FORBIDDEN_KEYS:
        if key in d:
            out.append(f"{path}: forbidden keyword {key}")
    if d.get("minItems", 0) > 1:
        out.append(f"{path}: minItems > 1")
    if d.get("type") == "object" or "properties" in d:
        if not d.get("properties"):
            out.append(f"{path}: object without properties")
        if d.get("additionalProperties") is not False:
            out.append(f"{path}: additionalProperties is not false")
    ref = d.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in stack:
            out.append(f"{path}: recursive $ref {name}")
        elif name in defs:
            _walk(defs[name], f"{path}->{name}", defs, out, (*stack, name))
    for key, value in d.items():
        if key in ("$defs", "$ref"):
            continue
        _walk(value, f"{path}.{key}", defs, out, stack)


def lint_schema(model: type[BaseModel]) -> list[str]:
    """Return violations of the Anthropic structured-output schema subset."""
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.get("$defs", {})
    out: list[str] = []
    _walk(schema, model.__name__, defs, out, ())
    return out
