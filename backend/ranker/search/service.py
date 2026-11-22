"""Search service shared by the API, the eval harness and the smoke check.

Phase 3 plan 01: lexical (english OR + skills) and vector legs fused with RRF inside one
transaction, hydrated, and logged to ``search_logs``. Later plans layer parsing, filters and
ranking signals on this path without changing the response contract.
"""

import json
import re
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import anyio.to_thread
import structlog
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
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
from ranker.search.rank import Candidate, Scored, apply_company_cap, score_candidates, why_matched
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
CURSOR_SALT = "rk-cursor-v1"
CURSOR_MAX_AGE_S = 24 * 3600
_WORD_RE = re.compile(r"[a-z0-9+#.]{3,}")
_REGION_CERTAIN = {"worldwide", "india", "apac"}
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


@dataclass(frozen=True)
class SearchOptions:
    """Internal knobs so the eval harness reuses the production path (never exposed by the API)."""

    legs: Literal["both", "lex", "vec"] = "both"
    apply_signals: bool = True
    log: bool = True
    now: datetime | None = None
    page_size: int | None = None


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


def _query_terms(q: str) -> list[str]:
    seen: list[str] = []
    for w in _WORD_RE.findall(q.lower()):
        if w not in seen:
            seen.append(w)
    return seen


def _place(row: Any) -> str:
    if row["remote_type"] == "remote":
        return "Remote · India OK" if row["remote_eligibility"] in _REGION_CERTAIN else "Remote"
    cities = list(row["cities"] or [])
    if len(cities) > 1:
        return "Multiple cities: " + ", ".join(cities[:2])
    return row["city"] or (cities[0] if cities else "") or row["country_code"] or ""


def _matched_terms(row: Any, job: JobResult, q_terms: Sequence[str]) -> list[str]:
    terms = list(job.matched_skills)
    title_words = set(_WORD_RE.findall(str(row["title"]).lower()))
    for t in q_terms:
        if t in title_words and t not in terms:
            terms.append(t)
    return terms


def _company_key(row: Any) -> str:
    if row["company_id"] is not None:
        return f"c:{row['company_id']}"
    if row["company"]:
        return f"n:{str(row['company']).lower()}"
    return f"j:{row['id']}"


def _candidate(row: Any, hit: Any) -> Candidate:
    return Candidate(
        id=int(row["id"]),
        rrf=float(hit.rrf),
        lex_rank=hit.lex_rank,
        vec_rank=hit.vec_rank,
        cos=None if hit.dist is None else 1.0 - float(hit.dist),
        company_key=_company_key(row),
        posted_or_first_seen=row["posted_at"] or row["first_seen_at"],
        region_certain=row["country_code"] == "IN" or row["remote_eligibility"] in _REGION_CERTAIN,
        salary_known=(
            row["salary_min_inr_annual"] is not None or row["salary_max_inr_annual"] is not None
        ),
        ghost=bool(row["ghost_reasons"]),
        seniority_unknown=row["seniority"] == "unknown",
    )


def _make_cursor(secret: str, search_log_id: UUID, offset: int) -> str:
    ser = URLSafeTimedSerializer(secret, salt=CURSOR_SALT)
    return str(ser.dumps({"sid": str(search_log_id), "off": offset}))


def _read_cursor(secret: str, cursor: str) -> tuple[UUID, int]:
    ser = URLSafeTimedSerializer(secret, salt=CURSOR_SALT)
    try:
        data = ser.loads(cursor, max_age=CURSOR_MAX_AGE_S)
        sid, off = UUID(str(data["sid"])), int(data["off"])
        if off < 0:
            raise ValueError("offset")
    except (BadData, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid_cursor") from exc
    return sid, off


_LOAD_LOG = text(
    "SELECT query_text, filters, parse, ranked_ids, pipeline_version, degraded "
    "FROM search_logs WHERE id = :id"
)


async def _cursor_page(
    engine: AsyncEngine, settings: Settings, cursor: str, cfg: RankingConfig, now: datetime
) -> SearchResponse:
    """Hydrate the next page of a stored ranking; no retrieval and no new search_logs row."""
    secret = settings.session_secret.get_secret_value()
    sid, off = _read_cursor(secret, cursor)
    size = cfg.page_size
    async with engine.connect() as conn:
        row = (await conn.execute(_LOAD_LOG, {"id": sid})).mappings().first()
        if row is None:
            raise HTTPException(status_code=422, detail="invalid_cursor")
        ranked_raw: list[Any] = list(row["ranked_ids"] or [])
        ranked = [int(i) for i in ranked_raw]
        page_ids = ranked[off : off + size]
        hydrated: dict[int, Any] = {}
        if page_ids:
            rows = (await conn.execute(HYDRATE_SQL, {"ids": page_ids})).mappings().all()
            hydrated = {int(r["id"]): r for r in rows}
    stored = {
        k: v for k, v in dict(row["filters"] or {}).items() if k in SearchFilters.model_fields
    }
    filters = SearchFilters.model_validate(stored)
    plog = dict(row["parse"] or {})
    skills = list(plog.get("skills") or [])
    q_terms = _query_terms(str(row["query_text"] or ""))
    items: list[JobResult] = []
    for jid in page_ids:
        r = hydrated.get(jid)
        if r is None:  # no longer active since the first page
            continue
        job = _job_result(r, skills)
        job.why_matched = why_matched(
            matched_terms=_matched_terms(r, job, q_terms),
            place=_place(r),
            posted=r["posted_at"] or r["first_seen_at"],
            now=now,
        )
        items.append(job)
    nxt = off + size
    return SearchResponse(
        items=items,
        next_cursor=_make_cursor(secret, sid, nxt) if nxt < len(ranked) else None,
        search_log_id=sid,
        parsed=ParsedSummary(
            parse_source=plog.get("source", "rules"),
            skills=skills,
            filters=filters,
            cached=bool(plog.get("cached", False)),
        ),
        set_aside=[],
        set_aside_universe=cfg.retrieval.set_aside_universe_k,
        relax=[],
        degraded=bool(row["degraded"]),
        pipeline_version=str(row["pipeline_version"]),
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
    *,
    options: SearchOptions = SearchOptions(),
) -> SearchResponse:
    started = time.perf_counter()
    cfg = ranking_config()
    now = options.now or datetime.now(UTC)
    if req.cursor:
        return await _cursor_page(engine, settings, req.cursor, cfg, now)
    page_size = options.page_size or cfg.page_size
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
    if qvec is None or options.legs == "lex":
        legs = "lex"
        params.pop("vec_k", None)
    else:
        params["qvec"] = vector_literal(qvec)
        legs = options.legs
    stmt = build_hybrid_sql(compiled.sql, legs=legs)

    async with engine.begin() as conn:
        if legs != "lex":
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
        fused_ids = [int(r.id) for r in fused]
        hydrated: dict[int, Any] = {}
        if fused_ids:
            rows = (await conn.execute(HYDRATE_SQL, {"ids": fused_ids})).mappings().all()
            hydrated = {int(r["id"]): r for r in rows}
        hits = {int(r.id): r for r in fused if int(r.id) in hydrated}
        cands = [_candidate(hydrated[i], hits[i]) for i in fused_ids if i in hydrated]
        if options.apply_signals:
            scored = score_candidates(
                cands,
                cfg,
                now=now,
                salary_filter=filters.min_salary_lpa is not None,
                seniority_filter=bool(filters.seniority),
            )
            scored = apply_company_cap(scored, cfg.diversity.max_per_company_top10)
        else:
            scored = [
                Scored(
                    c,
                    c.rrf,
                    {
                        "rrf": c.rrf,
                        "lex_rank": None if c.lex_rank is None else float(c.lex_rank),
                        "vec_rank": None if c.vec_rank is None else float(c.vec_rank),
                        "cos": c.cos,
                        "recency": None,
                        "region": None,
                        "salary_known": None,
                        "ghost": None,
                    },
                )
                for c in cands
            ]
        ranked_ids = [sc.candidate.id for sc in scored]
        q_terms = _query_terms(q)
        items: list[JobResult] = []
        results: list[dict[str, Any]] = []
        for rank, sc in enumerate(scored[:page_size], start=1):
            row = hydrated[sc.candidate.id]
            job = _job_result(row, skills)
            job.why_matched = why_matched(
                matched_terms=_matched_terms(row, job, q_terms),
                place=_place(row),
                posted=row["posted_at"] or row["first_seen_at"],
                now=now,
            )
            items.append(job)
            results.append(
                {
                    "job_id": sc.candidate.id,
                    "rank": rank,
                    "score": sc.score,
                    "features": sc.features,
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
        log_id: UUID | None = None
        if options.log:
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
                                "skills": skills,
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
        search_log_id=str(log_id) if log_id else None,
    )
    return SearchResponse(
        items=items,
        next_cursor=(
            _make_cursor(settings.session_secret.get_secret_value(), log_id, page_size)
            if log_id is not None and len(ranked_ids) > page_size
            else None
        ),
        search_log_id=log_id,
        parsed=parsed,
        set_aside=set_aside,
        set_aside_universe=ret.set_aside_universe_k,
        relax=relax,
        degraded=degraded,
        pipeline_version=pv,
    )


__all__ = ["SearchOptions", "SearchRequest", "lex_text_for", "search", "source_display_name"]
