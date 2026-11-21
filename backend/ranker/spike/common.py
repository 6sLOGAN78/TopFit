"""Shared helpers for the ING-00 spike modules."""

import logging
import re
from typing import Any, cast

USER_AGENT = "TopFit-jobs-indexer/1.0"
BOT_UA = "RankerBot/0.1 (+https://topfit.duckdns.org; portfolio project)"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

TECH_RE = re.compile(
    r"engineer|developer|sde|devops|sre|data|\bml\b|machine learning|\bqa\b|sdet|security"
    r"|android|ios|backend|frontend|full[ -]?stack",
    re.I,
)
NON_TECH_RE = re.compile(r"product manager|designer|sales|recruiter|support|marketing", re.I)
INDIA_RE = re.compile(
    r"india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai"
    r"|kolkata|remote|worldwide|anywhere|apac",
    re.I,
)


def exc_tag(exc: BaseException) -> str:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return f"{type(exc).__name__}:{status}"


def quiet_http_loggers() -> None:
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def in_scope(title: str) -> bool:
    return bool(TECH_RE.search(title)) and not NON_TECH_RE.search(title)


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast("dict[str, Any]", x) for x in cast("list[Any]", value) if isinstance(x, dict)]


def as_dict(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
