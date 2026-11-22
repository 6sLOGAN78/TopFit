"""Retrying HTTP client for source adapters (ING-07, D-15).

Errors carry only a status and an error type; request URLs (which may hold API keys)
never reach exception text or logs.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

USER_AGENT = "RankerBot/0.1 (+https://topfit.duckdns.org; portfolio project)"
MAX_RETRY_AFTER_SECONDS = 120.0
_SECRET_KEYS = frozenset({"app_id", "app_key", "api_key", "token"})
_exp_wait = wait_random_exponential(multiplier=1, max=60)


class SourceHTTPError(Exception):
    def __init__(
        self, status: int | None, error_type: str, retry_after: float | None = None
    ) -> None:
        super().__init__(error_type, status)
        self.status = status
        self.error_type = error_type
        self.retry_after = retry_after

    def __str__(self) -> str:
        return f"{self.error_type}:{self.status}" if self.status is not None else self.error_type


def build_client(*, timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = "&".join(f"{k}=[REDACTED]" if k in _SECRET_KEYS else f"{k}={v}" for k, v in pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _is_retryable(exc: BaseException) -> bool:
    if not isinstance(exc, SourceHTTPError):
        return False
    return exc.status is None or exc.status == 429 or exc.status >= 500


def _wait(state: RetryCallState) -> float:
    outcome = state.outcome
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, SourceHTTPError) and exc.retry_after is not None:
        return exc.retry_after
    return _exp_wait(state)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int] | None = None,
    max_attempts: int = 5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_attempt: Callable[[], None] | None = None,
) -> object:
    """GET ``url`` and return decoded JSON; ``on_attempt`` fires before every request."""

    async def _once() -> object:
        if on_attempt is not None:
            on_attempt()
        try:
            resp = await client.get(url, params=params)
        except httpx.TransportError as exc:
            raise SourceHTTPError(None, type(exc).__name__) from None
        if resp.status_code >= 400:
            raise SourceHTTPError(
                resp.status_code,
                "HTTPStatusError",
                _parse_retry_after(resp.headers.get("retry-after")),
            )
        try:
            return resp.json()
        except ValueError:
            raise SourceHTTPError(resp.status_code, "InvalidJSON") from None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=_wait,
        retry=retry_if_exception(_is_retryable),
        sleep=sleep,
        reraise=True,
    ):
        with attempt:
            return await _once()
    raise AssertionError("unreachable")  # pragma: no cover
