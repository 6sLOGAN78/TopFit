"""SEC-07: redaction proven on rendered JSON output (never capture_logs)."""

import io
import json
import logging
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


def _configure() -> io.StringIO:
    from ranker.observability import configure_logging

    buf = io.StringIO()
    configure_logging(level="DEBUG", stream=buf)
    return buf


def _lines(buf: io.StringIO) -> list[dict[str, Any]]:
    out = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in out]


def test_structlog_nested_redaction() -> None:
    buf = _configure()
    structlog.get_logger("t").info(
        "evt",
        resume_text="SECRET_RESUME_123",
        email="a.person@example.com",
        nested={"password": "hunter2pw", "items": [{"cookie": "c=SECRETCOOKIE"}]},
    )
    raw = buf.getvalue()
    _lines(buf)
    for secret in ("SECRET_RESUME_123", "a.person@example.com", "hunter2pw", "SECRETCOOKIE"):
        assert secret not in raw
    assert "[REDACTED]" in raw


def test_email_in_free_text() -> None:
    buf = _configure()
    structlog.get_logger("t").info("signup from bob.smith@example.org")
    raw = buf.getvalue()
    assert "[EMAIL]" in raw
    assert "bob.smith@example.org" not in raw


def test_stdlib_records_and_extras() -> None:
    buf = _configure()
    logging.getLogger("uvicorn.error").warning("x %s", "carol@example.net")
    logging.getLogger("sqlalchemy.engine").warning("q", extra={"content": "SECRET_MSG_456"})
    raw = buf.getvalue()
    assert len(_lines(buf)) >= 2
    assert "carol@example.net" not in raw
    assert "SECRET_MSG_456" not in raw


def _boom() -> None:
    resume_text = "LOCAL_SECRET_789"
    raise ValueError(len(resume_text))


def test_exception_locals_not_logged() -> None:
    buf = _configure()
    log = structlog.get_logger("t")
    try:
        _boom()
    except ValueError:
        log.exception("boom")
    raw = buf.getvalue()
    assert "ValueError" in raw
    assert "boom" in raw
    assert "LOCAL_SECRET_789" not in raw
    _lines(buf)


def test_request_log_line_has_no_secrets(client: Any) -> None:
    buf = _configure()
    for name in ("httpx", "httpx2"):  # test client's own URL log (anthropic pulls in httpx2)
        logging.getLogger(name).setLevel(logging.WARNING)
    resp = client.get(
        "/api/healthz?q=QUERY_SECRET_C",
        headers={"Authorization": "Bearer TOKEN_SECRET_A", "Cookie": "rk_ping=COOKIE_SECRET_B"},
    )
    assert resp.status_code == 200
    raw = buf.getvalue()
    for secret in ("TOKEN_SECRET_A", "COOKIE_SECRET_B", "QUERY_SECRET_C"):
        assert secret not in raw
    req = [ln for ln in _lines(buf) if ln.get("event") == "request"]
    assert len(req) == 1
    assert req[0]["method"] == "GET"
    assert req[0]["path"] == "/api/healthz"
    assert req[0]["status"] == 200
    assert isinstance(req[0]["duration_ms"], int | float)
