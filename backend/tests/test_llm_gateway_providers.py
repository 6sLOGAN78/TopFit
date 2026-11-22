"""Per-request provider selection and variable-price budgeting in the gateway."""

from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.client import ExtraProvider, LLMGateway, build_gateway
from ranker.llm.errors import BudgetExceeded, LLMOutputInvalid, LLMUnavailable, UnknownModelPrice
from ranker.llm.providers import MessageParam, ParsedResult
from ranker.llm.providers.fake import FakeProvider
from ranker.llm.providers.jev import JevProvider
from ranker.llm.schemas import JobEnrichment
from ranker.settings import get_settings

ENRICH = JobEnrichment(remote_eligibility="india", seniority="senior", employment_type="full_time")
ROUTER = "typesafe/jev-router"


class RoutedProvider:
    """Fake router: reports a routed model and an optional cost."""

    def __init__(self, cost: Decimal | None, routed: str = "openai/gpt-6-luna") -> None:
        self.cost = cost
        self.routed = routed
        self.calls = 0

    def available(self) -> bool:
        return True

    async def parse(self, **kwargs: object) -> ParsedResult[JobEnrichment]:
        self.calls += 1
        return ParsedResult(ENRICH, 81, 33, 0, 0, self.routed, "stop", cost_usd=self.cost)


class BadRouted(RoutedProvider):
    async def parse(self, **kwargs: object) -> ParsedResult[JobEnrichment]:
        raise LLMOutputInvalid("bad", input_tokens=10, output_tokens=5, cost_usd=self.cost)


def _gw(
    engine: AsyncEngine,
    jev: RoutedProvider,
    reserve: str = "0.001",
    **overrides: object,
) -> tuple[LLMGateway, FakeProvider]:
    base = FakeProvider({"JobEnrichment": lambda _m: ENRICH})
    settings = get_settings().model_copy(update=overrides)
    gw = LLMGateway(
        engine,
        settings,
        base,
        {"jev": ExtraProvider(jev, ROUTER, Decimal(reserve))},  # pyright: ignore[reportArgumentType]
    )
    return gw, base


async def _call(gw: LLMGateway, provider: str | None) -> JobEnrichment:
    messages: list[MessageParam] = [{"role": "user", "content": "x"}]
    return await gw.structured(
        purpose="job_enrich",
        tier="fast",
        schema=JobEnrichment,
        system="s",
        messages=messages,
        max_tokens=100,
        est_input_tokens=1000,
        provider=provider,
    )


async def _rows(engine: AsyncEngine) -> list[dict[str, object]]:
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT * FROM llm_calls ORDER BY id"))
        return [dict(r._mapping) for r in res]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("clean_db")
async def test_default_and_haiku_use_default_provider(engine: AsyncEngine) -> None:
    jev = RoutedProvider(Decimal("0.00003"))
    gw, base = _gw(engine, jev)
    await _call(gw, None)
    await _call(gw, "haiku")
    assert len(base.calls) == 2 and jev.calls == 0
    assert {r["model"] for r in await _rows(engine)} == {"claude-haiku-5-5"}


@pytest.mark.usefixtures("clean_db")
async def test_jev_settles_at_usage_cost_and_logs_routed_model(engine: AsyncEngine) -> None:
    jev = RoutedProvider(Decimal("0.0000246"))
    gw, base = _gw(engine, jev)
    await _call(gw, "jev")
    assert jev.calls == 1 and not base.calls
    (row,) = await _rows(engine)
    assert row["model"] == f"{ROUTER}>openai/gpt-6-luna"
    assert row["cost_usd"] == Decimal("0.000025")  # llm_calls.cost_usd keeps 6 decimals
    assert row["success"] is True and row["error"] is None
    assert (row["input_tokens"], row["output_tokens"]) == (81, 33)


@pytest.mark.usefixtures("clean_db")
async def test_jev_missing_cost_settles_at_reserve(engine: AsyncEngine) -> None:
    gw, _ = _gw(engine, RoutedProvider(None), reserve="0.001")
    await _call(gw, "jev")
    (row,) = await _rows(engine)
    assert row["cost_usd"] == Decimal("0.001")


@pytest.mark.usefixtures("clean_db")
async def test_jev_invalid_output_billed_at_reported_cost_or_reserve(
    engine: AsyncEngine,
) -> None:
    gw, _ = _gw(engine, BadRouted(Decimal("0.00002")))
    with pytest.raises(LLMOutputInvalid):
        await _call(gw, "jev")
    gw2, _ = _gw(engine, BadRouted(None), reserve="0.001")
    with pytest.raises(LLMOutputInvalid):
        await _call(gw2, "jev")
    rows = await _rows(engine)
    assert [r["cost_usd"] for r in rows] == [Decimal("0.00002"), Decimal("0.001")]


@pytest.mark.usefixtures("clean_db")
async def test_jev_reserve_counts_against_budget(engine: AsyncEngine) -> None:
    jev = RoutedProvider(Decimal("0.00003"))
    gw, _ = _gw(engine, jev, reserve="0.001", llm_monthly_budget_usd=0.0005)
    with pytest.raises(BudgetExceeded):
        await _call(gw, "jev")
    assert jev.calls == 0


@pytest.mark.usefixtures("clean_db")
async def test_other_unpriced_models_still_fail_closed(engine: AsyncEngine) -> None:
    gw, _ = _gw(engine, RoutedProvider(None), llm_model_fast="unpriced/model")
    with pytest.raises(UnknownModelPrice):
        await _call(gw, "haiku")


@pytest.mark.usefixtures("clean_db")
async def test_unknown_provider_is_unavailable(engine: AsyncEngine) -> None:
    gw, _ = _gw(engine, RoutedProvider(None))
    with pytest.raises(LLMUnavailable):
        await _call(gw, "nope")
    assert await _rows(engine) == []


def test_providers_listing(engine: AsyncEngine) -> None:
    gw, _ = _gw(engine, RoutedProvider(None))
    assert gw.providers() == ["haiku", "jev"]
    assert gw.available("jev") and not gw.available("nope")
    plain = LLMGateway(engine, get_settings(), FakeProvider())
    assert plain.providers() == ["haiku"]
    assert not plain.available("jev")
    assert gw.model_for_provider("jev", "fast") == ROUTER
    assert gw.model_for_provider("haiku", "fast") == "claude-haiku-5-5"


def test_build_gateway_jev_gating(engine: AsyncEngine) -> None:
    base = get_settings().model_copy(update={"llm_provider": "fake"})
    key = SecretStr("sk-or-x")
    off = build_gateway(engine, base.model_copy(update={"openrouter_api_key": key}))
    assert off.providers() == ["haiku"]
    no_key = build_gateway(engine, base.model_copy(update={"jev_enabled": True}))
    assert no_key.providers() == ["haiku"]
    on = build_gateway(
        engine, base.model_copy(update={"jev_enabled": True, "openrouter_api_key": key})
    )
    assert on.providers() == ["haiku", "jev"]
    assert on.model_for_provider("jev", "fast") == ROUTER
    assert isinstance(on._extra["jev"].provider, JevProvider)  # pyright: ignore[reportPrivateUsage]
