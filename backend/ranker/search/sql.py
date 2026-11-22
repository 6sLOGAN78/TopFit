"""Retrieval SQL for hybrid search (SRCH-02/03/04).

Rules (RESEARCH Pattern 3):
- No shared ``base`` CTE: each leg reads ``jobs`` directly, so the vector leg can use the HNSW
  index when ``vector_mode`` is ``hnsw``.
- The query vector is a bound constant inside the vector leg (never joined from another
  relation), which keeps an index ordering scan possible.
- Every ORDER BY has an ``id`` tie-breaker; ``row_number()`` sits outside the LIMIT subquery.
- User text reaches ``tsquery`` only through ``plainto_tsquery`` plus an OR rewrite, never
  ``to_tsquery``. Skill tokens are validated against ``^[a-z0-9]+$`` first.
- ``filter_sql`` is a fragment composed by code from a whitelist, never from user input.
"""

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection

from ranker.ingest.skills import SkillMatcher, default_matcher
from ranker.search.models import SearchFilters, SetAsideKey

Legs = Literal["both", "lex", "vec"]

# plainto_tsquery only emits quoted lexemes joined by ' & ' (and '<->' for phrases), so the
# text -> tsquery cast of the rewritten string cannot fail.
OR_TSQUERY = (
    "CASE WHEN numnode(plainto_tsquery('english', CAST(:lex_text AS text))) = 0 THEN NULL "
    "ELSE replace(plainto_tsquery('english', CAST(:lex_text AS text))::text, ' & ', ' | ')"
    "::tsquery END"
)
SKILLS_TSQUERY = (
    "CASE WHEN cardinality(CAST(:skill_tokens AS text[])) = 0 THEN NULL "
    "ELSE to_tsquery('simple', array_to_string(CAST(:skill_tokens AS text[]), ' | ')) END"
)

_TOKEN_RE = re.compile(r"^[a-z0-9]+$")

_LEX_CTE = """
lex AS (
  SELECT id, row_number() OVER (ORDER BY score DESC, id) AS r FROM (
    SELECT j.id,
           coalesce(ts_rank_cd(j.tsv, q.tq), 0)
             + CAST(:w_skill AS float8) * coalesce(ts_rank_cd(j.skills_tsv, q.sq), 0) AS score
    FROM jobs j, q
    WHERE j.status = 'active' AND j.canonical_job_id IS NULL
      AND (j.tsv @@ q.tq OR j.skills_tsv @@ q.sq)
      {filter_sql}
    ORDER BY score DESC, j.id LIMIT :lex_k) s
)"""

_VEC_CTE = """
vec AS (
  SELECT id, dist, row_number() OVER (ORDER BY dist, id) AS r FROM (
    SELECT j.id, j.embedding <=> CAST(:qvec AS vector(384)) AS dist
    FROM jobs j
    WHERE j.status = 'active' AND j.canonical_job_id IS NULL AND j.embedding IS NOT NULL
      {filter_sql}
    ORDER BY dist, j.id LIMIT :vec_k) s
)"""

_Q_CTE = f"q AS (SELECT {OR_TSQUERY} AS tq, {SKILLS_TSQUERY} AS sq)"

_FUSE = """
SELECT u.id, sum(1.0 / (CAST(:rrf_k AS int) + u.r)) AS rrf,
       min(u.r) FILTER (WHERE u.leg = 'lex') AS lex_rank,
       min(u.r) FILTER (WHERE u.leg = 'vec') AS vec_rank,
       min(u.dist) AS dist
FROM ({union}) u
GROUP BY u.id ORDER BY rrf DESC, u.id LIMIT :fused_k"""

_LEX_ROWS = "SELECT id, r, 'lex' AS leg, CAST(NULL AS float8) AS dist FROM lex"
_VEC_ROWS = "SELECT id, r, 'vec' AS leg, dist FROM vec"


def build_hybrid_sql(filter_sql: str = "", legs: Legs = "both") -> TextClause:
    """Compose the hybrid retrieval statement.

    Bind parameters by leg: lex needs lex_text, skill_tokens, w_skill, lex_k; vec needs
    qvec, vec_k; the fusion needs rrf_k and fused_k.
    """
    ctes: list[str] = []
    rows: list[str] = []
    if legs in ("both", "lex"):
        ctes += [_Q_CTE, _LEX_CTE.format(filter_sql=filter_sql).strip()]
        rows.append(_LEX_ROWS)
    if legs in ("both", "vec"):
        ctes.append(_VEC_CTE.format(filter_sql=filter_sql).strip())
        rows.append(_VEC_ROWS)
    sql = "WITH " + ",\n".join(ctes) + _FUSE.format(union=" UNION ALL ".join(rows))
    return text(sql)


# --- hard filters (SRCH-05, D-08, D-09) ------------------------------------------------------
# Clause text is a module constant; only the choice of clauses is dynamic and every value is a
# bound parameter. Inactive filters add no clause (no optional-parameter branches, which
# defeat index use and plan caching).

_REMOTE_INDIA_OK = (
    "j.remote_type = 'remote' AND j.remote_eligibility IN ('worldwide', 'india', 'apac')"
)
# No city: India jobs, remote jobs India can take (unknown eligibility stays, ADR 0002 D-07)
# and non-remote jobs with no stated country (ADR 0002 geo_unknown). US-only and EU-only
# remote jobs are the ones set aside.
_GEO = (
    "j.country_code = 'IN' "
    "OR (j.remote_type = 'remote' AND "
    "j.remote_eligibility IN ('worldwide', 'india', 'apac', 'unknown')) "
    "OR (j.country_code IS NULL AND j.remote_type <> 'remote')"
)
_CITY = "j.city = ANY(CAST(:f_cities AS text[])) OR j.cities && CAST(:f_cities AS text[])"
_WORK_MODE = "j.remote_type = ANY(CAST(:f_modes AS text[]))"
# Unknown seniority stays in; 03-04 ranks it lower.
_SENIORITY = "j.seniority = ANY(CAST(:f_sen AS text[])) OR j.seniority = 'unknown'"
# Unknown salary stays in; estimates satisfy the minimum unless exclude_estimates.
_MIN_SALARY = (
    "j.salary_max_inr_annual >= CAST(:f_min_inr AS numeric) "
    "OR j.salary_min_inr_annual >= CAST(:f_min_inr AS numeric) "
    "OR (j.salary_min_inr_annual IS NULL AND j.salary_max_inr_annual IS NULL)"
)
_ESTIMATES = "NOT j.salary_is_estimate"

_CLAUSE_ORDER: tuple[SetAsideKey, ...] = (
    "geo",
    "city",
    "work_mode",
    "seniority",
    "min_salary",
    "estimates",
)
LPA = 100_000


@dataclass(frozen=True)
class CompiledFilters:
    clauses: dict[SetAsideKey, str]
    params: dict[str, object]

    @property
    def sql(self) -> str:
        """Clauses ANDed in a fixed order, ready for ``build_hybrid_sql(filter_sql=...)``."""
        return "".join(f" AND ({self.clauses[k]})" for k in _CLAUSE_ORDER if k in self.clauses)


def compile_filters(f: SearchFilters) -> CompiledFilters:
    """Whitelisted SQL clauses and bind parameters for the active filters only."""
    clauses: dict[SetAsideKey, str] = {}
    params: dict[str, object] = {}
    if f.cities:
        city = _CITY
        if f.remote_ok:
            city += f" OR ({_REMOTE_INDIA_OK})"
        clauses["city"] = city
        params["f_cities"] = list(f.cities)
    else:
        clauses["geo"] = _GEO
    if f.work_modes:
        clauses["work_mode"] = _WORK_MODE
        params["f_modes"] = list(f.work_modes)
    if f.seniority:
        clauses["seniority"] = _SENIORITY
        params["f_sen"] = list(f.seniority)
    if f.min_salary_lpa is not None:
        clauses["min_salary"] = _MIN_SALARY
        params["f_min_inr"] = f.min_salary_lpa * LPA
        if f.exclude_estimates:
            clauses["estimates"] = _ESTIMATES
    return CompiledFilters(clauses, params)


def build_set_aside_sql(clauses: dict[SetAsideKey, str], legs: Legs = "both") -> TextClause:
    """Per-filter set-aside and relax counts over an unfiltered relevance universe (UI-06).

    The universe is the union of the unfiltered lexical and vector top ``:lex_k`` / ``:vec_k``
    (callers pass ``set_aside_universe_k`` for both). For each active clause ``k``:
    ``sa_k`` counts universe rows failing ``k``; ``rx_k`` counts rows failing only ``k``, which
    is how many jobs removing that single filter would add. Binds are the retrieval binds plus
    the compiled filter params.
    """
    ctes: list[str] = []
    members: list[str] = []
    if legs in ("both", "lex"):
        ctes += [_Q_CTE, _LEX_CTE.format(filter_sql="").strip()]
        members.append("SELECT id FROM lex")
    if legs in ("both", "vec"):
        ctes.append(_VEC_CTE.format(filter_sql="").strip())
        members.append("SELECT id FROM vec")
    ctes.append("universe AS (" + " UNION ".join(members) + ")")
    cols = ["count(*) AS universe"]
    for key in _CLAUSE_ORDER:
        if key not in clauses:
            continue
        fail = f"NOT coalesce(({clauses[key]}), false)"
        others = [f"coalesce(({c}), false)" for k, c in clauses.items() if k != key]
        cols.append(f"count(*) FILTER (WHERE {fail}) AS sa_{key}")
        cols.append(f"count(*) FILTER (WHERE {' AND '.join([fail, *others])}) AS rx_{key}")
    sql = (
        "WITH "
        + ",\n".join(ctes)
        + "\nSELECT "
        + ", ".join(cols)
        + " FROM jobs j JOIN universe u ON u.id = j.id"
    )
    return text(sql)


async def apply_vector_mode(conn: AsyncConnection, mode: Literal["exact", "hnsw"]) -> None:
    """Transaction-local planner settings via set_config (SET cannot take bind parameters)."""
    stmt = text("SELECT set_config(:k, :v, true)")
    if mode == "exact":
        await conn.execute(stmt, {"k": "enable_indexscan", "v": "off"})
        return
    await conn.execute(stmt, {"k": "hnsw.ef_search", "v": "200"})
    await conn.execute(stmt, {"k": "hnsw.iterative_scan", "v": "relaxed_order"})


def skill_tokens(skills: list[str], matcher: SkillMatcher | None = None) -> list[str]:
    """Search-safe simple-config tokens for canonical skills (cplusplus, csharp, golang...)."""
    m = matcher or default_matcher()
    out: list[str] = []
    for skill in skills:
        token = m.token(skill)
        if _TOKEN_RE.match(token) and token not in out:
            out.append(token)
    return out


def rrf_score(ranks: list[int | None], k: int = 60) -> float:
    """Reciprocal rank fusion score of one document over the legs that returned it."""
    return sum(1.0 / (k + r) for r in ranks if r is not None)


HYDRATE_SQL = text(
    "SELECT j.id, j.title, c.name AS company, j.source, j.url, j.city, j.cities, "
    "j.country_code, j.remote_type, j.remote_eligibility, j.onsite_days_per_week, "
    "j.seniority, j.employment_type, j.salary_min_inr_annual, j.salary_max_inr_annual, "
    "j.salary_is_estimate, j.skills, j.ghost_reasons, j.posted_at, j.first_seen_at, "
    "j.last_seen_at, j.company_id, c.normalized_name AS company_key "
    "FROM jobs j LEFT JOIN companies c ON c.id = j.company_id "
    "WHERE j.id = ANY(CAST(:ids AS bigint[])) AND j.status = 'active'"
)
