# ruff: noqa: RUF001
"""D-19 dealbreaker fields: visa sponsorship, relocation, industry, company stage and size.

Rule-derived only; anything not stated is ``unknown`` (no LLM fallback in this phase).
Phase 4 may set a job aside only on a known value, never on ``unknown``.
"""

import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from ranker.config_loader import load_config

Industry = Literal[
    "fintech",
    "edtech",
    "healthtech",
    "ecommerce",
    "saas",
    "devtools",
    "ai_ml",
    "gaming",
    "media",
    "logistics",
    "mobility",
    "crypto",
    "hrtech",
    "adtech",
    "cybersecurity",
    "consumer_internet",
    "it_services",
    "other",
    "unknown",
]
CompanyStage = Literal["early", "growth", "late", "public", "unknown"]
CompanySize = Literal["1-10", "11-50", "51-200", "201-1000", "1001-5000", "5001+", "unknown"]
VisaSponsorship = Literal["offered", "not_offered", "unknown"]
Relocation = Literal["offered", "not_offered", "unknown"]

MAX_SCAN = 6000

_FLAGS = re.IGNORECASE

_VISA_NEGATIVE = [
    re.compile(p, _FLAGS)
    for p in (
        r"\b(?:unable|not able|cannot|can't|can not)\s+to\s+(?:provide\s+)?sponsor(?:ship)?\b",
        r"\b(?:unable|not able)\s+to\s+(?:provide\s+)?(?:visa\s+)?sponsorship\b",
        r"\b(?:cannot|can't|can not|will not|won't|do not|does not|don't|doesn't)\s+sponsor\b",
        r"\bno\s+(?:visa\s+|work\s+permit\s+|h-?1b\s+)?sponsorship\b",
        r"\bsponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)\b",
        r"\bwithout\s+(?:the\s+need\s+for\s+)?(?:visa\s+)?sponsorship\b",
        r"\bnot\s+(?:able\s+to\s+)?(?:offer|provide)\s+(?:visa\s+)?sponsorship\b",
    )
]
_VISA_POSITIVE = [
    re.compile(p, _FLAGS)
    for p in (
        r"\bvisa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered|supported)\b",
        r"\bwe\s+(?:do\s+)?sponsor\s+(?:work\s+)?visas?\b",
        r"\bwe\s+(?:can|will)\s+sponsor\b",
        r"\bh-?1b\s+(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:provided|available|offered)\b",
        r"\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b",
        r"\bsponsor(?:s|ing)?\s+(?:work\s+)?visas?\b",
        r"\bvisa\s+support\b",
    )
]
_RELOC_NEGATIVE = [
    re.compile(p, _FLAGS)
    for p in (
        r"\bno\s+relocation\b",
        r"\brelocation\s+(?:assistance\s+|support\s+)?(?:is\s+)?not\s+"
        r"(?:provided|available|offered|supported)\b",
        r"\b(?:(?:do|does|will|can)\s+not|cannot|won't|can't|don't|doesn't)\s+"
        r"(?:offer|provide)\s+relocation\b",
        r"\bcandidates\s+must\s+(?:already\s+)?(?:be\s+)?(?:located|based|reside|live)\s+in\b",
        r"\bmust\s+already\s+(?:be\s+)?(?:located|based|reside|live)\b",
    )
]
_RELOC_POSITIVE = [
    re.compile(p, _FLAGS)
    for p in (
        r"\brelocation\s+(?:assistance|support|package|bonus|allowance|benefits?)\b",
        r"\bwe\s+(?:will|can)\s+relocate\b",
        r"\brelocation\s+(?:is\s+)?(?:provided|offered|available|covered|supported)\b",
        r"\b(?:will|can)\s+relocate\s+you\b",
        r"\bprovide[sd]?\s+relocation\b",
    )
]


def _scan(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def detect_visa_sponsorship(description: str | None) -> VisaSponsorship:
    if not description:
        return "unknown"
    text = description[:MAX_SCAN]
    if _scan(_VISA_NEGATIVE, text):
        return "not_offered"
    if _scan(_VISA_POSITIVE, text):
        return "offered"
    return "unknown"


def detect_relocation(description: str | None) -> Relocation:
    if not description:
        return "unknown"
    text = description[:MAX_SCAN]
    if _scan(_RELOC_NEGATIVE, text):
        return "not_offered"
    if _scan(_RELOC_POSITIVE, text):
        return "offered"
    return "unknown"


# --- Industry, company stage and size (D-19) -------------------------------------------------

_SIZE_BUCKETS: tuple[tuple[int, CompanySize], ...] = (
    (5001, "5001+"),
    (1001, "1001-5000"),
    (201, "201-1000"),
    (51, "51-200"),
    (11, "11-50"),
    (1, "1-10"),
)
_SELF_DESCRIBE_SCAN = 1500
_STAGE_SERIES_RE = re.compile(r"\bseries\s+([a-z])\b", _FLAGS)
_SIZE_RANGE_RE = re.compile(r"(\d+(?:,\d+)*)\s*(?:-|–|to)\s*(\d+(?:,\d+)*)", _FLAGS)
_SIZE_NUM_RE = re.compile(r"(\d+(?:,\d+)*)", _FLAGS)
_MAX_HINT = 100


class CompanyProfile(BaseModel):
    industry: Industry = "unknown"
    stage: CompanyStage = "unknown"
    size: CompanySize = "unknown"


class _CompanyEntry(BaseModel):
    normalized_name: str
    industry: Industry = "unknown"
    stage: CompanyStage = "unknown"
    size: CompanySize = "unknown"


class IndustriesConfig(BaseModel):
    companies: list[_CompanyEntry]
    hints: dict[str, Industry]
    phrases: dict[Industry, list[str]]


def _config() -> IndustriesConfig:
    return load_config("industries.yaml", IndustriesConfig)


def _company_map() -> dict[str, CompanyProfile]:
    return {
        c.normalized_name: CompanyProfile(industry=c.industry, stage=c.stage, size=c.size)
        for c in _config().companies
    }


def company_profile(company_normalized: str) -> CompanyProfile:
    return _company_map().get(company_normalized, CompanyProfile())


@lru_cache(maxsize=4)
def _phrase_patterns(cfg_id: int) -> tuple[tuple[Industry, re.Pattern[str]], ...]:
    del cfg_id  # cache key only: the config object is itself cached by load_config
    out: list[tuple[Industry, re.Pattern[str]]] = []
    for industry, phrases in _config().phrases.items():
        if not phrases:
            continue
        alt = "|".join(re.escape(p) for p in phrases)
        out.append(
            (
                industry,
                re.compile(
                    rf"\b(?:we\s+are|we're|is|are)\s+(?:a|an)\s+(?:[\w-]+\s+){{0,2}}?(?:{alt})"
                    r"\s+(?:company|startup|platform|business|firm|unicorn|provider)\b",
                    _FLAGS,
                ),
            )
        )
    return tuple(out)


def infer_industry(
    *, company_normalized: str, description: str | None, hint: str | None
) -> Industry:
    cfg = _config()
    hinted: Industry = "unknown"
    if hint:
        hinted = cfg.hints.get(hint.strip().lower()[:_MAX_HINT], "unknown")
        if hinted not in ("unknown", "other"):
            return hinted
    mapped = company_profile(company_normalized).industry
    if mapped != "unknown":
        return mapped
    if description:
        text = description[:_SELF_DESCRIBE_SCAN]
        for industry, pattern in _phrase_patterns(id(cfg)):
            if pattern.search(text):
                return industry
    return hinted


def normalize_company_stage(hint: str | None) -> CompanyStage:
    if not hint:
        return "unknown"
    h = hint.strip().lower()[:_MAX_HINT]
    if re.search(r"\b(?:public|nasdaq|nyse|nse|bse|ipo'?d|listed)\b", h):
        return "public"
    if re.search(r"\b(?:seed|pre-seed|angel|early)\b", h):
        return "early"
    series = _STAGE_SERIES_RE.search(h)
    if series:
        letter = series.group(1)
        if letter == "a":
            return "early"
        if letter in ("b", "c"):
            return "growth"
        return "late"
    if re.search(r"\b(?:growth|scale-?up)\b", h):
        return "growth"
    if re.search(r"\b(?:late|pre-ipo|mature)\b", h):
        return "late"
    return "unknown"


def _bucket(lower: int) -> CompanySize:
    for floor, label in _SIZE_BUCKETS:
        if lower >= floor:
            return label
    return "unknown"


def normalize_company_size(hint: str | None) -> CompanySize:
    if not hint:
        return "unknown"
    h = hint.strip()[:_MAX_HINT]
    m = _SIZE_RANGE_RE.search(h) or _SIZE_NUM_RE.search(h)
    if not m:
        return "unknown"
    try:
        lower = int(m.group(1).replace(",", ""))
    except ValueError:
        return "unknown"
    return _bucket(lower)


def resolve_company_stage(*, hint: str | None, company_normalized: str) -> CompanyStage:
    """Source hint > industries.yaml map > unknown."""
    from_hint = normalize_company_stage(hint)
    if from_hint != "unknown":
        return from_hint
    return company_profile(company_normalized).stage


def resolve_company_size(*, hint: str | None, company_normalized: str) -> CompanySize:
    """Source hint > industries.yaml map > unknown."""
    from_hint = normalize_company_size(hint)
    if from_hint != "unknown":
        return from_hint
    return company_profile(company_normalized).size
