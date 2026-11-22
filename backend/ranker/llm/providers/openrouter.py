"""OpenRouter provider: OpenAI-compatible chat completions with strict JSON-schema output.

Used to run enrichment on free models until an Anthropic key exists. The API key is only
ever placed in the Authorization header; prompts, responses and the key are never logged.
"""

import asyncio
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, SecretStr, ValidationError

from ranker.llm.errors import LLMAuthError, LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import MessageParam, ParsedResult

log = structlog.get_logger("ranker.llm.openrouter")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_MAX_RETRIES = 2
_RETRY_AFTER_CAP_S = 10.0
_BACKOFF_S = 1.0
_APP_HEADERS = {"HTTP-Referer": "https://topfit.duckdns.org", "X-Title": "Ranker"}


def _flatten(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":  # pyright: ignore[reportUnknownMemberType]
            parts.append(str(block.get("text", "")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return "\n".join(parts)


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        raw = resp.headers.get("retry-after")
        if raw is not None:
            try:
                return min(max(float(raw), 0.0), _RETRY_AFTER_CAP_S)
            except ValueError:
                pass
    return min(_BACKOFF_S * (2**attempt), _RETRY_AFTER_CAP_S)


class OpenRouterProvider:
    def __init__(
        self,
        api_key: SecretStr | None,
        timeout_s: float,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = client

    def available(self) -> bool:
        return self._api_key is not None and self._api_key.get_secret_value() != ""

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        if self._api_key is None:
            raise LLMUnavailable("OPENROUTER_API_KEY not set")
        headers = {
            **_APP_HEADERS,
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
        }
        timeout = httpx.Timeout(self._timeout_s, connect=5.0)
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            for attempt in range(_MAX_RETRIES + 1):
                resp: httpx.Response | None = None
                try:
                    resp = await client.post(
                        self._url, json=payload, headers=headers, timeout=timeout
                    )
                except httpx.TransportError as exc:
                    if attempt == _MAX_RETRIES:
                        raise LLMUnavailable(type(exc).__name__) from exc
                else:
                    code = resp.status_code
                    if code in (401, 403):
                        raise LLMAuthError(f"openrouter auth failed ({code})")
                    if code == 429 or code >= 500:
                        if attempt == _MAX_RETRIES:
                            raise LLMUnavailable(f"openrouter status {code}")
                    elif code >= 400:
                        raise LLMOutputInvalid(f"openrouter bad request ({code})")
                    else:
                        return resp
                await asyncio.sleep(_retry_delay(resp, attempt))
            raise LLMUnavailable("openrouter retries exhausted")  # pragma: no cover
        finally:
            if owns:
                await client.aclose()

    async def parse[T: BaseModel](
        self,
        *,
        model: str,
        schema: type[T],
        system: str,
        messages: list[MessageParam],
        max_tokens: int,
    ) -> ParsedResult[T]:
        if not self.available():
            raise LLMUnavailable("OPENROUTER_API_KEY not set")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *({"role": m["role"], "content": _flatten(m["content"])} for m in messages),
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        resp = await self._post(payload)
        try:
            body: Any = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            usage: dict[str, Any] = body.get("usage") or {}
            parsed = schema.model_validate_json(content or "")
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            log.warning("openrouter_invalid_output", model=model, error=type(exc).__name__)
            raise LLMOutputInvalid("openrouter output invalid") from exc
        details: dict[str, Any] = usage.get("prompt_tokens_details") or {}
        return ParsedResult(
            parsed=parsed,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_tokens=int(details.get("cached_tokens") or 0),
            cache_write_tokens=0,
            model=str(body.get("model") or model),
            stop_reason=choice.get("finish_reason"),
        )
