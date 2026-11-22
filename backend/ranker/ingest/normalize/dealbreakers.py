"""D-19 dealbreaker fields: visa sponsorship, relocation, industry, company stage and size.

Rule-derived only; anything not stated is ``unknown`` (no LLM fallback in this phase).
Phase 4 may set a job aside only on a known value, never on ``unknown``.
"""

import re
from typing import Literal

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
