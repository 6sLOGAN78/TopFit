"""Anthropic provider. The SDK is imported lazily so fake-provider runs never load it.

Verified against the installed SDK (anthropic 2.x): ``AsyncAnthropic(api_key, max_retries,
timeout)``, ``messages.parse(..., output_format=Model, output_config, thinking)`` returning
``parsed_output`` and ``usage.{input,output,cache_read_input,cache_creation_input}_tokens``.
The SDK uses ``httpx2``, so the timeout object is ``anthropic.Timeout``, not ``httpx.Timeout``.
No sampling parameters are ever sent.
"""

from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError

from ranker.llm.errors import LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import MessageParam, ParsedResult


class AnthropicProvider:
    def __init__(self, api_key: SecretStr | None, timeout_s: float) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        # Third-party boundary: the SDK client is typed Any and created on first use.
        self._client: Any = None

    def available(self) -> bool:
        return self._api_key is not None and self._api_key.get_secret_value() != ""

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            if self._api_key is None:
                raise LLMUnavailable("ANTHROPIC_API_KEY not set")
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key.get_secret_value(),
                max_retries=2,
                timeout=anthropic.Timeout(self._timeout_s, connect=5.0),
            )
        return self._client

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
            raise LLMUnavailable("ANTHROPIC_API_KEY not set")
        import anthropic

        # Haiku accepts thinking disabled; Sonnet 5.5 rejects it (use between_tools).
        thinking = {"type": "disabled"} if "haiku" in model else {"type": "between_tools"}
        client = self._get_client()
        try:
            resp = await client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_format=schema,
                output_config={"effort": "low"},
                thinking=thinking,
            )
        except anthropic.BadRequestError as exc:
            raise LLMOutputInvalid("bad request") from exc
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            raise LLMUnavailable(type(exc).__name__) from exc
        except ValidationError as exc:
            raise LLMOutputInvalid("schema validation failed") from exc
        parsed = resp.parsed_output
        if parsed is None:
            raise LLMOutputInvalid("no parsed output")
        usage = resp.usage
        return ParsedResult(
            parsed=parsed,
            input_tokens=int(usage.input_tokens),
            output_tokens=int(usage.output_tokens),
            cache_read_tokens=int(usage.cache_read_input_tokens or 0),
            cache_write_tokens=int(usage.cache_creation_input_tokens or 0),
            model=str(resp.model),
            stop_reason=resp.stop_reason,
        )
