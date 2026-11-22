import os
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.client import LLMGateway, build_gateway
from ranker.llm.errors import LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import MessageParam
from ranker.llm.providers.anthropic import AnthropicProvider
from ranker.llm.schemas import JobEnrichment
from ranker.settings import get_settings

ENRICH = JobEnrichment(remote_eligibility="india", seniority="mid", employment_type="full_time")

BLOCKS: list[MessageParam] = [
    {"role": "user", "content": "find jobs"},
    {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "plan", "signature": "sig-abc"},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "python"}},
        ],
    },
    {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "3 jobs"}],
    },
]


class StubMessages:
    def __init__(self, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.error = error

    async def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(
            parsed_output=ENRICH,
            usage=SimpleNamespace(
                input_tokens=11,
                output_tokens=7,
                cache_read_input_tokens=5,
                cache_creation_input_tokens=3,
            ),
            model=kwargs["model"],
            stop_reason="end_turn",
        )


def _provider(stub: StubMessages) -> AnthropicProvider:
    p = AnthropicProvider(SecretStr("sk-test"), 5.0)
    p._client = SimpleNamespace(messages=stub)  # pyright: ignore[reportPrivateUsage]
    return p


async def _parse(p: AnthropicProvider, model: str = "claude-haiku-5-5") -> Any:
    return await p.parse(
        model=model, schema=JobEnrichment, system="s", messages=BLOCKS, max_tokens=50
    )


async def test_haiku_kwargs_and_usage_mapping() -> None:
    stub = StubMessages()
    res = await _parse(_provider(stub))
    kw = stub.kwargs
    assert kw["output_format"] is JobEnrichment
    assert kw["output_config"] == {"effort": "low"}
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["model"] == "claude-haiku-5-5" and kw["max_tokens"] == 50 and kw["system"] == "s"
    assert not {"temperature", "top_p", "top_k"} & set(kw)
    assert (res.input_tokens, res.output_tokens) == (11, 7)
    assert (res.cache_read_tokens, res.cache_write_tokens) == (5, 3)
    assert res.parsed == ENRICH


async def test_sonnet_uses_between_tools() -> None:
    stub = StubMessages()
    await _parse(_provider(stub), "claude-sonnet-5-5")
    assert stub.kwargs["thinking"] == {"type": "between_tools"}


async def test_content_blocks_pass_through_unchanged() -> None:
    stub = StubMessages()
    await _parse(_provider(stub))
    sent = stub.kwargs["messages"]
    assert sent == BLOCKS
    assert sent[1]["content"][0] is BLOCKS[1]["content"][0]
    assert sent[1]["content"][0]["signature"] == "sig-abc"
    assert sent[1]["content"][1]["type"] == "tool_use"


async def test_blocks_pass_through_gateway(engine: AsyncEngine, database_url: str) -> None:
    stub = StubMessages()
    gw = LLMGateway(engine, get_settings(), _provider(stub))
    await gw.structured(
        purpose="t",
        tier="fast",
        schema=JobEnrichment,
        system="s",
        messages=BLOCKS,
        max_tokens=50,
        est_input_tokens=100,
    )
    assert stub.kwargs["messages"] is BLOCKS


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    response = httpx2.Response(code, request=httpx2.Request("POST", "https://x.invalid"))
    return cls("err", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        _status_error(anthropic.RateLimitError, 429),
        _status_error(anthropic.InternalServerError, 500),
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.invalid")),
    ],
)
async def test_sdk_errors_become_unavailable(error: Exception) -> None:
    with pytest.raises(LLMUnavailable):
        await _parse(_provider(StubMessages(error)))


async def test_bad_request_is_invalid_output() -> None:
    with pytest.raises(LLMOutputInvalid):
        await _parse(_provider(StubMessages(_status_error(anthropic.BadRequestError, 400))))


async def test_no_key_unavailable_without_sdk_client() -> None:
    p = AnthropicProvider(None, 5.0)
    assert p.available() is False
    with pytest.raises(LLMUnavailable):
        await _parse(p)
    assert p._client is None  # pyright: ignore[reportPrivateUsage]
    assert AnthropicProvider(SecretStr(""), 5.0).available() is False


async def test_build_gateway_anthropic_without_key(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().model_copy(
        update={"llm_provider": "anthropic", "anthropic_api_key": None}
    )
    assert build_gateway(engine, settings).available() is False


@pytest.mark.llm
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
async def test_live_job_enrichment(engine: AsyncEngine) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "anthropic",
            "anthropic_api_key": SecretStr(os.environ["ANTHROPIC_API_KEY"]),
        }
    )
    gw = build_gateway(engine, settings)
    out = await gw.structured(
        purpose="job_enrich",
        tier="fast",
        schema=JobEnrichment,
        system="Classify the job posting.",
        messages=[
            {"role": "user", "content": "Senior Python engineer, remote worldwide, full time."}
        ],
        max_tokens=100,
        est_input_tokens=200,
    )
    assert isinstance(out, JobEnrichment)
