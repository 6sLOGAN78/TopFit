"""Rules-first query parser (SRCH-05, SRCH-06, D-05).

Turns free text into editable hard-filter chips plus the text the retrieval legs rank on.
Only clear signals become filters: city, minimum salary, work mode and seniority. Skills and
role words never do. The parser reuses the Phase 2 normalisers (city aliases, salary text,
seniority) so a query is read the same way a job posting is.

``parse_query`` is the entry point the search service calls. The model fallback lives behind
``_model_fallback``; this plan returns ``None`` from it so search is rules-only.
"""

import re
from dataclasses import dataclass, replace
from functools import cache, lru_cache

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import load_config
from ranker.ingest.normalize.location import indian_city_names, normalize_location
from ranker.ingest.normalize.salary import parse_salary_text
from ranker.ingest.normalize.seniority import infer_seniority
from ranker.ingest.skills import SkillMatcher, default_matcher
from ranker.llm.client import LLMGateway
from ranker.llm.schemas import SALARY_BUCKETS_LPA
from ranker.search.models import (
    Chip,
    ChipSource,
    ParseModel,
    ParseSource,
    SearchFilters,
    WorkMode,
)
from ranker.settings import Settings

PURPOSE = "query_parse"
PROMPT_VERSION = "qparse-v1"
LOCATION_CACHE_SIZE = 4096

_EDGE_PUNCT = ",;:()[]\"'!?"
_SYMBOL_CHARS = re.compile(r"[+#.]")
_NUM_UNIT = re.compile(r"₹?(\d+(?:\.\d+)?(?:[-–]\d+(?:\.\d+)?)?)\+?([a-z]*)")  # noqa: RUF001
_SALARY_UNITS = frozenset({"lpa", "lakh", "lakhs", "lac", "lacs", "cr", "crore", "crores"})
_GLUED_ONLY_UNITS = frozenset({"l"})
_REMOTE_WORDS = frozenset({"remote", "wfh", "work-from-home"})
_HYBRID_WORDS = frozenset({"hybrid"})
_ONSITE_WORDS = frozenset({"onsite", "on-site", "office"})
_SOFT_REMOTE = frozenset({"ok", "okay", "fine", "also", "preferred", "welcome", "friendly"})
_SENIORITY_WORDS = frozenset(
    {
        "senior",
        "sr",
        "sr.",
        "junior",
        "jr",
        "jr.",
        "fresher",
        "freshers",
        "intern",
        "interns",
        "internship",
        "trainee",
        "staff",
        "principal",
        "lead",
        "graduate",
    }
)
_SENIORITY_LABELS = {
    "intern": "Intern",
    "junior": "Junior",
    "mid": "Mid",
    "senior": "Senior",
    "lead": "Lead",
}
_WORK_MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}
_MAX_TOKENS = 80


class _Synonym(BaseModel):
    canonical: str
    aliases: list[str]


class QuerySynonyms(BaseModel):
    synonyms: list[_Synonym]
    stopwords: list[str]
    role_words: list[str]


@dataclass(frozen=True)
class QueryParse:
    lex_text: str
    embed_text: str
    skills: list[str]
    filters: SearchFilters
    chips: list[Chip]
    residue: list[str]
    parse_source: ParseSource = "rules"
    cached: bool = False
    degraded: bool = False
    prompt_version: str = PROMPT_VERSION


@dataclass(frozen=True)
class _Vocab:
    synonyms: list[tuple[re.Pattern[str], str]]
    stopwords: frozenset[str]
    role_words: frozenset[str]


@cache
def _vocab() -> _Vocab:
    cfg = load_config("query_synonyms.yaml", QuerySynonyms)
    pairs: list[tuple[re.Pattern[str], str]] = []
    for entry in cfg.synonyms:
        for alias in sorted(entry.aliases, key=len, reverse=True):
            pairs.append(
                (
                    re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", re.IGNORECASE),
                    entry.canonical,
                )
            )
    return _Vocab(pairs, frozenset(cfg.stopwords), frozenset(cfg.role_words))


def _expand(q: str) -> str:
    out = q
    for pattern, canonical in _vocab().synonyms:
        out = pattern.sub(canonical, out)
    return out


def _bare(token: str) -> str:
    return token.strip(_EDGE_PUNCT)


@lru_cache(maxsize=LOCATION_CACHE_SIZE)  # keyed by user text, so bounded
def _location(gram: str) -> tuple[tuple[str, ...], bool]:
    loc = normalize_location(gram)
    return tuple(loc.cities), loc.country_code is not None


def _snap_lpa(lpa: float) -> int | None:
    best: int | None = None
    for bucket in SALARY_BUCKETS_LPA:
        if bucket <= lpa:
            best = bucket
    return best


def _find_salary(words: list[str], consumed: set[int]) -> int | None:
    """Minimum salary bucket in LPA from the first "<number> <unit>" in the query."""
    for i, word in enumerate(words):
        if i in consumed:
            continue
        m = _NUM_UNIT.fullmatch(word.lower())
        if m is None:
            continue
        num, unit = m.group(1), m.group(2)
        used = {i}
        if unit:
            if unit not in _SALARY_UNITS and unit not in _GLUED_ONLY_UNITS:
                continue
        else:
            nxt = words[i + 1].lower().rstrip("+") if i + 1 < len(words) else ""
            if nxt not in _SALARY_UNITS:
                continue
            unit = nxt
            used.add(i + 1)
        consumed |= used
        parsed = parse_salary_text(f"{num} {unit}", default_currency="INR")
        if parsed is None or parsed.min_inr_annual is None or parsed.out_of_bounds:
            return None
        return _snap_lpa(float(parsed.min_inr_annual) / 100_000)
    return None


def _find_work_modes(words: list[str], consumed: set[int]) -> tuple[list[WorkMode], bool]:
    """Work-mode words; the bool says "remote" was named (a city turns it into remote_ok)."""
    modes: list[WorkMode] = []
    remote = False
    for i, word in enumerate(words):
        if i in consumed:
            continue
        low = _bare(word.lower())
        if low == "work" and [w.lower() for w in words[i + 1 : i + 3]] == ["from", "home"]:
            consumed.update({i, i + 1, i + 2})
            remote = True
            continue
        if low in _REMOTE_WORDS:
            remote = True
            consumed.add(i)
            if i + 1 < len(words) and _bare(words[i + 1].lower()) in _SOFT_REMOTE:
                consumed.add(i + 1)
        elif low in _HYBRID_WORDS:
            consumed.add(i)
            if "hybrid" not in modes:
                modes.append("hybrid")
        elif low in _ONSITE_WORDS:
            consumed.add(i)
            if "onsite" not in modes:
                modes.append("onsite")
    return modes, remote


@cache
def _city_names() -> frozenset[str]:
    return frozenset(indian_city_names())


def _is_place(words: list[str]) -> bool:
    """True when ``words`` read as one city or region name, not a phrase containing one."""
    gram = " ".join(words).replace(".", "")
    if gram in _city_names():
        return True
    found, country = _location(gram)
    if not (found or country):
        return False
    if len(words) == 1:
        return True
    # Multi-word: accept only a minimal span (neither shorter span is itself a place).
    return not any(
        _location(" ".join(sub))[0] or _location(" ".join(sub))[1]
        for sub in (words[:-1], words[1:])
    )


def _find_cities(words: list[str], consumed: set[int]) -> list[str]:
    cities: list[str] = []
    i = 0
    while i < len(words):
        if i in consumed:
            i += 1
            continue
        matched = 0
        for n in range(min(3, len(words) - i), 0, -1):
            span = range(i, i + n)
            if any(j in consumed for j in span):
                continue
            gram_words = [_bare(w.lower()) for w in words[i : i + n]]
            if not _is_place(gram_words):
                continue
            for c in _location(" ".join(gram_words))[0]:
                if c not in cities:
                    cities.append(c)
            consumed.update(span)
            matched = n
            break
        i += matched or 1
    return cities


def lex_text_for(q: str, matcher: SkillMatcher | None = None) -> str:
    """Query text for the english leg with symbol skills removed.

    ``C++`` would otherwise tokenise to ``c`` and OR-match every C job; those skills are
    matched through the simple-config skills leg instead.
    """
    m = matcher or default_matcher()
    kept: list[str] = []
    for word in q.split():
        bare = _bare(word)
        if bare and _SYMBOL_CHARS.search(bare) and m.extract(bare):
            continue
        kept.append(word)
    return " ".join(kept)


def _chips(filters: SearchFilters, sources: dict[str, ChipSource]) -> list[Chip]:
    chips: list[Chip] = []
    if filters.cities:
        label = "City: " + ", ".join(filters.cities)
        if filters.remote_ok:
            label += " + remote"
        chips.append(
            Chip(
                field="city",
                value=",".join(filters.cities),
                label=label,
                source=sources.get("city", "rules"),
            )
        )
    if filters.min_salary_lpa is not None:
        label = f"₹{filters.min_salary_lpa} LPA+"
        if filters.exclude_estimates:
            label += " · no estimates"
        chips.append(
            Chip(
                field="min_salary",
                value=str(filters.min_salary_lpa),
                label=label,
                source=sources.get("min_salary", "rules"),
            )
        )
    if filters.work_modes:
        names = [_WORK_MODE_LABELS[m] for m in filters.work_modes]
        chips.append(
            Chip(
                field="work_mode",
                value=",".join(filters.work_modes),
                label=_join_or(names),
                source=sources.get("work_mode", "rules"),
            )
        )
    if filters.seniority:
        names = [_SENIORITY_LABELS[s] for s in filters.seniority]
        chips.append(
            Chip(
                field="seniority",
                value=",".join(filters.seniority),
                label=_join_or(names),
                source=sources.get("seniority", "rules"),
            )
        )
    return chips


def _join_or(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1].lower()


def _has_filters(f: SearchFilters) -> bool:
    return bool(
        f.cities
        or f.work_modes
        or f.seniority
        or f.min_salary_lpa is not None
        or f.exclude_estimates
    )


def parse_rules(q: str, *, detect_filters: bool = True) -> QueryParse:
    """Read ``q`` by rules alone. ``detect_filters=False`` keeps every word as ranking text."""
    vocab = _vocab()
    matcher = default_matcher()
    expanded = _expand(q)
    words = expanded.split()[:_MAX_TOKENS]
    skills = matcher.extract_query(expanded)

    filter_idx: set[int] = set()  # city, salary, work-mode and region words
    filters = SearchFilters()
    seniority_idx: set[int] = set()
    if detect_filters:
        min_lpa = _find_salary(words, filter_idx)
        modes, remote = _find_work_modes(words, filter_idx)
        cities = _find_cities(words, filter_idx)
        remote_ok = remote and bool(cities)
        if remote and not cities:
            modes.insert(0, "remote")
        level, _ = infer_seniority(q, None)
        seniority = [level] if level in _SENIORITY_LABELS else []
        if seniority:
            seniority_idx = {i for i, w in enumerate(words) if _bare(w.lower()) in _SENIORITY_WORDS}
        filters = SearchFilters(
            cities=cities,
            remote_ok=remote_ok,
            work_modes=modes,
            seniority=seniority,  # pyright: ignore[reportArgumentType]
            min_salary_lpa=min_lpa,
        )

    skill_words: set[str] = set()
    for canonical in skills:
        skill_words.update(re.split(r"[-\s]", canonical.lower()))

    lex: list[str] = []
    embed: list[str] = []
    residue: list[str] = []
    for i, word in enumerate(words):
        low = _bare(word.lower())
        if i in filter_idx or not low or low in vocab.stopwords:
            continue
        embed.append(word)
        if i in seniority_idx:
            continue
        word_skills = matcher.extract_query(_bare(word))
        ambiguous_skill = matcher.query_ok_canonical(low) in skills
        symbol_skill = bool(_SYMBOL_CHARS.search(low)) and bool(matcher.extract(_bare(word)))
        if not ambiguous_skill and not symbol_skill:
            lex.append(word)
        if (
            not word_skills
            and low not in vocab.role_words
            and low not in skill_words
            and not low.isdigit()
            and len(low) > 1
        ):
            residue.append(low)
    return QueryParse(
        lex_text=" ".join(lex),
        embed_text=" ".join(embed),
        skills=skills,
        filters=filters,
        chips=_chips(filters, {}),
        residue=residue,
    )


def _merge(
    parsed: SearchFilters, explicit: SearchFilters
) -> tuple[SearchFilters, dict[str, ChipSource]]:
    """Explicit fields override parsed ones per filter group; parsed groups fill the rest."""
    sources: dict[str, ChipSource] = {}
    cities, remote_ok = parsed.cities, parsed.remote_ok
    if explicit.cities:
        cities, remote_ok = explicit.cities, explicit.remote_ok
        sources["city"] = "user"
    modes = parsed.work_modes
    if explicit.work_modes:
        modes = explicit.work_modes
        sources["work_mode"] = "user"
    seniority = parsed.seniority
    if explicit.seniority:
        seniority = explicit.seniority
        sources["seniority"] = "user"
    min_lpa = parsed.min_salary_lpa
    if explicit.min_salary_lpa is not None or explicit.exclude_estimates:
        if explicit.min_salary_lpa is not None:
            min_lpa = explicit.min_salary_lpa
        sources["min_salary"] = "user"
    merged = SearchFilters(
        cities=cities,
        remote_ok=remote_ok,
        work_modes=modes,
        seniority=seniority,
        min_salary_lpa=min_lpa,
        exclude_estimates=explicit.exclude_estimates,
    )
    return merged, sources


async def _model_fallback(
    parse: QueryParse,
    q: str,
    *,
    engine: AsyncEngine | None,
    gateway: LLMGateway | None,
    parse_model: ParseModel,
    settings: Settings,
) -> QueryParse | None:
    """Plan 03-05 fills this hook (gate, cache, providers). Rules-only until then."""
    del parse, q, engine, gateway, parse_model, settings
    return None


async def parse_query(
    q: str,
    explicit: SearchFilters | None,
    auto_filters: bool,
    *,
    engine: AsyncEngine | None,
    gateway: LLMGateway | None,
    parse_model: ParseModel,
    settings: Settings,
) -> QueryParse:
    """Rules parse of ``q`` merged with explicit filters.

    ``auto_filters=False`` uses only the explicit filters (a removed chip never comes back)
    and leaves the whole query text for ranking.
    """
    if not auto_filters:
        base = parse_rules(q, detect_filters=False)
        filters = explicit or SearchFilters()
        _all_user: dict[str, ChipSource] = dict.fromkeys(
            ("city", "min_salary", "work_mode", "seniority"), "user"
        )
        return replace(base, filters=filters, chips=_chips(filters, _all_user))
    base = parse_rules(q)
    if explicit is not None and _has_filters(explicit):
        filters, sources = _merge(base.filters, explicit)
        base = replace(base, filters=filters, chips=_chips(filters, sources))
    refined = await _model_fallback(
        base, q, engine=engine, gateway=gateway, parse_model=parse_model, settings=settings
    )
    return refined or base


__all__ = [
    "PROMPT_VERSION",
    "PURPOSE",
    "SALARY_BUCKETS_LPA",
    "QueryParse",
    "lex_text_for",
    "parse_query",
    "parse_rules",
]
