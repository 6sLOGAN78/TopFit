"""PoliteFetcher: D-15 rules for scraped (non-API) hosts.

Honest RankerBot User-Agent, robots.txt fetched at runtime (fail closed), at most one request per
``min_interval_s`` per host, and no retry after a refusal: a 403/429/5xx/challenge latches the host
for the rest of this fetcher's life. One fixed identity, one direct connection, and no cookies
kept between requests.
Exception text carries a status or reason only, never a URL.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ranker.ingest.http import USER_AGENT

# Narrower than the spike's pattern: bare "challenge" appears in ordinary job text.
CHALLENGE_RE = re.compile(
    r"captcha|access denied|are you a robot|unusual traffic|cf-challenge|challenge-platform"
    r"|attention required",
    re.I,
)
MAX_BODY_CHARS = 5_000_000


class RobotsDisallowed(Exception):
    """robots.txt (or its unavailability) forbids fetching the URL; no request was made for it."""

    def __init__(self, reason: str = "robots") -> None:
        super().__init__(reason)
        self.reason = reason


class FetchRefused(Exception):
    """The host refused or challenged us. Never retried."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason, status)
        self.reason = reason
        self.status = status

    def __str__(self) -> str:
        return f"{self.reason}:{self.status}" if self.status is not None else self.reason


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str = USER_AGENT,
        min_interval_s: float = 3.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ua = user_agent
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | bool] = {}  # False = fail closed
        self._latched: set[str] = set()
        self.requests_made = 0

    @staticmethod
    def _origin(url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}", parts.netloc

    @staticmethod
    def _same_origin(resp: httpx.Response, origin: str) -> bool:
        url = resp.url
        return f"{url.scheme}://{url.netloc.decode()}" == origin

    async def _spaced_get(self, url: str, host: str) -> httpx.Response:
        if self._min_interval > 0 and host in self._last:
            gap = self._clock() - self._last[host]
            if gap < self._min_interval:
                await self._sleep(self._min_interval - gap)
        self._last[host] = self._clock()
        self.requests_made += 1
        try:
            # Redirects are never followed: a Location could point past the robots/path guards.
            return await self._client.get(
                url, headers={"User-Agent": self._ua}, follow_redirects=False
            )
        finally:
            self._client.cookies.clear()  # nothing carried between requests
            self._last[host] = self._clock()

    async def _load_robots(self, origin: str, host: str) -> RobotFileParser | bool:
        cached = self._robots.get(origin)
        if cached is not None:
            return cached
        verdict: RobotFileParser | bool = False
        try:
            resp = await self._spaced_get(f"{origin}/robots.txt", host)
            if resp.status_code == 404:
                verdict = True  # no robots.txt: nothing is disallowed
            elif resp.status_code < 300 and self._same_origin(resp, origin):
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                verdict = parser
            # 3xx (never followed), 401/403/5xx and anything else: fail closed
        except httpx.HTTPError:
            verdict = False
        self._robots[origin] = verdict
        return verdict

    async def allowed(self, url: str) -> bool:
        origin, host = self._origin(url)
        if host in self._latched:
            return False
        verdict = await self._load_robots(origin, host)
        if isinstance(verdict, bool):
            return verdict
        return verdict.can_fetch(self._ua, url)

    async def get_text(self, url: str) -> str:
        origin, host = self._origin(url)
        if host in self._latched:
            raise FetchRefused("host_refused")
        if not await self.allowed(url):
            raise RobotsDisallowed()
        try:
            resp = await self._spaced_get(url, host)
        except httpx.HTTPError as exc:
            self._latched.add(host)
            raise FetchRefused(type(exc).__name__) from None
        status = resp.status_code
        if 300 <= status < 400:
            self._latched.add(host)
            raise FetchRefused("redirect", status)
        if not self._same_origin(resp, origin):
            self._latched.add(host)
            raise FetchRefused("origin_changed", status)
        if status in (403, 429) or status >= 500:
            self._latched.add(host)
            raise FetchRefused("FetchRefused", status)
        if status >= 400:
            raise FetchRefused("FetchRefused", status)
        body = resp.text[:MAX_BODY_CHARS]
        if CHALLENGE_RE.search(body):
            self._latched.add(host)
            raise FetchRefused("challenge", status)
        return body
