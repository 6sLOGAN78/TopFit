"""GET /api/meta: index size, available parse models and the city roster (UI bootstrap)."""

from collections.abc import Callable

import anyio
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ranker.config_loader import load_config
from ranker.ingest.normalize.location import CitiesConfig
from ranker.search.models import ErrorBody, ErrorResponse, MetaResponse, ParseModel

router = APIRouter(tags=["meta"])
log = structlog.get_logger("ranker.api.meta")

META_TIMEOUT_SECONDS = 2

_ACTIVE = text("SELECT count(*) FROM jobs WHERE status = 'active' AND canonical_job_id IS NULL")


def city_roster() -> list[str]:
    cfg = load_config("cities.yaml", CitiesConfig)
    return [c.canonical for c in cfg.cities if c.country == "IN"]


def parse_models(gateway: object | None) -> list[ParseModel]:
    """Parse models the gateway offers; a later plan adds ``providers()`` to the gateway."""
    providers: Callable[[], list[ParseModel]] | None = getattr(gateway, "providers", None)
    return providers() if providers is not None else ["haiku"]


@router.get(
    "/meta",
    response_model=MetaResponse,
    responses={503: {"model": ErrorResponse}},
)
async def meta(request: Request) -> MetaResponse | JSONResponse:
    state = request.app.state
    try:
        with anyio.fail_after(META_TIMEOUT_SECONDS):
            async with state.engine.connect() as conn:
                active = int((await conn.execute(_ACTIVE)).scalar_one())
    except Exception as exc:
        log.error("meta_failed", error_type=type(exc).__name__)
        body = ErrorResponse(error=ErrorBody(code="unavailable", message="Index unavailable"))
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
    return MetaResponse(
        active_jobs=active,
        parse_models=parse_models(getattr(state, "gateway", None)),
        cities=city_roster(),
    )
