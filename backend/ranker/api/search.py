"""GET /api/jobs/search: hybrid search for guests (SRCH-02..04, UI-04).

Query parameter names are the URL contract used by the web app. The rate-limit dependency
added later reads ``request.cookies`` directly so this OpenAPI schema does not change.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import StringConstraints

from ranker.search.config import pipeline_version, ranking_config
from ranker.search.models import (
    MAX_QUERY_CHARS,
    ErrorResponse,
    ParsedSummary,
    ParseModel,
    SearchFilters,
    SearchResponse,
    SeniorityFilter,
    WorkMode,
    clean_query,
)
from ranker.search.service import PROMPT_VERSION, SearchRequest, search

router = APIRouter(prefix="/jobs", tags=["search"])

CityParam = Annotated[str, StringConstraints(max_length=60)]


@router.get(
    "/search",
    response_model=SearchResponse,
    responses={422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def search_jobs(
    request: Request,
    response: Response,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    city: Annotated[list[CityParam] | None, Query(max_length=10)] = None,
    remote_ok: bool = False,
    min_lpa: Annotated[int | None, Query(ge=0, le=200)] = None,
    no_est: bool = False,
    mode: Annotated[list[WorkMode] | None, Query()] = None,
    seniority: Annotated[list[SeniorityFilter] | None, Query()] = None,
    auto: bool = True,
    parse_model: ParseModel = "haiku",
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> SearchResponse:
    response.headers["Cache-Control"] = "no-store"
    cleaned = clean_query(q)
    state = request.app.state
    explicit = bool(city or remote_ok or min_lpa is not None or no_est or mode or seniority)
    filters = (
        SearchFilters(
            cities=city or [],
            remote_ok=remote_ok,
            work_modes=mode or [],
            seniority=seniority or [],
            min_salary_lpa=min_lpa,
            exclude_estimates=no_est,
        )
        if explicit
        else None
    )
    if not cleaned:
        return SearchResponse(
            items=[],
            next_cursor=None,
            search_log_id=None,
            parsed=ParsedSummary(filters=filters or SearchFilters()),
            set_aside=[],
            set_aside_universe=ranking_config().retrieval.set_aside_universe_k,
            relax=[],
            degraded=False,
            pipeline_version=pipeline_version(PROMPT_VERSION),
        )
    req = SearchRequest(
        q=cleaned,
        filters=filters,
        auto_filters=auto,
        parse_model=parse_model,
        cursor=cursor,
    )
    return await search(
        state.engine,
        getattr(state, "embedder", None),
        getattr(state, "gateway", None),
        state.settings,
        req,
    )
