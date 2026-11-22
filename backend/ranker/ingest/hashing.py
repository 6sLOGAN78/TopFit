"""Content hash and dedupe key.

``content_hash`` covers SOURCE-LEVEL fields only (deviation from SPEC 7.3 step 6, which
hashes the cleaned description): a normaliser or sanitiser upgrade must not change every
hash and re-embed the whole index. ``NORMALIZER_VERSION`` forces intentional re-runs.
"""

import hashlib

from ranker.ingest.sources.base import RawJob

_SEP = "\x1f"
# Seniority words and level numerals are dropped from the key's title part; the seniority
# value itself stays in the key so "Senior X" and "X" never merge.
_LEVEL_WORDS = frozenset(
    {
        "senior", "sr", "junior", "jr", "lead", "principal", "staff", "intern", "internship",
        "trainee", "i", "ii", "iii", "iv", "1", "2", "3", "4",
    }
)  # fmt: skip


def content_hash(raw: RawJob) -> str:
    parts = [
        raw.title,
        raw.company_name,
        raw.description_raw,
        raw.location_text or "",
        raw.salary_text or "",
        "" if raw.salary_min is None else repr(raw.salary_min),
        "" if raw.salary_max is None else repr(raw.salary_max),
    ]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def _title_tokens(title: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for ch in title[:300].lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return [t for t in tokens if t not in _LEVEL_WORDS]


def dedupe_key(company_normalized: str, title: str, seniority: str, city_or_remote: str) -> str:
    title_part = " ".join(_title_tokens(title))
    raw = "|".join([company_normalized, title_part, seniority, city_or_remote.lower()])
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()
