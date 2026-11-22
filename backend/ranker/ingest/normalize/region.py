"""Remote-eligibility rules (NORM-03, D-06/D-08).

Pure rules only; the LLM fallback for ``unknown`` lives in the enrichment task (plan 02-12).
Phrases are matched as whole words with literal alternations (no nested quantifiers), and
descriptions are scanned over the first 3,000 and last 1,000 characters only.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ranker.config_loader import load_config
from ranker.ingest.normalize.location import indian_city_names

RemoteEligibility = Literal[
    "worldwide", "india", "apac", "us_only", "eu_only", "other_region", "unknown"
]
RegionSource = Literal["hint", "description", "location", "none"]

_HEAD_CHARS = 3000
_TAIL_CHARS = 1000


class TokenPhrases(BaseModel):
    model_config = ConfigDict(frozen=True)
    phrases: list[str]
    hint_only: list[str] = []


class RegionsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens: dict[str, TokenPhrases]


class RegionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    remote_eligibility: RemoteEligibility
    remote_regions: list[str]
    source: RegionSource


@dataclass(frozen=True)
class _Matchers:
    cfg: RegionsConfig
    description: dict[str, re.Pattern[str]]
    hint: dict[str, re.Pattern[str]]


_cache: _Matchers | None = None


def _compile(phrases: list[str]) -> re.Pattern[str]:
    ordered = sorted({p.lower() for p in phrases}, key=len, reverse=True)
    body = "|".join(re.escape(p) for p in ordered)
    return re.compile(r"(?<!\w)(?:" + body + r")(?![\w+])")


def _matchers() -> _Matchers:
    global _cache
    cfg = load_config("regions.yaml", RegionsConfig)
    if _cache is not None and _cache.cfg is cfg:
        return _cache
    description: dict[str, re.Pattern[str]] = {}
    hint: dict[str, re.Pattern[str]] = {}
    for name, spec in cfg.tokens.items():
        base = list(spec.phrases)
        if name == "india":
            base += indian_city_names()
        description[name] = _compile(base)
        hint[name] = _compile(base + spec.hint_only)
    _cache = _Matchers(cfg, description, hint)
    return _cache


def _tokens(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    lowered = text.lower()
    return {token for token, pattern in patterns.items() if pattern.search(lowered)}


def _description_window(description: str) -> str:
    if len(description) <= _HEAD_CHARS + _TAIL_CHARS:
        return description
    return description[:_HEAD_CHARS] + "\n" + description[-_TAIL_CHARS:]


def _eligibility(tokens: set[str]) -> RemoteEligibility:
    if "worldwide" in tokens:
        return "worldwide"
    if "india" in tokens:
        return "india"
    if "apac" in tokens:
        return "apac"
    if tokens == {"us"}:
        return "us_only"
    if tokens == {"eu"}:
        return "eu_only"
    if tokens:
        return "other_region"
    return "unknown"


def _result(tokens: set[str], source: RegionSource) -> RegionResult:
    return RegionResult(
        remote_eligibility=_eligibility(tokens),
        remote_regions=sorted(tokens),
        source=source,
    )


def detect_remote_eligibility(
    *,
    region_hint: str | None,
    description: str | None,
    country_code: str | None,
    remote_type: str,
) -> RegionResult:
    """Order: region hint, then (non-remote with a known country) the location, then the
    description window, else unknown. For on-site and hybrid jobs the job's own country is
    more reliable than description phrases, so it is checked before the description."""
    m = _matchers()
    if region_hint:
        tokens = _tokens(region_hint, m.hint)
        if tokens:
            return _result(tokens, "hint")
    if remote_type != "remote" and country_code:
        return _result({"india"} if country_code == "IN" else {"other"}, "location")
    if description:
        tokens = _tokens(_description_window(description), m.description)
        if tokens:
            return _result(tokens, "description")
    return _result(set(), "none")
