import os
from collections.abc import Callable, Iterator
from pathlib import Path

# Must be set before any `ranker` import reads settings.
os.environ.setdefault("SESSION_SECRET", "test-secret-" + "x" * 52)
os.environ.setdefault("EMBEDDER", "fake")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("COOKIE_SECURE", "true")

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from ranker.main import create_app
from ranker.settings import get_settings

PG_IMAGE = "pgvector/pgvector:0.8.7-pg18-trixie"
BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(PG_IMAGE, driver="psycopg") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> Iterator[str]:
    url = pg_container.get_connection_url()
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    # Sync fixture, so asyncio.run() inside migrations/env.py is legal.
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    yield url
    os.environ.pop("DATABASE_URL", None)
    get_settings.cache_clear()


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[dict[str, str]], TestClient]]:
    """Build a TestClient with env overrides; clients are closed on teardown."""
    opened: list[TestClient] = []

    def _make(env_overrides: dict[str, str]) -> TestClient:
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        c = TestClient(create_app(), base_url="https://testserver")
        c.__enter__()
        opened.append(c)
        return c

    yield _make
    for c in opened:
        c.__exit__(None, None, None)
    get_settings.cache_clear()
