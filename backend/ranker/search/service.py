"""Search service shared by the API, the eval harness and the smoke check.

Phase 3 plan 01: lexical (english OR + skills) and vector legs fused with RRF inside one
transaction, hydrated, and logged to ``search_logs``. Later plans layer parsing, filters and
ranking signals on this path without changing the response contract.
"""

import json
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import anyio.to_thread
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import Embedder
from ranker.embeddings.service import vector_literal
from ranker.ingest.skills import default_matcher
from ranker.llm.client import LLMGateway
from ranker.search.config import RankingConfig, pipeline_version, ranking_config
from ranker.search.models import (
    JobResult,
    ParsedSummary,
    ParseModel,
    RelaxOption,
    SearchFilters,
    SearchResponse,
    SetAsideCount,
)
from ranker.search.parse import lex_text_for, parse_query
from ranker.search.sql import (
    HYDRATE_SQL,
    Legs,
    apply_vector_mode,
    build_hybrid_sql,
    build_set_aside_sql,
    compile_filters,
    skill_tokens,
)
from ranker.settings import Settings

log = structlog.get_logger("ranker.search")

# Bumped when the parse/rerank prompts change (feeds pipeline_version). No prompts yet.
PROMPT_VERSION = "search-v1"

MAX_LOGGED_QUERY_CHARS = 500
EMBED_CACHE_SIZE = 2048

SOURCE_DISPLAY_NAMES = {
    "remotive": "Remotive",
    "adzuna": "Adzuna",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "hn": "Hacker News",
}

_INSERT_LOG = text(
    "INSERT INTO search_logs (guest_id, surface, query_text, filters, parse, pipeline_version, "
    "results, ranked_ids, set_aside, lex_hits, vec_hits, degraded, latency_ms) "
    "VALUES (:guest_id, 'search', :q, CAST(:filters AS jsonb), CAST(:parse AS jsonb), :pv, "
    "CAST(:results AS jsonb), CAST(:ranked AS bigint[]), CAST(:set_aside AS jsonb), "
    ":lex_hits, :vec_hits, :degraded, :latency_ms) RETURNING id"
)


@dataclass(frozen=True)
class SearchRequest:
    q: str
    filters: SearchFilters | None = None
    auto_filters: bool = True
    parse_model: ParseModel = "haiku"
    cursor: str | None = None
    guest_id: UUID | None = None


class _EmbedCache:
    """Small in-process LRU keyed by (model, text); the query embedding is the hot path."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._data: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    def get(self, key: tuple[str, str]) -> list[float] | None:
        vec = self._data.get(key)
        if vec is not None:
            self._data.move_to_end(key)
        return vec

    def put(self, key: tuple[str, str], vec: list[float]) -> None:
        self._data[key] = vec
        self._data.move_to_end(key)
        while len(self._data) > self._size:
            self._data.popitem(last=False)


_embed_cache = _EmbedCache(EMBED_CACHE_SIZE)


async def _embed(embedder: Embedder, model: str, query: str) -> list[float]:
    key = (model, query)
    cached = _embed_cache.get(key)
    if cached is not None:
        return cached
    vec = (await anyio.to_thread.run_sync(embedder.embed, [query]))[0]
    _embed_cache.put(key, vec)
    return vec


def _lpa(value: Decimal | None) -> float | None:
    return None if value is None else round(float(value) / 100_000, 1)


def source_display_name(source: str) -> str:
    return SOURCE_DISPLAY_NAMES.get(source, source.title())


def _job_result(row: Any, query_skills: Sequence[str]) -> JobResult:
    skills = list(row["skills"] or [])
    return JobResult.model_validate(
        {
            "id": row["id"],
            "title": row["title"],
            "company": row["company"],
            "source": row["source"],
            "source_display_name": source_display_name(row["source"]),
            "url": row["url"],
            "city": row["city"],
            "cities": list(row["cities"] or []),
            "country_code": row["country_code"],
            "remote_type": row["remote_type"],
            "remote_eligibility": row["remote_eligibility"],
            "onsite_days_per_week": row["onsite_days_per_week"],
            "seniority": row["seniority"],
            "employment_type": row["employment_type"],
            "salary_min_lpa": _lpa(row["salary_min_inr_annual"]),
            "salary_max_lpa": _lpa(row["salary_max_inr_annual"]),
            "salary_is_estimate": row["salary_is_estimate"],
            "skills": skills,
            "matched_skills": [s for s in query_skills if s in skills],
            "ghost_reasons": list(row["ghost_reasons"] or []),
            "posted_at": row["posted_at"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "why_matched": "",
        }
    )


def _empty_response(filters: SearchFilters, cfg: RankingConfig, pv: str) -> SearchResponse:
    return SearchResponse(
        items=[],
        next_cursor=None,
        search_log_id=None,
        parsed=ParsedSummary(filters=filters),
        set_aside=[],
        set_aside_universe=cfg.retrieval.set_aside_universe_k,
        relax=[],
        degraded=False,
        pipeline_version=pv,
    )


async def search(
    engine: AsyncEngine,
    embedder: Embedder | None,
    gateway: LLMGateway | None,
    settings: Settings,
    req: SearchRequest,
) -> SearchResponse:
    started = time.perf_counter()
    cfg = ranking_config()
    ret = cfg.retrieval
    pv = pipeline_version(PROMPT_VERSION)
    filters = req.filters or SearchFilters()
    q = req.q.strip()
    if not q:
        return _empty_response(filters, cfg, pv)

    parse = await parse_query(
        q,
        req.filters,
        req.auto_filters,
        engine=engine,
        gateway=gateway,
        parse_model=req.parse_model,
        settings=settings,
    )
    filters = parse.filters
    skills = parse.skills
    tokens = skill_tokens(skills, default_matcher())
    compiled = compile_filters(filters)

    degraded = parse.degraded
    qvec: list[float] | None = None
    if embedder is not None:
        try:
            qvec = await _embed(embedder, settings.embed_model, parse.embed_text or q)
        except Exception as exc:  # LLM/embedding trouble must never break search
            log.error("query_embed_failed", error_type=type(exc).__name__)
    if qvec is None:
        degraded = True

    params: dict[str, Any] = {
        "lex_text": parse.lex_text,
        "skill_tokens": tokens,
        "w_skill": ret.skill_rank_weight,
        "lex_k": ret.lexical_k,
        "vec_k": ret.vector_k,
        "rrf_k": ret.rrf_k,
        "fused_k": ret.fused_k,
        **compiled.params,
    }
    legs: Legs = "both"
    if qvec is None:
        legs = "lex"
        del params["vec_k"]
    else:
        params["qvec"] = vector_literal(qvec)
    stmt = build_hybrid_sql(compiled.sql, legs=legs)

    async with engine.begin() as conn:
        if qvec is not None:
            await apply_vector_mode(conn, ret.vector_mode)
        fused = (await conn.execute(stmt, params)).all()
        set_aside: list[SetAsideCount] = []
        relax: list[RelaxOption] = []
        if compiled.clauses:
            sa_params = {
                **params,
                "lex_k": ret.set_aside_universe_k,
                "vec_k": ret.set_aside_universe_k,
            }
            counts = (
                (await conn.execute(build_set_aside_sql(compiled.clauses, legs), sa_params))
                .mappings()
                .one()
            )
            for key in compiled.clauses:
                if counts[f"sa_{key}"] > 0:
                    set_aside.append(SetAsideCount(filter=key, count=int(counts[f"sa_{key}"])))
                if key != "geo" and counts[f"rx_{key}"] > 0:
                    relax.append(RelaxOption(filter=key, count=int(counts[f"rx_{key}"])))
            relax.sort(key=lambda r: -r.count)
        ranked_ids = [int(r.id) for r in fused]
        page_ids = ranked_ids[: cfg.page_size]
        hydrated: dict[int, Any] = {}
        if page_ids:
            rows = (await conn.execute(HYDRATE_SQL, {"ids": page_ids})).mappings().all()
            hydrated = {int(r["id"]): r for r in rows}
        items: list[JobResult] = []
        results: list[dict[str, Any]] = []
        for rank, row in enumerate(fused[: cfg.page_size], start=1):
            job = hydrated.get(int(row.id))
            if job is None:
                continue
            items.append(_job_result(job, skills))
            results.append(
                {
                    "job_id": int(row.id),
                    "rank": rank,
                    "score": float(row.rrf),
                    "features": {
                        "rrf": float(row.rrf),
                        "lex_rank": row.lex_rank,
                        "vec_rank": row.vec_rank,
                        "cos": None if row.dist is None else 1.0 - float(row.dist),
                    },
                }
            )
        lex_hits = sum(1 for r in fused if r.lex_rank is not None)
        vec_hits = sum(1 for r in fused if r.vec_rank is not None)
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed = ParsedSummary(
            chips=parse.chips,
            parse_source=parse.parse_source,
            skills=skills,
            filters=filters,
            cached=parse.cached,
        )
        log_id = (
            await conn.execute(
                _INSERT_LOG,
                {
                    "guest_id": req.guest_id,
                    "q": q[:MAX_LOGGED_QUERY_CHARS],
                    "filters": json.dumps(
                        {
                            **filters.model_dump(),
                            "chip_sources": {c.field: c.source for c in parse.chips},
                        }
                    ),
                    "parse": json.dumps(
                        {
                            "source": parsed.parse_source,
                            "provider": None,
                            "cached": parse.cached,
                            "residue_count": len(parse.residue),
                            "prompt_version": parse.prompt_version,
                        }
                    ),
                    "pv": pv,
                    "results": json.dumps(results),
                    "ranked": ranked_ids,
                    "set_aside": json.dumps({sa.filter: sa.count for sa in set_aside}),
                    "lex_hits": lex_hits,
                    "vec_hits": vec_hits,
                    "degraded": degraded,
                    "latency_ms": latency_ms,
                },
            )
        ).scalar_one()

    log.info(
        "search_done",
        results=len(items),
        lex_hits=lex_hits,
        vec_hits=vec_hits,
        latency_ms=latency_ms,
        degraded=degraded,
        search_log_id=str(log_id),
    )
    return SearchResponse(
        items=items,
        next_cursor=None,
        search_log_id=log_id,
        parsed=parsed,
        set_aside=set_aside,
        set_aside_universe=ret.set_aside_universe_k,
        relax=relax,
        degraded=degraded,
        pipeline_version=pv,
    )


__all__ = ["SearchRequest", "lex_text_for", "search", "source_display_name"]
