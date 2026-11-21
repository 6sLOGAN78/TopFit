from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".")[:3])


def test_readyz_ok(client: TestClient) -> None:
    r = client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    pgv = body["checks"]["pgvector"]
    assert pgv["ok"] is True
    assert pgv["version"].startswith("0.8.")
    assert _version_tuple(pgv["version"]) >= (0, 8, 3)
    assert body["checks"]["model"]["ok"] is True
    assert body["checks"]["embedding"] == {"ok": True, "dim": 384}


def test_readyz_without_extension(
    database_url: str,
    pg_container: PostgresContainer,
    make_client: Callable[[dict[str, str]], TestClient],
) -> None:
    url = make_url(database_url)
    plain = f"postgresql://{url.username}:{url.password}@{url.host}:{url.port}/postgres"
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS noext")
        conn.execute("CREATE DATABASE noext")
    noext_url = url.set(database="noext").render_as_string(hide_password=False)
    c = make_client({"DATABASE_URL": noext_url})
    r = c.get("/api/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["db"]["ok"] is True
    assert checks["pgvector"]["ok"] is False


def test_readyz_model_load_failure(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    make_client: Callable[[dict[str, str]], TestClient],
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("model download failed")

    monkeypatch.setattr("ranker.main.build_embedder", boom)
    c = make_client({"DATABASE_URL": database_url})
    assert c.get("/api/healthz").status_code == 200
    r = c.get("/api/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["model"]["ok"] is False


def test_readyz_db_down_does_not_leak_password(
    make_client: Callable[[dict[str, str]], TestClient],
) -> None:
    secret = "s3cr3tpassw0rd"  # noqa: S105
    c = make_client({"DATABASE_URL": f"postgresql+psycopg://u:{secret}@127.0.0.1:1/none"})
    r = c.get("/api/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["db"]["ok"] is False
    assert secret not in r.text
