"""Provider interface for the LLM gateway."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel


class MessageParam(TypedDict):
    """A chat message. ``content`` keeps provider content blocks as-is (thinking with its
    signature, tool_use, tool_result, ...): never flatten them to strings.
    """

    role: Literal["user", "assistant"]
    content: str | list[Any]


@dataclass
class ParsedResult[T: BaseModel]:
    parsed: T
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    model: str
    stop_reason: str | None


class Provider(Protocol):
    def available(self) -> bool: ...

    async def parse[T: BaseModel](
        self,
        *,
        model: str,
        schema: type[T],
        system: str,
        messages: list[MessageParam],
        max_tokens: int,
    ) -> ParsedResult[T]: ...
