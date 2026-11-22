"""LLM gateway exceptions."""

from datetime import UTC, datetime
from decimal import Decimal


class LLMError(Exception):
    """Base class for every gateway error."""


class LLMUnavailable(LLMError):
    """No key, provider down after retries, or timeout."""


class LLMAuthError(LLMUnavailable):
    """Provider rejected the credentials (401/403). Never retried."""


class BudgetExceeded(LLMError):
    def __init__(self, resets_at: datetime | None = None) -> None:
        super().__init__("monthly LLM budget exceeded")
        self.resets_at = resets_at or next_month_start()


class UnknownModelPrice(LLMError):
    """Model has no entry in llm_prices.yaml (fail closed)."""


class LLMOutputInvalid(LLMError):
    """Provider output missing or failed schema validation.

    When the provider still reported billed usage, the token counts (and the provider's own
    USD cost, if it sent one) ride along so the gateway can charge the failed call.
    """

    def __init__(
        self,
        message: str = "",
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cost_usd: Decimal | None = None,
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cost_usd = cost_usd

    @property
    def has_usage(self) -> bool:
        return self.input_tokens is not None or self.output_tokens is not None


def next_month_start(now: datetime | None = None) -> datetime:
    """First instant of the next UTC month."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current.month == 12:
        return datetime(current.year + 1, 1, 1, tzinfo=UTC)
    return datetime(current.year, current.month + 1, 1, tzinfo=UTC)
