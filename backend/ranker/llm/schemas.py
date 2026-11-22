"""Typed LLM output schemas. No free-form dict fields (Claude returns {} for them)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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


__all__ = [
    "EmploymentType",
    "JobEnrichment",
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
