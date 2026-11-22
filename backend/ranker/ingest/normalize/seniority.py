# ruff: noqa: RUF001
"""Rule-based seniority and employment-type inference (NORM-05, rules half).

Undecided values stay ``unknown`` so the LLM fallback (plan 02-12) can fill them.
Regexes are word-bounded and linear; the description scan is capped (T-02-18).
"""

import re
from typing import Literal

Seniority = Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "unknown"]
Source = Literal["title", "years", "hint", "none"]

MAX_DESCRIPTION_SCAN = 4000
MAX_TITLE = 300

# Title rules, checked in this order: intern, lead, senior, mid, junior.
_NUMERAL = r"(?:iii|ii|i|3|2|1)"
_ROLE = r"(?:sde|sdet|swe|engineer|developer|programmer|analyst|scientist|architect|designer)"
_INTERN_RE = re.compile(r"\b(?:intern(?:ship)?s?|trainee|apprentice)\b", re.IGNORECASE)
_LEAD_RE = re.compile(
    r"\b(?:staff|principal|lead|head\s+of|engineering\s+manager|manager|director|vp|"
    r"vice\s+president|architect)\b",
    re.IGNORECASE,
)
_SENIOR_RE = re.compile(r"\b(?:senior|sr)\b\.?", re.IGNORECASE)
_LEVEL_RE = re.compile(rf"\b{_ROLE}\s*[-\s]?\s*(?P<n>{_NUMERAL})\b", re.IGNORECASE)
_JUNIOR_RE = re.compile(
    r"\b(?:junior|jr|fresher|freshers|associate|entry[-\s]level|graduate)\b", re.IGNORECASE
)

_LEVEL_MAP: dict[str, Seniority] = {
    "i": "junior",
    "1": "junior",
    "ii": "mid",
    "2": "mid",
    "iii": "senior",
    "3": "senior",
}

# Years of experience: "3+ years of experience", "5-7 years exp", "Experience: 3 years".
_YEARS_A_RE = re.compile(
    r"(?P<lo>\d{1,2})\s*(?:(?:-|–|to)\s*(?P<hi>\d{1,2}))?\s*\+?\s*(?:years?|yrs?)"
    r"(?:\s+of)?(?:\s+\w+){0,3}?\s+(?:experience|exp)\b",
    re.IGNORECASE,
)
_YEARS_B_RE = re.compile(
    r"\bexperience\b\W{0,4}(?:of\s+)?(?P<lo>\d{1,2})\s*(?:(?:-|–|to)\s*(?P<hi>\d{1,2}))?"
    r"\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)


def _seniority_from_years(minimum: int) -> Seniority:
    if minimum < 2:
        return "junior"
    if minimum < 5:
        return "mid"
    if minimum < 10:
        return "senior"
    return "lead"


def infer_seniority(title: str, description: str | None) -> tuple[Seniority, str]:
    t = title[:MAX_TITLE]
    if _INTERN_RE.search(t):
        return "intern", "title"
    if _LEAD_RE.search(t):
        return "lead", "title"
    if _SENIOR_RE.search(t):
        return "senior", "title"
    level = _LEVEL_RE.search(t)
    if level:
        return _LEVEL_MAP[level.group("n").lower()], "title"
    if _JUNIOR_RE.search(t):
        return "junior", "title"
    if description:
        text = description[:MAX_DESCRIPTION_SCAN]
        m = _YEARS_A_RE.search(text) or _YEARS_B_RE.search(text)
        if m:
            return _seniority_from_years(int(m.group("lo"))), "years"
    return "unknown", "none"


def _employment_from_hint(hint: str) -> EmploymentType | None:
    h = hint.lower()
    if "intern" in h:
        return "internship"
    if "part" in h:
        return "part_time"
    if "contract" in h or "freelance" in h or "temp" in h:
        return "contract"
    if "full" in h:
        return "full_time"
    return None


def infer_employment_type(
    *, hint: str | None, contract_type: str | None, title: str
) -> tuple[EmploymentType, str]:
    # Adzuna contract_type "contract" wins over contract_time "full_time".
    if contract_type and "contract" in contract_type.lower():
        return "contract", "hint"
    if hint:
        value = _employment_from_hint(hint[:100])
        if value is not None:
            return value, "hint"
    if _INTERN_RE.search(title[:MAX_TITLE]):
        return "internship", "title"
    return "unknown", "none"
