"""Tech and geography scope rules (D-05, D-06).

Out-of-scope jobs are skipped at ingest (not stored); see ADR 0002.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ranker.config_loader import load_config
from ranker.ingest.normalize.location import Location

Family = Literal["engineering", "data", "ml_ai", "devops", "qa", "security", "mobile"]
Reason = Literal["tech", "category", "excluded", "not_tech", "geo_out_of_scope", "geo_unknown"]

_TITLE_CHARS = 300


class FamilyPatterns(BaseModel):
    model_config = ConfigDict(frozen=True)
    family: Family
    patterns: list[str]


class ScopeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    include_title: list[str]
    exclude_title: list[str]
    tech_categories: list[str]
    tech_category_keywords: list[str]
    category_family: dict[str, Family] = {}
    family_patterns: list[FamilyPatterns]


class ScopeDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    in_scope: bool
    reason: Reason
    family: Family | None = None


@dataclass(frozen=True)
class _Compiled:
    cfg: ScopeConfig
    include: re.Pattern[str]
    exclude: re.Pattern[str]
    categories: frozenset[str]
    families: list[tuple[Family, re.Pattern[str]]]


_cache: _Compiled | None = None


def _alternation(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(patterns) + r")(?![a-z0-9])", re.IGNORECASE)


def _compiled() -> _Compiled:
    global _cache
    cfg = load_config("scope.yaml", ScopeConfig)
    if _cache is not None and _cache.cfg is cfg:
        return _cache
    _cache = _Compiled(
        cfg=cfg,
        include=_alternation(cfg.include_title),
        exclude=_alternation(cfg.exclude_title),
        categories=frozenset(c.lower() for c in cfg.tech_categories),
        families=[(f.family, _alternation(f.patterns)) for f in cfg.family_patterns],
    )
    return _cache


def _family(title: str, category: str | None, c: _Compiled) -> Family:
    for family, pattern in c.families:
        if pattern.search(title):
            return family
    if category:
        mapped = c.cfg.category_family.get(category.strip().lower())
        if mapped:
            return mapped
    return "engineering"


def _category_is_tech(category: str | None, c: _Compiled) -> bool:
    if not category:
        return False
    lowered = category.strip().lower()
    if lowered in c.categories:
        return True
    return any(k in lowered for k in c.cfg.tech_category_keywords)


def classify_tech(title: str, category: str | None) -> ScopeDecision:
    c = _compiled()
    text = title[:_TITLE_CHARS]
    if c.exclude.search(text):
        return ScopeDecision(in_scope=False, reason="excluded")
    if c.include.search(text):
        return ScopeDecision(in_scope=True, reason="tech", family=_family(text, category, c))
    if _category_is_tech(category, c):
        return ScopeDecision(in_scope=True, reason="category", family=_family(text, category, c))
    return ScopeDecision(in_scope=False, reason="not_tech")


def classify_geo(location: Location, remote_type: str) -> ScopeDecision:
    """Geography check (D-06). In-scope results carry reason ``tech`` as a neutral value:
    ``classify_scope`` replaces it with the tech reason."""
    if remote_type == "remote":
        return ScopeDecision(in_scope=True, reason="tech")
    country = location.country_code
    if country is None:
        return ScopeDecision(in_scope=True, reason="geo_unknown")
    if country == "IN":
        return ScopeDecision(in_scope=True, reason="tech")
    return ScopeDecision(in_scope=False, reason="geo_out_of_scope")


def classify_scope(title: str, category: str | None, location: Location) -> ScopeDecision:
    tech = classify_tech(title, category)
    if not tech.in_scope:
        return tech
    geo = classify_geo(location, location.remote_type)
    if not geo.in_scope:
        return ScopeDecision(in_scope=False, reason=geo.reason, family=tech.family)
    if geo.reason == "geo_unknown":
        return ScopeDecision(in_scope=True, reason="geo_unknown", family=tech.family)
    return tech
