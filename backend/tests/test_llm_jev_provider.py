"""JevProvider: OpenRouter transport with the typesafe/jev-router model (respx only)."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from ranker.llm.errors import LLMAuthError, LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import openrouter
from ranker.llm.providers.jev import DEFAULT_MODEL, JevProvider
from ranker.llm.schemas import QueryParseFallback, lint_schema
from ranker.settings import Settings

URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = "sk-or-canary-jev-1a2b3c4d5e6f"
ANSWER = {
    "city": "Bengaluru",
    "work_mode": "remote_or_city",
    "seniority": "senior",
    "employment_type": "unspecified",
    "min_salary_lpa": "10",
    "role_family": "backend",
}


def _ok(routed: str = "openai/gpt-6-luna", cost: float | None = 0.0000246) -> httpx.Response:
    usage: dict[str, Any] = {"prompt_tokens": 81, "completion_tokens": 33}
    if cost is not None:
        usage["cost"] = cost
    return httpx.Response(
        200,
        json={
            "model": routed,
            "choices": [{"message": {"content": json.dumps(ANSWER)}, "finish_reason": "stop"}],
            "usage": usage,
        },
    )


async def _parse(p: JevProvider) -> Any:
    return await p.parse(
        model=DEFAULT_MODEL,
        schema=QueryParseFallback,
        system="SYS",
        messages=[{"role": "user", "content": "SECRET-QUERY-TEXT"}],
        max_tokens=150,
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(openrouter.asyncio, "sleep", fake_sleep)


def test_fallback_schema_is_enum_only_and_lint_clean() -> None:
    assert lint_schema(QueryParseFallback) == []
    props = QueryParseFallback.model_json_schema()["properties"]
    assert set(props) == {
        "city",
        "work_mode",
        "seniority",
        "employment_type",
        "min_salary_lpa",
        "role_family",
    }
    for spec in props.values():
        assert spec["type"] == "string" and spec["enum"]
    assert "Bengaluru" in props["city"]["enum"] and "none" in props["city"]["enum"]
    assert props["min_salary_lpa"]["enum"][:2] == ["none", "3"]


def test_fallback_schema_rejects_values_outside_enum() -> None:
    with pytest.raises(ValueError):
        QueryParseFallback.model_validate({**ANSWER, "city": "Atlantis"})
    with pytest.raises(ValueError):
        QueryParseFallback.model_validate({**ANSWER, "seniority": "wizard"})


@respx.mock
async def test_request_uses_openrouter_key_router_model_and_reasoning_off() -> None:
    route = respx.post(URL).mock(return_value=_ok())
    res = await _parse(JevProvider(SecretStr(KEY), 5.0))
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    sent = json.loads(req.content)
    assert sent["model"] == "typesafe/jev-router"
    assert sent["reasoning"] == {"enabled": False}
    assert "temperature" not in sent and "top_p" not in sent
    rf = sent["response_format"]["json_schema"]
    assert rf["strict"] is True and rf["schema"] == QueryParseFallback.model_json_schema()
    assert res.parsed.city == "Bengaluru"
    assert res.model == "openai/gpt-6-luna"
    assert res.cost_usd == Decimal("0.0000246")
    assert (res.input_tokens, res.output_tokens) == (81, 33)


@respx.mock
async def test_missing_cost_is_none() -> None:
    respx.post(URL).mock(return_value=_ok(cost=None))
    assert (await _parse(JevProvider(SecretStr(KEY), 5.0))).cost_usd is None


@respx.mock
async def test_status_mapping() -> None:
    respx.post(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(LLMAuthError):
        await _parse(JevProvider(SecretStr(KEY), 5.0))
    respx.post(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(LLMUnavailable):
        await _parse(JevProvider(SecretStr(KEY), 5.0))
    respx.post(URL).mock(return_value=httpx.Response(422))
    with pytest.raises(LLMOutputInvalid):
        await _parse(JevProvider(SecretStr(KEY), 5.0))


async def test_unavailable_without_key() -> None:
    p = JevProvider(None, 5.0)
    assert not p.available()
    with pytest.raises(LLMUnavailable):
        await _parse(p)


@respx.mock
async def test_key_and_query_never_logged(capfd: pytest.CaptureFixture[str]) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMOutputInvalid):
        await _parse(JevProvider(SecretStr(KEY), 5.0))
    out = capfd.readouterr()
    assert KEY not in out.out + out.err
    assert "SECRET-QUERY-TEXT" not in out.out + out.err


def test_settings_defaults() -> None:
    s = Settings()  # pyright: ignore[reportCallIssue]
    assert s.jev_enabled is False
    assert s.jev_model == "typesafe/jev-router"
    assert s.jev_reserve_usd == 0.001
    assert s.parse_fallback_timeout_seconds == 1.2
