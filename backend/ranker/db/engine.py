"""Async SQLAlchemy engine factory."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.settings import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_size=5, pool_pre_ping=True)
