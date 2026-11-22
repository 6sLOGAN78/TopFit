from collections.abc import Callable, Sequence

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
    secret = "s3cr3tpassw0rd"
    c = make_client({"DATABASE_URL": f"postgresql+psycopg://u:{secret}@127.0.0.1:1/none"})
    r = c.get("/api/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["db"]["ok"] is False
    assert secret not in r.text


class _CountingEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.dim = 384
        self.calls = 0
        self.fail = fail

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model-secret-detail")
        return [[0.0] * 384 for _ in texts]


def _patch_embedder(monkeypatch: pytest.MonkeyPatch, proxy: _CountingEmbedder) -> None:
    def build(*_args: object, **_kwargs: object) -> _CountingEmbedder:
        return proxy

    monkeypatch.setattr("ranker.main.build_embedder", build)


def test_readyz_runs_no_inference_per_request(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    make_client: Callable[[dict[str, str]], TestClient],
) -> None:
    proxy = _CountingEmbedder()
    _patch_embedder(monkeypatch, proxy)
    c = make_client({"DATABASE_URL": database_url})
    for _ in range(5):
        r = c.get("/api/readyz")
        assert r.status_code == 200
        assert r.json()["checks"]["embedding"] == {"ok": True, "dim": 384}
    assert proxy.calls == 1  # the startup self-check only (CR-01)


def test_readyz_embedding_selfcheck_failure(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    make_client: Callable[[dict[str, str]], TestClient],
    capsys: pytest.CaptureFixture[str],
) -> None:
    proxy = _CountingEmbedder(fail=True)
    _patch_embedder(monkeypatch, proxy)
    c = make_client({"DATABASE_URL": database_url})
    assert c.get("/api/healthz").status_code == 200
    r = c.get("/api/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["model"]["ok"] is True
    assert checks["embedding"] == {"ok": False, "dim": 0}
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "embedding_selfcheck_failed" in logs and "RuntimeError" in logs
    assert "model-secret-detail" not in logs and "model-secret-detail" not in r.text
    assert proxy.calls == 1
