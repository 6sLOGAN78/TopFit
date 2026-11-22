"""Company-name normalisation (NORM-07, pure half).

``normalize_company_name`` yields a stable key so the ``companies`` table has one row per
real company. Placeholders ("Confidential", "Stealth Startup", staffing agencies) are
flagged so later dedupe/diversity can ignore them (T-02-20). Linear, length-capped.
"""

import re
import unicodedata

MAX_NAME = 200
UNKNOWN = "unknown"

# Dropped from the end, iteratively, while at least one token remains.
HARD_SUFFIXES: frozenset[str] = frozenset(
    {
        "pvt",
        "private",
        "ltd",
        "limited",
        "llp",
        "llc",
        "inc",
        "corp",
        "corporation",
        "gmbh",
        "co",
        "india",
    }
)
# Dropped from the end only if the remainder is at least this many characters.
SOFT_SUFFIXES: frozenset[str] = frozenset({"technologies", "technology", "labs", "solutions"})
SOFT_MIN_REMAINDER = 3

PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^confidential(?: company)?$",
        r"^company confidential$",
        r"^hiring company$",
        r"^hiring (?:now|organization|organisation)$",
        r"^stealth(?: startup| mode| company)?$",
        r"^a client of\b",
        r"^our client$",
        r"^top mnc$",
        r"^(?:leading|reputed|well known) (?:mnc|company|organization|organisation)$",
        r"^undisclosed$",
        r"^anonymous$",
        r"^not disclosed$",
        r"^unknown$",
    )
)

# Normalised staffing/recruiting agency names (configurable constant).
STAFFING_AGENCIES: frozenset[str] = frozenset(
    {
        "randstad",
        "teamlease",
        "adecco",
        "manpower",
        "manpowergroup",
        "quess",
        "quess corp",
        "michael page",
        "kelly services",
        "hays",
        "naukri",
        "ciel hr",
        "xpheno",
        "talent500",
        "careernet",
        "abc consultants",
        "peoplestrong",
    }
)

_NON_WORD_RE = re.compile(r"[\W_]+")


def _clean(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text[:MAX_NAME]).casefold()
    folded = unicodedata.normalize("NFKC", folded)
    return _NON_WORD_RE.sub(" ", folded).strip()


def _normalize_once(text: str) -> str:
    tokens = _clean(text).split()
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        last = tokens[-1]
        if last in HARD_SUFFIXES:
            tokens.pop()
            changed = True
        elif last in SOFT_SUFFIXES and len(" ".join(tokens[:-1])) >= SOFT_MIN_REMAINDER:
            tokens.pop()
            changed = True
    return " ".join(tokens)


def normalize_company_name(name: str, canonical_hint: str | None = None) -> str:
    base = canonical_hint if canonical_hint and canonical_hint.strip() else name
    current = base
    # Iterate to a fixpoint so normalisation is idempotent (casefold/NFKC edge cases).
    for _ in range(4):
        nxt = _normalize_once(current)
        if nxt == current:
            break
        current = nxt
    return current or UNKNOWN


def is_placeholder_company(name: str) -> bool:
    key = normalize_company_name(name)
    if key == UNKNOWN:
        return True
    cleaned = _clean(name)
    for candidate in (cleaned, key):
        if any(p.search(candidate) for p in PLACEHOLDER_PATTERNS):
            return True
    return key in STAFFING_AGENCIES
