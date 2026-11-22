"""Liveness (healthz) and readiness (readyz) endpoints.

CR-01 / T-02-70: readyz is public, so it must be cheap. The embedding self-check runs once at
startup (:func:`embedding_selfcheck`, called from the app lifespan) and readyz only reports the
cached result; the request path does ``SELECT 1`` plus the pgvector version lookup under a 3 s
timeout and no model inference or thread-pool work. There is deliberately no background
refresh: the ONNX model is immutable once loaded, so re-running inference would only bring back
idle CPU load without detecting anything new.
"""

import asyncio
from functools import partial
from typing import Any

import anyio
import anyio.to_thread
import structlog
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import EMBED_DIM, Embedder

router = APIRouter()
log = structlog.get_logger("ranker.api.health")

SELFCHECK_TIMEOUT_SECONDS = 30

MIN_VECTOR = (0, 8, 3)


class HealthResponse(BaseModel):
    status: str


class ReadyzResponse(BaseModel):
    status: str
    checks: dict[str, dict[str, Any]]


async def embedding_selfcheck(embedder: Embedder | None) -> dict[str, Any]:
    """Embed one probe text once (startup only). Logs only the exception type on failure."""
    if embedder is None:
        return {"ok": False, "dim": 0}
    try:
        with anyio.fail_after(SELFCHECK_TIMEOUT_SECONDS):
            vectors = await anyio.to_thread.run_sync(
                partial(embedder.embed, ["readiness probe"]), abandon_on_cancel=True
            )
        dim = len(vectors[0])
    except Exception as exc:
        log.error("embedding_selfcheck_failed", error_type=type(exc).__name__)
        return {"ok": False, "dim": 0}
    return {"ok": dim == EMBED_DIM, "dim": dim}


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> dict[str, str]:
    """Liveness only: no DB or model dependency."""
    return {"status": "ok"}


@router.get("/readyz", response_model=ReadyzResponse, responses={503: {"model": ReadyzResponse}})
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    engine: AsyncEngine = request.app.state.engine
    try:
        async with asyncio.timeout(3), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            checks["db"] = {"ok": True}
            ver = (
                await conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one_or_none()
            ok = ver is not None and tuple(int(p) for p in ver.split(".")[:3]) >= MIN_VECTOR
            checks["pgvector"] = {"ok": ok, "version": ver, "min": "0.8.3"}
    except Exception as exc:  # report the type only, never the DSN
        checks.setdefault("db", {"ok": False, "error": type(exc).__name__})
        checks.setdefault("pgvector", {"ok": False, "version": None, "min": "0.8.3"})

    embedder = getattr(request.app.state, "embedder", None)
    checks["model"] = {"ok": embedder is not None}
    # Cached startup result; no inference on the request path (CR-01).
    checks["embedding"] = dict(
        getattr(request.app.state, "embedding_check", None) or {"ok": False, "dim": 0}
    )

    all_ok = all(c["ok"] for c in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ok" if all_ok else "unavailable", "checks": checks}
