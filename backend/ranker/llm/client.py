"""LLMGateway: the single entry point for LLM calls (FND-06).

Logs one ``llm_calls`` row per attempt (no prompt or response text), enforces the monthly
budget before calling, and degrades via LLMUnavailable / BudgetExceeded.
"""

import asyncio
import time
from decimal import Decimal
from typing import Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.cost import cost_usd, worst_case_cost
from ranker.llm.errors import (
    BudgetExceeded,
    LLMError,
    LLMOutputInvalid,
    LLMUnavailable,
    UnknownModelPrice,
)
from ranker.llm.providers import MessageParam, Provider
from ranker.settings import Settings

log = structlog.get_logger("ranker.llm")

_SPENT_SQL = text(
    "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls "
    "WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
)
_INSERT_SQL = text(
    "INSERT INTO llm_calls (user_id, purpose, model, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_tokens, cost_usd, latency_ms, success, error) "
    "VALUES (:user_id, :purpose, :model, :input_tokens, :output_tokens, :cache_read_tokens, "
    ":cache_write_tokens, :cost_usd, :latency_ms, :success, :error)"
)


class LLMGateway:
    def __init__(self, engine: AsyncEngine, settings: Settings, provider: Provider) -> None:
        self._engine = engine
        self._settings = settings
        self._provider = provider

    def available(self) -> bool:
        return self._provider.available()

    def _model_for(self, tier: Literal["fast", "agent"]) -> str:
        if tier == "fast":
            return self._settings.llm_model_fast
        return self._settings.llm_model_agent

    async def _record(
        self,
        *,
        user_id: UUID | None,
        purpose: str,
        model: str,
        success: bool,
        error: str | None,
        cost: Decimal = Decimal(0),
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                _INSERT_SQL,
                {
                    "user_id": user_id,
                    "purpose": purpose,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_write_tokens": cache_write_tokens,
                    "cost_usd": cost,
                    "latency_ms": latency_ms,
                    "success": success,
                    "error": error,
                },
            )
        log.info(
            "llm_call",
            purpose=purpose,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost),
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    async def _check_budget(
        self,
        *,
        user_id: UUID | None,
        purpose: str,
        model: str,
        est_input_tokens: int,
        max_tokens: int,
    ) -> None:
        try:
            worst = worst_case_cost(model, est_input_tokens=est_input_tokens, max_tokens=max_tokens)
        except UnknownModelPrice:
            # Fail closed: an unpriced model cannot be budgeted.
            log.error("llm_unknown_model_price", model=model)
            await self._record(
                user_id=user_id,
                purpose=purpose,
                model=model,
                success=False,
                error="UnknownModelPrice",
            )
            raise
        async with self._engine.connect() as conn:
            spent = Decimal((await conn.execute(_SPENT_SQL)).scalar_one())
        if spent + worst > Decimal(str(self._settings.llm_monthly_budget_usd)):
            await self._record(
                user_id=user_id,
                purpose=purpose,
                model=model,
                success=False,
                error="budget_exceeded",
            )
            raise BudgetExceeded()

    async def structured[T: BaseModel](
        self,
        *,
        purpose: str,
        tier: Literal["fast", "agent"],
        schema: type[T],
        system: str,
        messages: list[MessageParam],
        max_tokens: int,
        est_input_tokens: int,
        user_id: UUID | None = None,
    ) -> T:
        model = self._model_for(tier)
        if not self.available():
            log.warning("llm_unavailable", purpose=purpose, reason="no_provider_key")
            raise LLMUnavailable("LLM provider not configured")
        await self._check_budget(
            user_id=user_id,
            purpose=purpose,
            model=model,
            est_input_tokens=est_input_tokens,
            max_tokens=max_tokens,
        )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.llm_timeout_seconds):
                result = await self._provider.parse(
                    model=model,
                    schema=schema,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                )
            try:
                parsed = schema.model_validate(result.parsed.model_dump())
            except (ValidationError, AttributeError) as exc:
                raise LLMOutputInvalid("schema validation failed") from exc
        except TimeoutError as exc:
            await self._fail(user_id, purpose, model, started, LLMUnavailable.__name__)
            raise LLMUnavailable("LLM call timed out") from exc
        except LLMError as exc:
            await self._fail(user_id, purpose, model, started, type(exc).__name__)
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = cost_usd(
            model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )
        await self._record(
            user_id=user_id,
            purpose=purpose,
            model=model,
            success=True,
            error=None,
            cost=cost,
            latency_ms=latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )
        return parsed

    async def _fail(
        self, user_id: UUID | None, purpose: str, model: str, started: float, error: str
    ) -> None:
        await self._record(
            user_id=user_id,
            purpose=purpose,
            model=model,
            success=False,
            error=error,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def build_gateway(engine: AsyncEngine, settings: Settings) -> LLMGateway:
    provider: Provider
    if settings.llm_provider == "fake":
        from ranker.llm.providers.fake import FakeProvider

        provider = FakeProvider()
    else:
        from ranker.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(settings.anthropic_api_key, settings.llm_timeout_seconds)
    return LLMGateway(engine, settings, provider)
