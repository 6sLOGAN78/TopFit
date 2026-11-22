"""Deterministic, network-free provider for tests and keyless runs."""

from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from ranker.llm.providers import MessageParam, ParsedResult

Responder = Callable[[list[MessageParam]], BaseModel] | Exception


class FakeProvider:
    """``responses`` maps a schema class name to a callable or an exception to raise."""

    def __init__(self, responses: Mapping[str, Responder] | None = None) -> None:
        self.responses: dict[str, Responder] = dict(responses or {})
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    async def parse[T: BaseModel](
        self,
        *,
        model: str,
        schema: type[T],
        system: str,
        messages: list[MessageParam],
        max_tokens: int,
    ) -> ParsedResult[T]:
        self.calls.append(
            {
                "model": model,
                "schema": schema,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
            }
        )
        responder = self.responses.get(schema.__name__)
        if isinstance(responder, Exception):
            raise responder
        if responder is None:
            raise LookupError(f"FakeProvider has no response for {schema.__name__}")
        return ParsedResult(
            parsed=cast(T, responder(messages)),
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model=model,
            stop_reason="end_turn",
        )
