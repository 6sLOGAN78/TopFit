"""Source credentials must not leak through logs, run records or errors (T-02-33)."""

import io
import json
import logging
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

import httpx
import pytest
import respx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest import http as ingest_http
from ranker.ingest import pipeline
from ranker.ingest.sources import BaseSourceConfig
from ranker.observability import configure_logging
from ranker.settings import Settings

CANARY = "KEY-LEAK-CANARY-123"


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved = {n: logging.getLogger(n).level for n in ("httpx", "httpcore", "uvicorn.access")}
    yield
    structlog.reset_defaults()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def _configure() -> io.StringIO:
    buf = io.StringIO()
    configure_logging(level="DEBUG", stream=buf)
    return buf


def test_http_loggers_are_quiet() -> None:
    _configure()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_query_secrets_masked_in_values_and_text() -> None:
    buf = _configure()
    structlog.get_logger("t").info(
        "evt",
        url="https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=ID123&app_key=KEY456&x=1",
        detail="request failed for app_key=KEY456 retrying",
        app_key="KEY456",
        nested={"headers": [{"x-admin-token": "ADMINTOK"}], "anthropic_api_key": "sk-ant-1"},
        app_id="ID123",
        api_key="APIKEY789",
        admin_token="ADMINTOK2",
    )
    raw = buf.getvalue()
    for secret in ("ID123", "KEY456", "ADMINTOK", "sk-ant-1", "APIKEY789", "ADMINTOK2"):
        assert secret not in raw
    assert "x=1" in raw  # non-secret params survive
    assert "[REDACTED]" in raw


@respx.mock
@pytest.mark.usefixtures("clean_db")
async def test_failing_adzuna_run_never_leaks_key(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf = _configure()
    # even the library loggers must not emit URLs; force the worst case at INFO level
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = BaseSourceConfig.model_validate(
        {
            "expiry_policy": "ttl",
            "results_per_page": 50,
            "min_interval_seconds": 0,
            "quota": {"calls_per_day": 50, "calls_per_month": 500},
            "queries": [{"name": "it", "category": "it-jobs", "max_pages": 3}],
        }
    )
    monkeypatch.setattr(pipeline, "_source_config", lambda _source: cfg)

    real = ingest_http.get_json

    async def fast(*args: Any, **kw: Any) -> object:
        async def _nosleep(_: float) -> None:
            return None

        kw["sleep"] = _nosleep
        return await real(*args, **kw)

    monkeypatch.setattr("ranker.ingest.sources.adzuna.get_json", fast)
    route = respx.get(url__startswith="https://api.adzuna.com/").mock(
        return_value=httpx.Response(500, text=f"upstream error echo {CANARY}")
    )
    settings = Settings(
        database_url="postgresql+psycopg://x/x",
        session_secret="s",  # pyright: ignore[reportArgumentType]
        adzuna_app_id="ID-CANARY-9",  # pyright: ignore[reportArgumentType]
        adzuna_app_key=CANARY,  # pyright: ignore[reportArgumentType]
    )

    summary = await pipeline.run_ingest(engine, settings, "adzuna")

    assert route.call_count >= 1
    assert summary.status in {"partial", "failed"}
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT errors::text, notes::text, status FROM ingest_runs ORDER BY id DESC")
            )
        ).one()
    cli_json = json.dumps(asdict(summary))
    rendered = buf.getvalue()
    for blob in (rendered, row[0], row[1], cli_json):
        assert CANARY not in blob
        assert "ID-CANARY-9" not in blob
    assert row[2] in {"partial", "failed"}
