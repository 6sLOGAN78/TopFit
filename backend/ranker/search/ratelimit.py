"""Guest search rate limits on a Postgres fixed-window counter table (SEC-04).

Two buckets per new search: a signed ``rk_gid`` guest cookie and an HMAC of the client IP
(never the raw IP). Cursor (load-more) requests are not counted.
"""

import hashlib
import hmac
import ipaddress
import math
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.api.errors import RateLimited
from ranker.settings import Settings

COOKIE = "rk_gid"
COOKIE_MAX_AGE = 30 * 24 * 3600
WINDOW = timedelta(hours=1)

_HIT_SQL = text(
    "INSERT INTO rate_limit_counters (bucket, window_start, count) "
    "VALUES (:bucket, date_bin(CAST(:window AS interval), :now, TIMESTAMPTZ '2000-01-01'), 1) "
    "ON CONFLICT (bucket, window_start) DO UPDATE SET count = rate_limit_counters.count + 1 "
    "RETURNING count, window_start"
)


async def _hit(engine: AsyncEngine, bucket: str, now: datetime) -> tuple[int, datetime]:
    async with engine.begin() as conn:
        row = (
            await conn.execute(_HIT_SQL, {"bucket": bucket, "window": "1 hour", "now": now})
        ).one()
    return int(row[0]), row[1]


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="rk-gid-v1")


def _guest_id(request: Request, settings: Settings) -> UUID:
    if raw := request.cookies.get(COOKIE):
        try:
            return UUID(hex=str(_serializer(settings).loads(raw, max_age=COOKIE_MAX_AGE)["gid"]))
        except BadSignature, KeyError, TypeError, ValueError:
            pass
    return uuid.uuid4()


def client_ip(request: Request, settings: Settings) -> str:
    """Real client IP. Trust boundary: X-Forwarded-For is honoured only when
    RATE_LIMIT_TRUST_XFF is set (prod, where Caddy is the only published port and
    overwrites the header for untrusted clients). Only the last hop, the one Caddy added,
    is used; earlier entries are client-controlled."""
    if settings.rate_limit_trust_xff:
        last = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def normalize_ip(ip: str) -> str:
    """IPv4 stays whole; IPv6 is cut to its /64; IPv4-mapped IPv6 counts as the IPv4."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
    return str(addr)


def _ip_bucket(settings: Settings, ip: str) -> str:
    ip = normalize_ip(ip)
    key = settings.session_secret.get_secret_value().encode()
    return "search:ip:" + hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


async def rate_limit_search(request: Request, response: Response) -> UUID | None:
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled or request.query_params.get("cursor"):
        request.state.guest_id = None
        return None
    gid = _guest_id(request, settings)
    request.state.guest_id = gid
    response.set_cookie(
        COOKIE,
        _serializer(settings).dumps({"gid": gid.hex}),
        max_age=COOKIE_MAX_AGE,
        path="/api",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    engine: AsyncEngine = request.app.state.engine
    now = datetime.now(UTC)
    ip = client_ip(request, settings)
    checks = (
        (f"search:g:{gid}", settings.rate_limit_guest_per_hour),
        (_ip_bucket(settings, ip), settings.rate_limit_ip_per_hour),
    )
    worst: int | None = None
    for bucket, limit in checks:
        count, start = await _hit(engine, bucket, now)
        if count > limit:
            wait = max(1, math.ceil((start + WINDOW - now).total_seconds()))
            worst = max(worst or 0, wait)
    if worst is not None:
        raise RateLimited(retry_after=worst)
    return gid
