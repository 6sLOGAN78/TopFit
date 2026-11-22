import httpx
import pytest
import respx

from ranker.ingest.http import USER_AGENT, SourceHTTPError, build_client, get_json, redact_url

URL = "https://src.test/feed?app_key=SECRET"


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


@respx.mock
async def test_429_then_200() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})]
    )
    async with build_client() as c:
        assert await get_json(c, URL, sleep=Sleeps()) == {"ok": 1}
    assert route.call_count == 2


@respx.mock
async def test_503_five_times_raises() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    async with build_client() as c:
        with pytest.raises(SourceHTTPError) as ei:
            await get_json(c, URL, sleep=Sleeps())
    assert ei.value.status == 503
    assert route.call_count == 5
    assert "SECRET" not in str(ei.value) and "src.test" not in str(ei.value)
    assert "503" in str(ei.value)


@respx.mock
async def test_404_not_retried() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with build_client() as c:
        with pytest.raises(SourceHTTPError):
            await get_json(c, URL, sleep=Sleeps())
    assert route.call_count == 1


@respx.mock
async def test_retry_after_honoured_and_capped() -> None:
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "99999"}),
            httpx.Response(200, json=[]),
        ]
    )
    sleeps = Sleeps()
    async with build_client() as c:
        await get_json(c, URL, sleep=sleeps)
    assert sleeps.calls[0] >= 7
    assert sleeps.calls[1] == 120


@respx.mock
async def test_connect_error_retried() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=1)]
    )
    async with build_client() as c:
        assert await get_json(c, URL, sleep=Sleeps()) == 1
    assert route.call_count == 2


@respx.mock
async def test_on_attempt_counts_every_attempt() -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json=1)]
    )
    n: list[int] = []
    async with build_client() as c:
        await get_json(c, URL, sleep=Sleeps(), on_attempt=lambda: n.append(1))
    assert len(n) == 3
    respx.get(URL).mock(return_value=httpx.Response(404))
    n.clear()
    async with build_client() as c:
        with pytest.raises(SourceHTTPError):
            await get_json(c, URL, sleep=Sleeps(), on_attempt=lambda: n.append(1))
    assert len(n) == 1


@respx.mock
async def test_connect_error_counts_as_attempt() -> None:
    respx.get(URL).mock(side_effect=[httpx.ConnectError("x"), httpx.Response(200, json=1)])
    n: list[int] = []
    async with build_client() as c:
        await get_json(c, URL, sleep=Sleeps(), on_attempt=lambda: n.append(1))
    assert len(n) == 2


@respx.mock
async def test_user_agent_header() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=1))
    async with build_client() as c:
        await get_json(c, URL)
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT
    assert "topfit.duckdns.org" in USER_AGENT


def test_redact_url() -> None:
    assert (
        redact_url("https://x/y?app_id=1&app_key=SECRET&what=a")
        == "https://x/y?app_id=[REDACTED]&app_key=[REDACTED]&what=a"
    )
