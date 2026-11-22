import httpx
import pytest
import respx

from ranker.ingest.http import USER_AGENT
from ranker.ingest.polite import FetchRefused, PoliteFetcher, RobotsDisallowed

HOST = "https://internshala.com"
# urllib.robotparser has no wildcard support, so use literal prefixes here; the Internshala
# adapter's path guard (not robots) is what rejects query strings.
ROBOTS = "User-agent: *\nDisallow: /job/details/\nDisallow: /search\n"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _fetcher(client: httpx.AsyncClient, clock: Clock, interval: float = 3.0) -> PoliteFetcher:
    return PoliteFetcher(client, min_interval_s=interval, sleep=clock.sleep, clock=clock)


@respx.mock
async def test_robots_once_ua_and_spacing() -> None:
    robots = respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    page = respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    respx.get("https://other.example/robots.txt").mock(return_value=httpx.Response(404))
    other = respx.get("https://other.example/x").mock(return_value=httpx.Response(404))
    clock = Clock()
    async with httpx.AsyncClient() as client:
        f = _fetcher(client, clock)
        assert await f.get_text(f"{HOST}/jobs/python-jobs/") == "<html>ok</html>"
        await f.get_text(f"{HOST}/jobs/python-jobs/")
        assert robots.call_count == 1 and page.call_count == 2
        # robots (t0) -> page (3 s later) -> page (3 s later); the first request never waits
        assert clock.sleeps == [3.0, 3.0]
        clock.sleeps.clear()
        with pytest.raises(FetchRefused):
            await f.get_text("https://other.example/x")  # robots 404 -> allowed, page 404
        # the other host's own robots->page gap is the only wait: internshala traffic adds none
        assert clock.sleeps == [3.0]
        assert other.call_count == 1
    for call in [*robots.calls, *page.calls]:
        assert call.request.headers["user-agent"] == USER_AGENT
        assert "cookie" not in call.request.headers
    assert f.requests_made == 5  # 2 robots + 3 pages


@respx.mock
async def test_disallowed_url_makes_no_request() -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    detail = respx.get(f"{HOST}/job/details/x").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        f = _fetcher(client, Clock())
        with pytest.raises(RobotsDisallowed):
            await f.get_text(f"{HOST}/job/details/x")
        assert not await f.allowed(f"{HOST}/search/python")
        assert await f.allowed(f"{HOST}/jobs/python-jobs/")
    assert detail.call_count == 0


@pytest.mark.parametrize(
    ("robots_response", "allowed"),
    [
        (httpx.Response(404), True),
        (httpx.Response(401), False),
        (httpx.Response(403), False),
        (httpx.Response(500), False),
        (httpx.ConnectError("boom"), False),
    ],
)
@respx.mock
async def test_robots_failure_modes(
    robots_response: httpx.Response | Exception, allowed: bool
) -> None:
    if isinstance(robots_response, Exception):
        respx.get(f"{HOST}/robots.txt").mock(side_effect=robots_response)
    else:
        respx.get(f"{HOST}/robots.txt").mock(return_value=robots_response)
    async with httpx.AsyncClient() as client:
        f = _fetcher(client, Clock())
        assert await f.allowed(f"{HOST}/jobs/python-jobs/") is allowed


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(429, headers={"retry-after": "1"}),
        httpx.Response(503),
        httpx.Response(200, text="<html>Please solve this CAPTCHA</html>"),
    ],
)
@respx.mock
async def test_refusal_latches_host_without_retry(response: httpx.Response) -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    page = respx.get(f"{HOST}/jobs/python-jobs/").mock(return_value=response)
    async with httpx.AsyncClient() as client:
        f = _fetcher(client, Clock())
        with pytest.raises(FetchRefused) as info:
            await f.get_text(f"{HOST}/jobs/python-jobs/")
        assert "internshala" not in str(info.value)
        with pytest.raises(FetchRefused, match="host_refused"):
            await f.get_text(f"{HOST}/jobs/java-jobs/")
    assert page.call_count == 1 and f.requests_made == 2


@respx.mock
async def test_other_4xx_is_refused_but_does_not_latch() -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    respx.get(f"{HOST}/jobs/gone-jobs/").mock(return_value=httpx.Response(404))
    ok = respx.get(f"{HOST}/jobs/python-jobs/").mock(return_value=httpx.Response(200, text="ok"))
    async with httpx.AsyncClient() as client:
        f = _fetcher(client, Clock())
        with pytest.raises(FetchRefused):
            await f.get_text(f"{HOST}/jobs/gone-jobs/")
        assert await f.get_text(f"{HOST}/jobs/python-jobs/") == "ok"
    assert ok.call_count == 1


@respx.mock
async def test_ordinary_challenge_word_in_job_text_is_not_a_refusal() -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(200, text="<p>Solve real challenges</p>")
    )
    async with httpx.AsyncClient() as client:
        assert "challenges" in await _fetcher(client, Clock()).get_text(f"{HOST}/jobs/python-jobs/")


@pytest.mark.parametrize(
    ("status", "location"),
    [
        (301, f"{HOST}/job/details/x1"),
        (302, "https://evil.example/jobs/python-jobs/"),
        (302, f"{HOST}/jobs/java-jobs/"),
    ],
)
@respx.mock
async def test_redirect_is_refused_and_never_followed(status: int, location: str) -> None:
    respx.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    page = respx.get(f"{HOST}/jobs/python-jobs/").mock(
        return_value=httpx.Response(status, headers={"location": location})
    )
    target = respx.get(location).mock(return_value=httpx.Response(200, text="target"))
    async with httpx.AsyncClient(follow_redirects=True) as client:  # client default must not matter
        f = _fetcher(client, Clock())
        with pytest.raises(FetchRefused) as info:
            await f.get_text(f"{HOST}/jobs/python-jobs/")
        assert info.value.status == status
        with pytest.raises(FetchRefused, match="host_refused"):
            await f.get_text(f"{HOST}/jobs/web-development-jobs/")
    if "java-jobs" not in location:
        assert target.call_count == 0
    assert page.call_count == 1


@respx.mock
async def test_robots_redirect_fails_closed() -> None:
    respx.get(f"{HOST}/robots.txt").mock(
        return_value=httpx.Response(301, headers={"location": "https://evil.example/robots.txt"})
    )
    evil = respx.get("https://evil.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        assert not await _fetcher(client, Clock()).allowed(f"{HOST}/jobs/python-jobs/")
    assert evil.call_count == 0
