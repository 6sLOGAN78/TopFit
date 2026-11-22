"""Pure guest ranking: light signals on top of RRF, the company cap and the why-matched line.

No I/O. ``score_candidates`` adds freshness, region certainty and salary-known boosts and
ghost / unknown-seniority penalties to the normalised RRF score (D-11). ``apply_company_cap``
defers extra jobs of one company to just after the top ten without dropping any (D-13).
"""

import math
from dataclasses import dataclass
from datetime import datetime

from ranker.search.config import RankingConfig

MAX_WHY_TERMS = 3
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class Candidate:
    id: int
    rrf: float
    lex_rank: int | None
    vec_rank: int | None
    cos: float | None
    company_key: str
    posted_or_first_seen: datetime
    region_certain: bool
    salary_known: bool
    ghost: bool
    seniority_unknown: bool


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    score: float
    features: dict[str, float | None]


def score_candidates(
    cands: list[Candidate],
    cfg: RankingConfig,
    *,
    now: datetime,
    salary_filter: bool,
    seniority_filter: bool,
) -> list[Scored]:
    """Score and sort (score DESC, id ASC). RRF is divided by its fixed maximum, 2 / (k + 1)."""
    w = cfg.guest_weights
    max_rrf = 2.0 / (cfg.retrieval.rrf_k + 1)
    tau = cfg.recency_half_life_days
    out: list[Scored] = []
    for c in cands:
        rrf_norm = c.rrf / max_rrf
        age_days = max((now - c.posted_or_first_seen).total_seconds() / SECONDS_PER_DAY, 0.0)
        recency = math.exp(-age_days / tau)
        region = 1.0 if c.region_certain else 0.0
        salary = 1.0 if (salary_filter and c.salary_known) else 0.0
        ghost = 1.0 if c.ghost else 0.0
        unknown_sen = 1.0 if (seniority_filter and c.seniority_unknown) else 0.0
        score = (
            w.rrf * rrf_norm
            + w.recency * recency
            + w.region_certain * region
            + w.salary_known * salary
            - w.ghost_penalty * ghost
            - w.unknown_seniority_penalty * unknown_sen
        )
        out.append(
            Scored(
                c,
                score,
                {
                    "rrf": c.rrf,
                    "lex_rank": None if c.lex_rank is None else float(c.lex_rank),
                    "vec_rank": None if c.vec_rank is None else float(c.vec_rank),
                    "cos": c.cos,
                    "recency": recency,
                    "region": region,
                    "salary_known": 1.0 if c.salary_known else 0.0,
                    "ghost": ghost,
                },
            )
        )
    out.sort(key=lambda s: (-s.score, s.candidate.id))
    return out


def apply_company_cap(items: list[Scored], max_per_company: int, top_n: int = 10) -> list[Scored]:
    """At most ``max_per_company`` per company in the first ``top_n``; extras follow position
    ``top_n`` in their original relative order, then the untouched tail. Nothing is dropped."""
    selected: list[Scored] = []
    deferred: list[Scored] = []
    counts: dict[str, int] = {}
    pos = 0
    while pos < len(items) and len(selected) < top_n:
        item = items[pos]
        pos += 1
        key = item.candidate.company_key
        if counts.get(key, 0) >= max_per_company:
            deferred.append(item)
        else:
            counts[key] = counts.get(key, 0) + 1
            selected.append(item)
    return selected + deferred + items[pos:]


def _posted_text(posted: datetime, now: datetime) -> str:
    days = int((now - posted).total_seconds() // SECONDS_PER_DAY)
    return "posted today" if days < 1 else f"posted {days}d ago"


def why_matched(*, matched_terms: list[str], place: str, posted: datetime, now: datetime) -> str:
    """Rule-based explanation, e.g. ``Matches python, backend · Bengaluru · posted 2d ago``."""
    parts: list[str] = []
    if matched_terms:
        parts.append("Matches " + ", ".join(matched_terms[:MAX_WHY_TERMS]))
    if place:
        parts.append(place)
    parts.append(_posted_text(posted, now))
    return " · ".join(parts)
