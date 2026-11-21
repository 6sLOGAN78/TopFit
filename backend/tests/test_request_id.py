"""D-22: X-Request-ID honoured, regenerated when invalid, present on log lines."""

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    structlog.reset_defaults()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _buf() -> io.StringIO:
    from ranker.observability import configure_logging

    buf = io.StringIO()
    configure_logging(level="DEBUG", stream=buf)
    return buf


def _request_line(buf: io.StringIO) -> dict[str, Any]:
    lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    req = [ln for ln in lines if ln.get("event") == "request"]
    assert len(req) == 1
    return req[0]


def test_valid_request_id_honoured(client: Any) -> None:
    buf = _buf()
    rid = str(uuid.uuid4())
    resp = client.get("/api/healthz", headers={"X-Request-ID": rid})
    assert resp.headers["X-Request-ID"] == rid
    assert _request_line(buf)["request_id"] == rid


def test_invalid_request_id_regenerated(client: Any) -> None:
    buf = _buf()
    resp = client.get("/api/healthz", headers={"X-Request-ID": "bad-INJECT"})
    got = resp.headers["X-Request-ID"]
    assert got != "bad-INJECT"
    assert uuid.UUID(got).version == 4
    assert "bad-INJECT" not in buf.getvalue()
    assert _request_line(buf)["request_id"] == got


def test_request_id_generated_when_absent(client: Any) -> None:
    _buf()
    resp = client.get("/api/healthz")
    assert uuid.UUID(resp.headers["X-Request-ID"]).version == 4
