"""HTTP API routers (mounted under /api by ranker.main)."""

from fastapi import APIRouter

from ranker.api import admin, health, session

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(session.router)
api_router.include_router(admin.router)
