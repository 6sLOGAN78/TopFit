"""Structured JSON logging with request IDs and PII redaction (SEC-07, OPS-04).

Rule: never log user text (resume text, chat messages) under the ``message`` or
``content`` keys, or anywhere else. Those keys are masked by :func:`redact` as a safety
net, and the structlog event name lives under ``event`` for the same reason.

Every record, whether it comes from structlog, the stdlib (uvicorn, SQLAlchemy, Alembic)
or loguru (fastembed), is rendered by one ``ProcessorFormatter`` so that the redaction
processor sees all of it.
"""

import logging
import re
import sys
import time
from typing import Any, TextIO, cast

import structlog
from asgi_correlation_id import correlation_id
from structlog.types import EventDict, Processor, WrappedLogger

REDACT_KEYS: frozenset[str] = frozenset(
    {
        "resume_text",
        "message",
        "content",
        "email",
        "password",
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
    }
)
MASK = "[REDACTED]"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        items = cast("dict[Any, Any]", value)
        return {k: MASK if str(k).lower() in REDACT_KEYS else _scrub(v) for k, v in items.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in cast("list[Any]", value)]
    if isinstance(value, str):
        return EMAIL_RE.sub("[EMAIL]", value)
    return value


def redact(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: mask sensitive keys at any depth and emails in free text."""
    return _scrub(event_dict)  # type: ignore[no-any-return]


def add_request_id(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    rid = correlation_id.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


_LOGURU_LEVELS = {
    "TRACE": logging.DEBUG,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SUCCESS": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _forward_loguru() -> None:
    try:
        from loguru import logger as loguru_logger  # pyright: ignore[reportMissingImports]
    except ImportError:
        return

    def _sink(message: Any) -> None:
        record = message.record
        level = _LOGURU_LEVELS.get(record["level"].name, logging.INFO)
        logging.getLogger("fastembed").log(level, "%s", record["message"])

    loguru_logger.remove()
    loguru_logger.add(_sink, level="INFO", format="{message}")


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Configure structlog + stdlib + loguru to emit single-line redacted JSON. Idempotent."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            redact,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _forward_loguru()


class RequestLogMiddleware:
    """Pure ASGI middleware: one ``request`` log line per HTTP request (path only)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.get_logger("ranker.request").info(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
