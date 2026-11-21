"""Liveness (healthz) and readiness (readyz) endpoints."""

import asyncio
from typing import Any

import anyio
from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter()

MIN_VECTOR = (0, 8, 3)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only: no DB or model dependency."""
    return {"status": "ok"}


@router.get("/readyz")
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
    if embedder is not None:
        try:
            with anyio.fail_after(5):
                vec = await anyio.to_thread.run_sync(embedder.embed, ["readiness probe"])
            checks["embedding"] = {"ok": len(vec[0]) == 384, "dim": len(vec[0])}
        except Exception as exc:
            checks["embedding"] = {"ok": False, "dim": 0, "error": type(exc).__name__}
    else:
        checks["embedding"] = {"ok": False, "dim": 0}

    all_ok = all(c["ok"] for c in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ok" if all_ok else "unavailable", "checks": checks}
