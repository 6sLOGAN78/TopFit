"""Skill extraction (NORM-06): curated taxonomy + symbol-safe, ambiguity-aware matcher.

Pure module: no Settings import. The taxonomy lives in ``config/skills.yaml``.

Matching rules
- Custom boundaries (``\\b`` fails next to ``+``, ``#`` and ``.``), so ``C++``, ``C#``,
  ``.NET`` and ``Node.js`` match and ``JavaScript`` never yields ``Java``.
- Longest alias first inside one alternation (``React Native`` before ``React``).
- Ambiguous aliases (``Go``, ``R``, ``Swift`` ...) only count with a nearby context regex
  or when they sit in a separator-delimited skills list.
"""

import re
from collections.abc import Sequence
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ranker.config_loader import load_config

BOUND_L = r"(?<![A-Za-z0-9_+#.])"
BOUND_R = r"(?![A-Za-z0-9_+#])"

MAX_TEXT_CHARS = 20_000
CONTEXT_WINDOW = 40

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

_SEPARATORS = re.compile(r"[,/|•·;:\n]|\s+(?:and|or)\s+|\s+&\s+")
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
        strong: list[tuple[int, int, str]] = []
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
                    strong.append((m.start(1), m.end(1), rec.canonical))
        strong = _drop_overlaps(strong)
        found: set[str] = set()
        for _, _, canonical in strong:
            found.add(canonical)
        for start, end, rec in weak:
            if any(s < end and start < e for s, e, _ in strong):
                continue
            if self._weak_ok(text, start, end, rec, strong):
                found.add(rec.canonical)
        for canonical in list(found):
            found.update(self._implies.get(canonical, []))
        return sorted(found)

    @staticmethod
    def _weak_ok(
        text: str, start: int, end: int, rec: _Alias, strong: list[tuple[int, int, str]]
    ) -> bool:
        lo = max(0, start - CONTEXT_WINDOW)
        window = text[lo : end + CONTEXT_WINDOW]
        if any(c.search(window) for c in rec.context):
            return True
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end == -1:
            line_end = len(text)
        # The alias must stand alone as one list item.
        seg_start = line_start
        seg_end = line_end
        for sep in _SEPARATORS.finditer(text, line_start, line_end):
            if sep.end() <= start:
                seg_start = sep.end()
            elif sep.start() >= end:
                seg_end = sep.start()
                break
        segment = text[seg_start:seg_end].strip(" \t.()*-")
        if segment != text[start:end]:
            return False
        others = sum(1 for s, e, _ in strong if line_start <= s and e <= line_end)
        if others >= 2:
            return True
        return bool(_LIST_KEYWORDS.search(text[line_start:start]))


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
