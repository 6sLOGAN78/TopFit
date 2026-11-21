"""HTTP API routers (mounted under /api by ranker.main)."""

from fastapi import APIRouter

from ranker.api import health, session

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(session.router)
