"""Async SQLAlchemy engine factory.

WR-04 / SEC-07: outside development the engine hides bound parameters, so DB exception text
(and anything that logs it) never contains bound values such as emails or resume text. The test
environment hides them too, so the suite exercises the production path.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ranker.settings import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        pool_pre_ping=True,
        hide_parameters=settings.app_env != "development",
    )
