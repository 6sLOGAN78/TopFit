"""LLMGateway: the single entry point for LLM calls (FND-06).

Logs one ``llm_calls`` row per attempt (no prompt or response text), enforces the monthly
budget before calling, and degrades via LLMUnavailable / BudgetExceeded.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
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
from ranker.llm.providers import MessageParam, ParsedResult, Provider
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
_UPDATE_SQL = text(
    "UPDATE llm_calls SET model = COALESCE(CAST(:model AS text), model), "
    "input_tokens = :input_tokens, output_tokens = :output_tokens, "
    "cache_read_tokens = :cache_read_tokens, cache_write_tokens = :cache_write_tokens, "
    "cost_usd = :cost_usd, latency_ms = :latency_ms, success = :success, error = :error "
    "WHERE id = :id"
)
_RESERVE_SQL = text(
    "INSERT INTO llm_calls (user_id, purpose, model, cost_usd, success, error) "
    "VALUES (:user_id, :purpose, :model, :cost_usd, false, 'pending') RETURNING id"
)
# Fixed key serialising budget reservations across all gateway instances.
_BUDGET_LOCK_KEY = 727275


@dataclass(frozen=True)
class ExtraProvider:
    """A provider selectable per request (``structured(provider=name)``).

    ``reserve_usd`` is for variable-priced routers: a fixed per-call cap is reserved against
    the budget instead of a token-priced worst case, and the call settles at the provider's
    reported cost (or at the reserve when none is reported). Without it, the usual price
    table applies and an unpriced model fails closed.
    """

    provider: Provider
    model: str
    reserve_usd: Decimal | None = None


class LLMGateway:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        provider: Provider,
        extra: Mapping[str, ExtraProvider] | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._provider = provider
        self._extra: dict[str, ExtraProvider] = dict(extra or {})

    def available(self, provider: str | None = None) -> bool:
        if provider in (None, "haiku"):
            return self._provider.available()
        extra = self._extra.get(provider or "")
        return extra is not None and extra.provider.available()

    def providers(self) -> list[Literal["haiku", "jev"]]:
        """Parse models offered to the UI; haiku (the default provider) is always first."""
        out: list[Literal["haiku", "jev"]] = ["haiku"]
        if self.available("jev"):
            out.append("jev")
        return out

    def model_for_provider(self, provider: str | None, tier: Literal["fast", "agent"]) -> str:
        extra = self._extra.get(provider or "")
        return extra.model if extra is not None else self._model_for(tier)

    def _model_for(self, tier: Literal["fast", "agent"]) -> str:
        if self._settings.llm_provider == "openrouter":
            return self._settings.openrouter_model
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
        row_id: int | None = None,
        routed_model: str | None = None,
    ) -> None:
        """Insert the attempt row, or finalise the reservation row when ``row_id`` is given."""
        async with self._engine.begin() as conn:
            await conn.execute(
                _INSERT_SQL if row_id is None else _UPDATE_SQL,
                {
                    **({"id": row_id} if row_id is not None else {}),
                    **(
                        {"user_id": user_id, "purpose": purpose, "model": model}
                        if row_id is None
                        else {}
                    ),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_write_tokens": cache_write_tokens,
                    "cost_usd": cost,
                    "latency_ms": latency_ms,
                    "success": success,
                    "error": error,
                    **({"model": routed_model} if row_id is not None else {}),
                },
            )
        log.info(
            "llm_call",
            purpose=purpose,
            model=routed_model or model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost),
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    async def _reserve(
        self,
        *,
        user_id: UUID | None,
        purpose: str,
        model: str,
        est_input_tokens: int,
        max_tokens: int,
        reserve_usd: Decimal | None = None,
    ) -> tuple[int, Decimal]:
        """Reserve the worst-case cost atomically; returns (row id, reserved cost).

        One transaction holds an advisory lock, re-reads spend (which includes other
        callers' pending reservations) and inserts a pending row, so concurrent callers
        cannot all pass the check.
        """
        try:
            worst = (
                reserve_usd
                if reserve_usd is not None
                else worst_case_cost(
                    model, est_input_tokens=est_input_tokens, max_tokens=max_tokens
                )
            )
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
        budget = Decimal(str(self._settings.llm_monthly_budget_usd))
        exceeded = False
        row_id = 0
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BUDGET_LOCK_KEY})
            spent = Decimal((await conn.execute(_SPENT_SQL)).scalar_one())
            if spent + worst > budget:
                exceeded = True
                await conn.execute(
                    _INSERT_SQL,
                    {
                        "user_id": user_id,
                        "purpose": purpose,
                        "model": model,
                        "input_tokens": None,
                        "output_tokens": None,
                        "cache_read_tokens": None,
                        "cache_write_tokens": None,
                        "cost_usd": Decimal(0),
                        "latency_ms": None,
                        "success": False,
                        "error": "budget_exceeded",
                    },
                )
            else:
                row_id = (
                    await conn.execute(
                        _RESERVE_SQL,
                        {
                            "user_id": user_id,
                            "purpose": purpose,
                            "model": model,
                            "cost_usd": worst,
                        },
                    )
                ).scalar_one()
        if exceeded:
            log.warning("llm_budget_exceeded", purpose=purpose, model=model)
            raise BudgetExceeded()
        return row_id, worst

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
        provider: str | None = None,
    ) -> T:
        prov: Provider = self._provider
        reserve_usd: Decimal | None = None
        model = self._model_for(tier)
        if provider not in (None, "haiku"):
            extra = self._extra.get(provider or "")
            if extra is None:
                log.warning("llm_unavailable", purpose=purpose, reason="unknown_provider")
                raise LLMUnavailable("LLM provider not configured")
            prov, model, reserve_usd = extra.provider, extra.model, extra.reserve_usd
        if not prov.available():
            log.warning("llm_unavailable", purpose=purpose, reason="no_provider_key")
            raise LLMUnavailable("LLM provider not configured")
        row_id, fallback_cost = await self._reserve(
            user_id=user_id,
            purpose=purpose,
            model=model,
            est_input_tokens=est_input_tokens,
            max_tokens=max_tokens,
            reserve_usd=reserve_usd,
        )
        started = time.perf_counter()
        recorded = False
        # Usage is unknown until the provider returns: the reserved worst-case cost stays
        # on the row for a timeout or cancellation after tokens may have been spent.
        try:
            result: ParsedResult[T] | None = None
            try:
                async with asyncio.timeout(self._settings.llm_timeout_seconds):
                    result = await prov.parse(
                        model=model,
                        schema=schema,
                        system=system,
                        messages=messages,
                        max_tokens=max_tokens,
                    )
            except TimeoutError as exc:
                recorded = True
                await self._fail(
                    row_id, user_id, purpose, model, started, LLMUnavailable.__name__, fallback_cost
                )
                raise LLMUnavailable("LLM call timed out") from exc
            except LLMOutputInvalid as exc:
                # Unusable output can still be billed: charge the reported usage.
                recorded = True
                await self._fail_billed(row_id, user_id, purpose, model, started, exc, reserve_usd)
                raise
            except LLMError as exc:
                # The provider reported a definite failure before returning usage.
                recorded = True
                await self._fail(row_id, user_id, purpose, model, started, type(exc).__name__)
                raise
            routed: str | None = None
            if reserve_usd is not None:
                # Variable-priced router: settle at the reported cost, else at the reserve,
                # and keep the model it actually routed to next to the router id.
                real_cost = result.cost_usd if result.cost_usd is not None else reserve_usd
                if result.model and result.model != model:
                    routed = f"{model}>{result.model}"
            else:
                real_cost = cost_usd(
                    model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cache_read_tokens=result.cache_read_tokens,
                    cache_write_tokens=result.cache_write_tokens,
                )
            try:
                parsed = schema.model_validate(result.parsed.model_dump())
            except (ValidationError, AttributeError) as exc:
                # Tokens were spent even though the output is unusable.
                recorded = True
                await self._record_result(
                    row_id,
                    user_id,
                    purpose,
                    model,
                    started,
                    result,
                    real_cost,
                    "LLMOutputInvalid",
                    routed,
                )
                raise LLMOutputInvalid("schema validation failed") from exc
            recorded = True
            await self._record_result(
                row_id, user_id, purpose, model, started, result, real_cost, None, routed
            )
            return parsed
        finally:
            if not recorded:
                # Cancellation or an unexpected error: write the row even if we are cancelled.
                await asyncio.shield(
                    self._fail(row_id, user_id, purpose, model, started, "Cancelled", fallback_cost)
                )

    async def _record_result(
        self,
        row_id: int,
        user_id: UUID | None,
        purpose: str,
        model: str,
        started: float,
        result: ParsedResult[Any],
        cost: Decimal,
        error: str | None,
        routed_model: str | None = None,
    ) -> None:
        await self._record(
            user_id=user_id,
            purpose=purpose,
            model=model,
            success=error is None,
            error=error,
            cost=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            row_id=row_id,
            routed_model=routed_model,
        )

    async def _fail_billed(
        self,
        row_id: int,
        user_id: UUID | None,
        purpose: str,
        model: str,
        started: float,
        exc: LLMOutputInvalid,
        reserve_usd: Decimal | None = None,
    ) -> None:
        """Settle an invalid-output call at the larger of token-priced and provider cost."""
        if not exc.has_usage and exc.cost_usd is None:
            await self._fail(row_id, user_id, purpose, model, started, type(exc).__name__)
            return
        input_tokens = exc.input_tokens or 0
        output_tokens = exc.output_tokens or 0
        cache_read = min(exc.cache_read_tokens or 0, input_tokens)
        if reserve_usd is not None:
            cost = exc.cost_usd if exc.cost_usd is not None else reserve_usd
        else:
            # OpenAI-style prompt_tokens include cached tokens; price them at the cache rate.
            priced = cost_usd(
                model,
                input_tokens=input_tokens - cache_read,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
            )
            cost = max(priced, exc.cost_usd or Decimal(0))
        await self._record(
            user_id=user_id,
            purpose=purpose,
            model=model,
            success=False,
            error=type(exc).__name__,
            cost=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            cache_read_tokens=exc.cache_read_tokens,
            cache_write_tokens=None,
            row_id=row_id,
        )

    async def _fail(
        self,
        row_id: int,
        user_id: UUID | None,
        purpose: str,
        model: str,
        started: float,
        error: str,
        cost: Decimal = Decimal(0),
    ) -> None:
        await self._record(
            user_id=user_id,
            purpose=purpose,
            model=model,
            success=False,
            error=error,
            cost=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
            row_id=row_id,
        )


def build_gateway(engine: AsyncEngine, settings: Settings) -> LLMGateway:
    provider: Provider
    if settings.llm_provider == "fake":
        from ranker.llm.providers.fake import FakeProvider

        provider = FakeProvider()
    elif settings.llm_provider == "openrouter":
        from ranker.llm.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider(
            settings.openrouter_api_key,
            settings.llm_timeout_seconds,
            settings.openrouter_base_url,
        )
    else:
        from ranker.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(settings.anthropic_api_key, settings.llm_timeout_seconds)
    extra: dict[str, ExtraProvider] = {}
    key = settings.openrouter_api_key
    if settings.jev_enabled and key is not None and key.get_secret_value():
        from ranker.llm.providers.jev import JevProvider

        extra["jev"] = ExtraProvider(
            JevProvider(key, settings.llm_timeout_seconds, settings.openrouter_base_url),
            settings.jev_model,
            Decimal(str(settings.jev_reserve_usd)),
        )
    return LLMGateway(engine, settings, provider, extra)
