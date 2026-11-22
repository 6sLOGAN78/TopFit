"""LLM gateway exceptions."""

from datetime import UTC, datetime


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
    """Provider output missing or failed schema validation."""


def next_month_start(now: datetime | None = None) -> datetime:
    """First instant of the next UTC month."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current.month == 12:
        return datetime(current.year + 1, 1, 1, tzinfo=UTC)
    return datetime(current.year, current.month + 1, 1, tzinfo=UTC)
