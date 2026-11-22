"""WR-04 / SEC-07: bound SQL parameters never appear in exception text outside development."""

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ranker.db import create_engine_from_settings
from ranker.settings import Settings, get_settings

SettingsFor = Callable[..., Settings]
UNREACHABLE = "postgresql+psycopg://u:p@127.0.0.1:1/x"


@pytest.fixture
def settings_for(monkeypatch: pytest.MonkeyPatch) -> Iterator[SettingsFor]:
    def make(app_env: str, database_url: str = UNREACHABLE) -> Settings:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("DATABASE_URL", database_url)
        get_settings.cache_clear()
        return get_settings()

    yield make
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("app_env", "hidden"), [("production", True), ("test", True), ("development", False)]
)
async def test_hide_parameters_by_env(
    settings_for: SettingsFor, app_env: str, hidden: bool
) -> None:
    engine = create_engine_from_settings(settings_for(app_env))
    try:
        assert engine.sync_engine.hide_parameters is hidden
    finally:
        await engine.dispose()


@pytest.mark.parametrize(("app_env", "leaks"), [("test", False), ("development", True)])
async def test_bound_value_absent_from_dbapi_error(
    database_url: str, settings_for: SettingsFor, app_env: str, leaks: bool
) -> None:
    engine = create_engine_from_settings(settings_for(app_env, database_url))
    try:
        with pytest.raises(DBAPIError) as info:
            async with engine.connect() as conn:
                # The server error ("relation does not exist") never echoes the value itself;
                # only SQLAlchemy's "[parameters: ...]" suffix could.
                await conn.execute(
                    text("SELECT :v FROM no_such_table_wr04"), {"v": "sensitive-value-123"}
                )
        assert ("sensitive-value-123" in str(info.value)) is leaks
    finally:
        await engine.dispose()
