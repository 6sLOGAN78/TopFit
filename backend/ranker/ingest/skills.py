"""Skill extraction (NORM-06): curated taxonomy + symbol-safe, ambiguity-aware matcher.

Pure module: no Settings import. The taxonomy lives in ``config/skills.yaml``.

Matching rules
- Custom boundaries (``\\b`` fails next to ``+``, ``#`` and ``.``), so ``C++``, ``C#``,
  ``.NET`` and ``Node.js`` match and ``JavaScript`` never yields ``Java``.
- Longest alias first inside one alternation (``React Native`` before ``React``).
- Ambiguous aliases (``Go``, ``R``, ``Swift`` ...) only count with a nearby context regex
  or when they sit in a separator-delimited skills list.

Cost bounds (T-02-22): input is capped at 20k chars; separators are located once per text and
looked up with ``bisect``; per-match list-context work uses fixed windows; each canonical is
evaluated at most ``MAX_WEAK_TRIES`` times and skipped once found.
"""

import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ranker.config_loader import load_config

BOUND_L = r"(?<![A-Za-z0-9_+#.])"
BOUND_R = r"(?![A-Za-z0-9_+#])"

MAX_TEXT_CHARS = 20_000
CONTEXT_WINDOW = 40
LIST_WINDOW = 200
MAX_WEAK_TRIES = 5

Category = Literal[
    "language",
    "framework",
    "library",
    "database",
    "cloud",
    "devops",
    "tool",
    "data",
    "ml",
    "platform",
    "concept",
    "testing",
    "security",
    "mobile",
]

# Every alternative is a bounded token: no whitespace-run quantifiers, so matching is linear.
_SEPARATORS = re.compile(r"[,/|•·;:\n]|\b(?:and|or)\b|(?<=\s)&(?=\s)")
_LIST_KEYWORDS = re.compile(
    r"\b(?:skills?|languages?|stack|proficien\w*|technologies|tools|"
    r"experience (?:with|in)|knowledge of)\b",
    re.IGNORECASE,
)


class SkillEntry(BaseModel):
    """One canonical skill.

    ``aliases`` follow the entry's ambiguity flags; ``strong_aliases`` are distinctive
    spellings of an ambiguous skill (e.g. ``Golang``) that always match.
    """

    model_config = {"extra": "forbid"}

    canonical: str
    aliases: list[str] = Field(default_factory=list)
    strong_aliases: list[str] = Field(default_factory=list)
    category: Category
    ambiguous: bool = False
    case_sensitive: bool = False
    context: list[str] = Field(default_factory=list)
    implies: list[str] = Field(default_factory=list)
    token: str | None = None


class SkillsConfig(BaseModel):
    model_config = {"extra": "forbid"}

    provenance: list[str]
    skills: list[SkillEntry]

    @model_validator(mode="after")
    def _check(self) -> SkillsConfig:
        canonicals: set[str] = set()
        for entry in self.skills:
            if entry.canonical in canonicals:
                raise ValueError(f"duplicate canonical {entry.canonical}")
            canonicals.add(entry.canonical)
        seen: dict[tuple[str, bool], str] = {}
        for entry in self.skills:
            for imp in entry.implies:
                if imp not in canonicals:
                    raise ValueError(f"{entry.canonical} implies unknown {imp}")
            for alias, cs in _entry_aliases(entry):
                key = (alias.lower(), cs)
                owner = seen.get(key)
                if owner is not None and owner != entry.canonical:
                    raise ValueError(f"alias {alias!r} maps to {owner} and {entry.canonical}")
                seen[key] = entry.canonical
        return self


def _entry_aliases(entry: SkillEntry) -> list[tuple[str, bool]]:
    """All (alias, case_sensitive) pairs, including implicit canonical spellings."""
    out: list[tuple[str, bool]] = []
    if not entry.ambiguous:
        out.append((entry.canonical, False))
        if "-" in entry.canonical:
            out.append((entry.canonical.replace("-", " "), False))
    out.extend((a, entry.case_sensitive) for a in entry.aliases)
    out.extend((a, False) for a in entry.strong_aliases)
    # de-duplicate while keeping order
    return list(dict.fromkeys(out))


class _Alias:
    __slots__ = ("alias", "canonical", "context", "cs", "weak")

    def __init__(
        self, alias: str, canonical: str, cs: bool, weak: bool, context: list[re.Pattern[str]]
    ) -> None:
        self.alias = alias
        self.canonical = canonical
        self.cs = cs
        self.weak = weak
        self.context = context


def _compile(aliases: list[str], *, cs: bool) -> re.Pattern[str] | None:
    if not aliases:
        return None
    ordered = sorted(set(aliases), key=len, reverse=True)
    body = "|".join(re.escape(a) for a in ordered)
    return re.compile(BOUND_L + "(" + body + ")" + BOUND_R, 0 if cs else re.IGNORECASE)


class SkillMatcher:
    """Compiled once from a ``SkillsConfig``; ``extract`` is pure and bounded."""

    def __init__(self, config: SkillsConfig) -> None:
        self._tokens: dict[str, str] = {}
        self._implies: dict[str, list[str]] = {}
        self._records: dict[tuple[str, bool, bool], _Alias] = {}
        groups: dict[tuple[bool, bool], list[str]] = {}
        for entry in config.skills:
            self._tokens[entry.canonical] = entry.token or _default_token(entry.canonical)
            self._implies[entry.canonical] = list(entry.implies)
            ctx = [re.compile(c) for c in entry.context]
            weak_aliases: set[str] = set(entry.aliases) if entry.ambiguous else set[str]()
            for alias, cs in _entry_aliases(entry):
                weak = alias in weak_aliases and alias not in entry.strong_aliases
                rec = _Alias(alias, entry.canonical, cs, weak, ctx if weak else [])
                self._records[(alias if cs else alias.lower(), cs, weak)] = rec
                groups.setdefault((weak, cs), []).append(alias)
        self._patterns: list[tuple[bool, bool, re.Pattern[str]]] = []
        for (weak, cs), aliases in groups.items():
            pat = _compile(aliases, cs=cs)
            if pat is not None:
                self._patterns.append((weak, cs, pat))

    def token(self, canonical: str) -> str:
        return self._tokens.get(canonical) or _default_token(canonical)

    def extract(self, text: str) -> list[str]:
        text = text[:MAX_TEXT_CHARS]
        raw_strong: list[tuple[int, int, str]] = []
        weak: list[tuple[int, int, _Alias]] = []
        for is_weak, cs, pat in self._patterns:
            for m in pat.finditer(text):
                matched = m.group(1)
                rec = self._records.get((matched if cs else matched.lower(), cs, is_weak))
                if rec is None:
                    continue
                if is_weak:
                    weak.append((m.start(1), m.end(1), rec))
                else:
                    raw_strong.append((m.start(1), m.end(1), rec.canonical))
        strong = _drop_overlaps(raw_strong)
        strong_starts = [s for s, _, _ in strong]
        found: set[str] = {c for _, _, c in strong}
        if weak:
            sep_starts: list[int] = []
            sep_ends: list[int] = []
            for sep in _SEPARATORS.finditer(text):
                sep_starts.append(sep.start())
                sep_ends.append(sep.end())
            tried: dict[str, int] = {}
            for start, end, rec in sorted(weak, key=lambda w: w[0]):
                canonical = rec.canonical
                if canonical in found or tried.get(canonical, 0) >= MAX_WEAK_TRIES:
                    continue
                if _overlaps(strong, strong_starts, start, end):
                    continue
                tried[canonical] = tried.get(canonical, 0) + 1
                if _weak_ok(text, start, end, rec, strong, strong_starts, sep_starts, sep_ends):
                    found.add(canonical)
        for canonical in list(found):
            found.update(self._implies.get(canonical, []))
        return sorted(found)


def _overlaps(strong: list[tuple[int, int, str]], starts: list[int], start: int, end: int) -> bool:
    """True if [start, end) overlaps a strong span (spans are sorted, non-overlapping)."""
    i = bisect_right(starts, start) - 1
    if i >= 0 and strong[i][1] > start:
        return True
    return i + 1 < len(strong) and strong[i + 1][0] < end


def _weak_ok(
    text: str,
    start: int,
    end: int,
    rec: _Alias,
    strong: list[tuple[int, int, str]],
    strong_starts: list[int],
    sep_starts: list[int],
    sep_ends: list[int],
) -> bool:
    lo = max(0, start - CONTEXT_WINDOW)
    window = text[lo : end + CONTEXT_WINDOW]
    if any(c.search(window) for c in rec.context):
        return True
    # Line bounds, searched only within a fixed window so cost per match is constant.
    wlo = max(0, start - LIST_WINDOW)
    whi = min(len(text), end + LIST_WINDOW)
    nl = text.rfind("\n", wlo, start)
    line_start = nl + 1 if nl != -1 else wlo
    nl = text.find("\n", end, whi)
    line_end = nl if nl != -1 else whi
    # The alias must stand alone as one list item.
    i = bisect_right(sep_ends, start) - 1
    seg_start = max(line_start, sep_ends[i]) if i >= 0 else line_start
    j = bisect_left(sep_starts, end)
    seg_end = min(line_end, sep_starts[j]) if j < len(sep_starts) else line_end
    if text[seg_start:seg_end].strip(" \t.()*-") != text[start:end]:
        return False
    others = 0
    k = bisect_left(strong_starts, line_start)
    while k < len(strong) and strong[k][0] < line_end and others < 2:
        if strong[k][1] <= line_end:
            others += 1
        k += 1
    if others >= 2:
        return True
    return bool(_LIST_KEYWORDS.search(text, wlo, start))


def _drop_overlaps(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    kept: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if kept and span[0] < kept[-1][1]:
            continue
        kept.append(span)
    return kept


def _default_token(canonical: str) -> str:
    return re.sub(r"[^a-z0-9]", "", canonical.lower())


@cache
def default_matcher() -> SkillMatcher:
    return SkillMatcher(load_config("skills.yaml", SkillsConfig))


def extract_skills(text: str, matcher: SkillMatcher | None = None) -> list[str]:
    """Sorted, de-duplicated canonical skills found in ``text`` (first 20k chars)."""
    return (matcher or default_matcher()).extract(text)


def skills_text(skills: Sequence[str], matcher: SkillMatcher | None = None) -> str:
    """Search-safe token string for full-text indexing, e.g. ``cplusplus csharp golang``."""
    m = matcher or default_matcher()
    return " ".join(m.token(s) for s in skills)
