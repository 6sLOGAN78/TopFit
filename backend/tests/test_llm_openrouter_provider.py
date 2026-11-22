import json
import os
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm import cost
from ranker.llm.client import build_gateway
from ranker.llm.errors import LLMAuthError, LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import openrouter
from ranker.llm.providers.openrouter import OpenRouterProvider
from ranker.llm.schemas import JobEnrichment
from ranker.settings import get_settings

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = "sk-or-canary-9f8e7d6c5b4a"
GOOD = {"remote_eligibility": "india", "seniority": "mid", "employment_type": "full_time"}


def _ok(content: str | None = None, **extra: Any) -> httpx.Response:
    body = {
        "model": "m:free",
        "choices": [
            {
                "message": {"content": content if content is not None else json.dumps(GOOD)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 9},
        **extra,
    }
    return httpx.Response(200, json=body)


async def _parse(p: OpenRouterProvider) -> Any:
    return await p.parse(
        model="m:free",
        schema=JobEnrichment,
        system="SECRET-PROMPT-TEXT",
        messages=[{"role": "user", "content": "SECRET-JOB-TEXT"}],
        max_tokens=50,
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)

    monkeypatch.setattr(openrouter.asyncio, "sleep", fake_sleep)
    return delays


@respx.mock
async def test_success_request_shape_and_usage() -> None:
    route = respx.post(URL).mock(return_value=_ok())
    res = await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    assert res.parsed == JobEnrichment.model_validate(GOOD)
    assert (res.input_tokens, res.output_tokens) == (12, 9)
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["http-referer"] == "https://topfit.duckdns.org"
    assert req.headers["x-title"] == "Ranker"
    sent = json.loads(req.content)
    rf = sent["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == JobEnrichment.model_json_schema()
    assert sent["messages"][0] == {"role": "system", "content": "SECRET-PROMPT-TEXT"}


@respx.mock
async def test_reasoning_disabled_in_request() -> None:
    route = respx.post(URL).mock(return_value=_ok())
    await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    sent = json.loads(route.calls.last.request.content)
    assert sent["reasoning"] == {"enabled": False}


@respx.mock
@pytest.mark.parametrize("content", ["not json", "", json.dumps({**GOOD, "seniority": "wizard"})])
async def test_invalid_output(content: str) -> None:
    respx.post(URL).mock(return_value=_ok(content))
    with pytest.raises(LLMOutputInvalid):
        await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))


@respx.mock
async def test_malformed_envelope_is_invalid() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMOutputInvalid):
        await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))


@respx.mock
async def test_429_retry_after_honoured_and_capped(no_sleep: list[float]) -> None:
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(429, headers={"retry-after": "999"}),
            _ok(),
        ]
    )
    res = await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    assert res.parsed.seniority == "mid"
    assert no_sleep == [3.0, 10.0]


@respx.mock
async def test_429_exhausted_is_unavailable() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(LLMUnavailable):
        await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    assert route.call_count == 3


@respx.mock
async def test_500_retried_then_succeeds() -> None:
    route = respx.post(URL).mock(side_effect=[httpx.Response(500), _ok()])
    await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    assert route.call_count == 2


@respx.mock
async def test_transport_error_is_unavailable() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(LLMUnavailable):
        await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))


@respx.mock
@pytest.mark.parametrize("code", [401, 403])
async def test_auth_errors_not_retried(code: int) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(code))
    with pytest.raises(LLMAuthError):
        await _parse(OpenRouterProvider(SecretStr(KEY), 5.0))
    assert route.call_count == 1


@respx.mock
async def test_missing_key_defers_without_network() -> None:
    route = respx.post(URL).mock(return_value=_ok())
    p = OpenRouterProvider(None, 5.0)
    assert p.available() is False
    with pytest.raises(LLMUnavailable):
        await _parse(p)
    assert OpenRouterProvider(SecretStr(""), 5.0).available() is False
    assert route.call_count == 0


@respx.mock
async def test_key_and_prompts_never_logged(capsys: pytest.CaptureFixture[str]) -> None:
    respx.post(URL).mock(side_effect=[httpx.Response(401), _ok("not json")])
    p = OpenRouterProvider(SecretStr(KEY), 5.0)
    for _ in range(2):
        with pytest.raises((LLMOutputInvalid, LLMAuthError)) as exc:
            await _parse(p)
        assert KEY not in repr(exc.value) and "SECRET-JOB-TEXT" not in repr(exc.value)
    out = capsys.readouterr()
    assert KEY not in out.out + out.err
    assert "SECRET-JOB-TEXT" not in out.out + out.err
    assert KEY not in repr(p._api_key)  # pyright: ignore[reportPrivateUsage]


def test_free_models_cost_zero() -> None:
    assert cost.cost_usd("any/model:free", input_tokens=1000, output_tokens=1000) == Decimal(0)
    assert cost.worst_case_cost("any/model:free", est_input_tokens=1000, max_tokens=1000) == 0


async def test_build_gateway_openrouter(engine: AsyncEngine) -> None:
    base = get_settings().model_copy(update={"llm_provider": "openrouter"})
    no_key = base.model_copy(update={"openrouter_api_key": None})
    assert build_gateway(engine, no_key).available() is False
    keyed = base.model_copy(update={"openrouter_api_key": SecretStr(KEY)})
    gw = build_gateway(engine, keyed)
    assert gw.available() is True
    assert gw._model_for("fast") == gw._model_for("agent") == keyed.openrouter_model  # pyright: ignore[reportPrivateUsage]


@pytest.mark.llm
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OPENROUTER_API_KEY")
async def test_live_job_enrichment(engine: AsyncEngine) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": SecretStr(os.environ["OPENROUTER_API_KEY"]),
        }
    )
    out = await build_gateway(engine, settings).structured(
        purpose="job_enrich",
        tier="fast",
        schema=JobEnrichment,
        system="Classify the job posting.",
        messages=[
            {"role": "user", "content": "Senior Python engineer, remote worldwide, full time."}
        ],
        max_tokens=400,
        est_input_tokens=200,
    )
    assert isinstance(out, JobEnrichment)
