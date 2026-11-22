from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ranker.search.ratelimit import normalize_ip

PARAMS = {"q": "!!!"}  # cleans to something trivial; the limiter runs before the search


@pytest.fixture(autouse=True)
def _truncate(database_url: str) -> None:
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE rate_limit_counters"))
    eng.dispose()


def _get(c: TestClient, **extra: str) -> int:
    return c.get("/api/jobs/search", params={**PARAMS, **extra}).status_code


def test_unlogged_table(database_url: str) -> None:
    eng = create_engine(database_url)
    with eng.connect() as conn:
        persistence = conn.execute(
            text("SELECT relpersistence FROM pg_class WHERE relname = 'rate_limit_counters'")
        ).scalar_one()
    eng.dispose()
    assert persistence == "u"


def test_guest_limit_429_with_envelope(client: TestClient) -> None:
    for _ in range(60):
        assert _get(client) == 200
    cookie = client.cookies.get("rk_gid")
    assert cookie
    r = client.get("/api/jobs/search", params=PARAMS)
    assert r.status_code == 429
    retry = int(r.headers["Retry-After"])
    assert 1 <= retry <= 3600
    body = r.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["details"]["retry_after"] == retry


def test_cookie_attributes(client: TestClient) -> None:
    r = client.get("/api/jobs/search", params=PARAMS)
    header = r.headers["set-cookie"].lower()
    assert "rk_gid=" in header
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=lax" in header
    assert "path=/api" in header


def test_window_resets(client: TestClient) -> None:
    for _ in range(60):
        assert _get(client) == 200
    assert _get(client) == 429
    with time_machine.travel(datetime.now(UTC) + timedelta(hours=2), tick=True):
        assert _get(client) == 200


def test_ip_bucket_survives_cookie_clearing(
    make_client: Callable[[dict[str, str]], TestClient], database_url: str
) -> None:
    c = make_client({"RATE_LIMIT_IP_PER_HOUR": "5"})
    for _ in range(5):
        assert _get(c) == 200
        c.cookies.clear()
    assert _get(c) == 429
    eng = create_engine(database_url)
    with eng.connect() as conn:
        buckets = [r[0] for r in conn.execute(text("SELECT bucket FROM rate_limit_counters"))]
    eng.dispose()
    assert any(b.startswith("search:ip:") for b in buckets)
    assert not any("testclient" in b or "127.0.0.1" in b for b in buckets)


def test_cursor_requests_not_counted(
    make_client: Callable[[dict[str, str]], TestClient],
) -> None:
    c = make_client({"RATE_LIMIT_GUEST_PER_HOUR": "2"})
    assert _get(c) == 200
    for _ in range(10):
        assert c.get("/api/jobs/search", params={**PARAMS, "cursor": "abc"}).status_code != 429
    assert _get(c) == 200
    assert _get(c) == 429


def test_disabled(make_client: Callable[[dict[str, str]], TestClient]) -> None:
    c = make_client({"RATE_LIMIT_ENABLED": "false", "RATE_LIMIT_GUEST_PER_HOUR": "2"})
    assert all(_get(c) == 200 for _ in range(100))


def test_invalid_cookie_gets_new_id(client: TestClient) -> None:
    r = client.get("/api/jobs/search", params=PARAMS, headers={"Cookie": "rk_gid=garbage"})
    assert r.status_code == 200
    assert "rk_gid=" in r.headers["set-cookie"]
    assert "rk_gid=garbage" not in r.headers["set-cookie"]


def _ip_buckets(database_url: str) -> set[str]:
    eng = create_engine(database_url)
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT bucket FROM rate_limit_counters"))
        out = {r[0] for r in rows if r[0].startswith("search:ip:")}
    eng.dispose()
    return out


def test_xff_ignored_when_untrusted(client: TestClient, database_url: str) -> None:
    client.get("/api/jobs/search", params=PARAMS, headers={"X-Forwarded-For": "9.9.9.9"})
    first = _ip_buckets(database_url)
    client.cookies.clear()
    client.get("/api/jobs/search", params=PARAMS, headers={"X-Forwarded-For": "8.8.8.8"})
    assert _ip_buckets(database_url) == first
    assert len(first) == 1


def test_xff_last_hop_when_trusted(
    make_client: Callable[[dict[str, str]], TestClient], database_url: str
) -> None:
    c = make_client({"RATE_LIMIT_TRUST_XFF": "true"})
    c.get("/api/jobs/search", params=PARAMS, headers={"X-Forwarded-For": "1.1.1.1, 5.5.5.5"})
    a = _ip_buckets(database_url)
    c.cookies.clear()
    # spoofed leading hops must not change the bucket; the last hop does
    c.get("/api/jobs/search", params=PARAMS, headers={"X-Forwarded-For": "2.2.2.2, 5.5.5.5"})
    assert _ip_buckets(database_url) == a
    c.cookies.clear()
    c.get("/api/jobs/search", params=PARAMS, headers={"X-Forwarded-For": "5.5.5.6"})
    assert len(_ip_buckets(database_url)) == 2


def test_normalize_ip() -> None:
    assert normalize_ip("203.0.113.7") == "203.0.113.7"
    assert normalize_ip("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == "2001:db8:1:2::"
    assert normalize_ip("2001:db8:1:2::1") == normalize_ip("2001:db8:1:2:ffff::9")
    assert normalize_ip("2001:db8:1:3::1") != normalize_ip("2001:db8:1:2::1")
    assert normalize_ip("::ffff:203.0.113.7") == "203.0.113.7"
    assert normalize_ip("garbage") == "garbage"
