"""Location normalisation (NORM-02) and on-site days per week (D-19).

Pure functions over ``config/cities.yaml``. Matching is token based: the text is split into
segments on ``, / | ; ( ) -`` and each segment is scanned left to right for the longest
alias (3, 2, then 1 words).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ranker.config_loader import load_config

RemoteType = Literal["onsite", "hybrid", "remote", "unknown"]

_SEGMENT_SPLIT = re.compile(r"[,/|;()\-–—]+")  # noqa: RUF001
_DOTS = re.compile(r"[.’']")  # noqa: RUF001
_MAX_NGRAM = 3


class CityEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    canonical: str
    aliases: list[str] = []
    state: str | None = None
    country: str = "IN"
    metro: str | None = None


class CitiesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    cities: list[CityEntry]
    multi: dict[str, list[str]] = {}
    country_any: list[str] = []
    countries: dict[str, str] = {}


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)
    city: str | None
    cities: list[str]
    country_code: str | None
    remote_type: RemoteType


@dataclass(frozen=True)
class _Index:
    cfg: CitiesConfig
    city: dict[tuple[str, ...], str]
    multi: dict[tuple[str, ...], list[str]]
    country_any: set[tuple[str, ...]]
    countries: dict[tuple[str, ...], str]
    max_n: int


_index_cache: _Index | None = None


def _words(text: str) -> list[list[str]]:
    """Lowercase text -> list of segments, each a list of words."""
    cleaned = _DOTS.sub("", text.lower())
    segments: list[list[str]] = []
    for seg in _SEGMENT_SPLIT.split(cleaned):
        words = seg.split()
        if words:
            segments.append(words)
    return segments


def _key(alias: str) -> tuple[str, ...]:
    return tuple(w for seg in _words(alias) for w in seg)


def _index() -> _Index:
    global _index_cache
    cfg = load_config("cities.yaml", CitiesConfig)
    if _index_cache is not None and _index_cache.cfg is cfg:
        return _index_cache
    city: dict[tuple[str, ...], str] = {}
    for entry in cfg.cities:
        for alias in [entry.canonical, *entry.aliases]:
            city[_key(alias)] = entry.canonical
    multi = {_key(k): v for k, v in cfg.multi.items()}
    country_any = {_key(p) for p in cfg.country_any}
    countries = {_key(k): v for k, v in cfg.countries.items()}
    longest = max(len(k) for d in (city, multi, countries) for k in d)
    longest = max(longest, max((len(k) for k in country_any), default=1))
    _index_cache = _Index(cfg, city, multi, country_any, countries, min(longest, _MAX_NGRAM))
    return _index_cache


def indian_city_names() -> list[str]:
    """Every canonical city plus alias, lowercase (used by the region rules)."""
    cfg = load_config("cities.yaml", CitiesConfig)
    names: set[str] = set()
    for entry in cfg.cities:
        names.add(entry.canonical.lower())
        names.update(a.lower() for a in entry.aliases)
    return sorted(names)


@dataclass
class _Matches:
    cities: list[str]
    country_any: bool
    country: str | None


def _scan(texts: Sequence[str], idx: _Index) -> _Matches:
    cities: list[str] = []
    country_any = False
    country: str | None = None
    for text in texts:
        for words in _words(text):
            i = 0
            while i < len(words):
                matched = 0
                for n in range(min(idx.max_n, len(words) - i), 0, -1):
                    gram = tuple(words[i : i + n])
                    if gram in idx.multi:
                        cities.extend(idx.multi[gram])
                    elif gram in idx.city:
                        cities.append(idx.city[gram])
                    elif gram in idx.country_any:
                        country_any = True
                    elif gram in idx.countries:
                        country = country or idx.countries[gram]
                    else:
                        continue
                    matched = n
                    break
                i += matched or 1
    unique: list[str] = []
    for c in cities:
        if c not in unique:
            unique.append(c)
    return _Matches(unique, country_any, country)


_HYBRID_RE = re.compile(r"\bhybrid\b")
_REMOTE_RE = re.compile(r"\b(?:remote|wfh|work from home|work-from-home)\b")


def _remote_type(
    hint: str | None, text: str | None, description: str | None, found: bool
) -> RemoteType:
    h = (hint or "").strip().lower().replace("-", "").replace(" ", "")
    if h == "remote":
        return "remote"
    if h == "hybrid":
        return "hybrid"
    if h == "onsite":
        return "onsite"
    blob = f"{text or ''} {(description or '')[:2000]}".lower()
    if _HYBRID_RE.search(blob):
        return "hybrid"
    if _REMOTE_RE.search(blob):
        return "remote"
    return "onsite" if found else "unknown"


def _country_from_hint(country_hint: str | None, idx: _Index) -> str | None:
    if not country_hint:
        return None
    hint = country_hint.strip()
    if len(hint) == 2 and hint.isalpha():
        return hint.upper()
    key = _key(hint)
    if key in idx.country_any:
        return "IN"
    return idx.countries.get(key)


def normalize_location(
    location_text: str | None,
    *,
    parts: Sequence[str] = (),
    workplace_hint: str | None = None,
    country_hint: str | None = None,
    description: str | None = None,
) -> Location:
    idx = _index()
    # Adzuna area lists put the most specific entry last, so scan them in reverse.
    texts = [t for t in [location_text, *reversed(parts)] if t]
    m = _scan(texts, idx)
    country: str | None
    if m.cities or m.country_any:
        country = "IN"
    else:
        country = m.country or _country_from_hint(country_hint, idx)
    found = bool(m.cities) or country is not None
    return Location(
        city=m.cities[0] if m.cities else None,
        cities=m.cities,
        country_code=country,
        remote_type=_remote_type(workplace_hint, location_text, description, found),
    )


# --- on-site days per week (D-19) -----------------------------------------------------------

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_NUM = r"(\d{1,2}|one|two|three|four|five|six|seven)"
_RANGE = _NUM + r"(?:\s?(?:-|–|—|to)\s?" + _NUM + r")?"  # noqa: RUF001
_OFFICE = r"(?:wfo|(?:work(?:ing)?\sfrom\s|in\s|at\s)(?:the\s|our\s)?office|in-office)"
_DAY_PATTERNS = [
    re.compile(
        _RANGE
        + r"\sdays?\s(?:a|per|each|/)\s?week\s(?:in|at|from)\s(?:the\s|our\s)?(?:office|work)"
    ),
    re.compile(_RANGE + r"\sdays?\s" + _OFFICE),
    re.compile(r"work(?:ing)?\sfrom\soffice\s(?:for\s)?" + _RANGE + r"\sdays?"),
    re.compile(r"in-?\s?office\s" + _RANGE + r"\s?(?:x|times|days?)"),
    re.compile(_RANGE + r"\sdays?\sworking"),
]
_WS = re.compile(r"\s+")
_SCAN_CHARS = 4000


def _to_int(token: str | None) -> int | None:
    if token is None:
        return None
    value = int(token) if token.isdigit() else _NUM_WORDS.get(token)
    if value is None or not 0 <= value <= 7:
        return None
    return value


def detect_onsite_days(description: str | None, remote_type: str) -> int | None:
    """On-site days per week; 0 for remote, None when the text does not say."""
    if remote_type == "remote":
        return 0
    if not description:
        return None
    text = _WS.sub(" ", description[:_SCAN_CHARS].lower())
    best: tuple[int, int] | None = None
    for pattern in _DAY_PATTERNS:
        for match in pattern.finditer(text):
            first = _to_int(match.group(1))
            if first is None:
                continue
            second = _to_int(match.group(2))
            value = min(first, second) if second is not None else first
            if best is None or match.start() < best[0]:
                best = (match.start(), value)
            break
    return best[1] if best else None
