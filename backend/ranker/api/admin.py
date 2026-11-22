"""Admin endpoints. Auth is the swappable `require_admin` dependency (Phase 5: admin role)."""

import hmac
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.sources import registered_sources
from ranker.settings import Settings
from ranker.tasks.queue import enqueue

log = structlog.get_logger("ranker.api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(
    request: Request, x_admin_token: Annotated[str | None, Header()] = None
) -> None:
    settings: Settings = request.app.state.settings
    secret = settings.admin_token.get_secret_value() if settings.admin_token else ""
    if not secret:
        raise HTTPException(status_code=404)  # endpoint invisible when unset
    if x_admin_token is None or not hmac.compare_digest(x_admin_token.encode(), secret.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/ingest/{source}", status_code=202, dependencies=[Depends(require_admin)])
async def trigger_ingest(
    request: Request, source: str, mode: Literal["incremental", "backfill"] = "incremental"
) -> dict[str, int]:
    if source not in registered_sources():
        raise HTTPException(status_code=404, detail="unknown_source")
    engine: AsyncEngine = request.app.state.engine
    task_id = await enqueue(
        engine, "ingest", {"source": source, "mode": mode}, dedupe_key=f"ingest:{source}"
    )
    if task_id is None:
        raise HTTPException(status_code=409, detail="already_pending")
    log.info("admin_ingest_enqueued", source=source, task_id=task_id)
    return {"task_id": task_id}
