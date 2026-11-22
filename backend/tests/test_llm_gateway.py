import asyncio
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.client import LLMGateway
from ranker.llm.errors import BudgetExceeded, LLMOutputInvalid, LLMUnavailable
from ranker.llm.providers import MessageParam
from ranker.llm.providers.fake import FakeProvider
from ranker.llm.schemas import JobEnrichment
from ranker.settings import Settings, get_settings

SECRET_PROMPT = "super-secret-prompt-text-xyz"
ENRICH = JobEnrichment(remote_eligibility="india", seniority="senior", employment_type="full_time")


def _gateway(
    engine: AsyncEngine, provider: FakeProvider, **overrides: object
) -> tuple[LLMGateway, FakeProvider]:
    settings: Settings = get_settings().model_copy(update=overrides)
    return LLMGateway(engine, settings, provider), provider


async def _call(gw: LLMGateway) -> JobEnrichment:
    messages: list[MessageParam] = [{"role": "user", "content": SECRET_PROMPT}]
    return await gw.structured(
        purpose="job_enrich",
        tier="fast",
        schema=JobEnrichment,
        system="sys-" + SECRET_PROMPT,
        messages=messages,
        max_tokens=100,
        est_input_tokens=1000,
    )


async def _rows(engine: AsyncEngine) -> list[dict[str, object]]:
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT * FROM llm_calls ORDER BY id"))
        return [dict(r._mapping) for r in res]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("clean_db")
async def test_success_logged_without_prompt_text(engine: AsyncEngine) -> None:
    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": lambda _m: ENRICH}))
    assert await _call(gw) == ENRICH
    rows = await _rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row["success"] is True
    assert row["model"] == "claude-haiku-5-5"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 20
    assert row["cost_usd"] > 0  # pyright: ignore[reportOperatorIssue]
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0  # pyright: ignore[reportOperatorIssue]
    assert row["error"] is None
    assert SECRET_PROMPT not in str(row)


@pytest.mark.usefixtures("clean_db")
async def test_budget_exceeded_skips_provider(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO llm_calls (purpose, model, cost_usd, success, created_at) VALUES "
                "('x','m',4.9999,true,now()), "
                "('x','m',100,true, date_trunc('month', now() AT TIME ZONE 'UTC') "
                "- interval '1 day')"
            )
        )
    gw, provider = _gateway(
        engine, FakeProvider({"JobEnrichment": lambda _m: ENRICH}), llm_monthly_budget_usd=5.0
    )
    with pytest.raises(BudgetExceeded):
        await _call(gw)
    assert provider.calls == []
    last = (await _rows(engine))[-1]
    assert last["success"] is False
    assert last["error"] == "budget_exceeded"
    assert last["cost_usd"] == 0


@pytest.mark.usefixtures("clean_db")
async def test_last_month_rows_do_not_count(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO llm_calls (purpose, model, cost_usd, success, created_at) VALUES "
                "('x','m',100,true, date_trunc('month', now() AT TIME ZONE 'UTC') "
                "- interval '1 day')"
            )
        )
    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": lambda _m: ENRICH}))
    assert await _call(gw) == ENRICH


@pytest.mark.usefixtures("clean_db")
async def test_provider_unavailable_logged_and_propagated(engine: AsyncEngine) -> None:
    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": LLMUnavailable("down")}))
    with pytest.raises(LLMUnavailable):
        await _call(gw)
    rows = await _rows(engine)
    assert rows[0]["success"] is False
    assert rows[0]["error"] == "LLMUnavailable"


@pytest.mark.usefixtures("clean_db")
async def test_invalid_output(engine: AsyncEngine) -> None:
    def bad(_m: list[MessageParam]) -> JobEnrichment:
        return JobEnrichment.model_construct(
            remote_eligibility="mars", seniority="senior", employment_type="full_time"
        )

    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": bad}))
    with pytest.raises(LLMOutputInvalid):
        await _call(gw)
    rows = await _rows(engine)
    assert rows[0]["success"] is False
    assert rows[0]["error"] == "LLMOutputInvalid"
    # Tokens were spent: the failure row carries the real cost, not zero.
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["cost_usd"] > 0  # pyright: ignore[reportOperatorIssue]


@pytest.mark.usefixtures("clean_db")
async def test_provider_invalid_output_with_usage_is_charged(engine: AsyncEngine) -> None:
    exc = LLMOutputInvalid("cut off", input_tokens=1000, output_tokens=100, cache_read_tokens=0)
    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": exc}))
    with pytest.raises(LLMOutputInvalid):
        await _call(gw)
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error"] == "LLMOutputInvalid"
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (1000, 100)
    assert rows[0]["cost_usd"] == Decimal("0.00015")  # 1000*0.10 + 100*0.50 per MTok


@pytest.mark.usefixtures("clean_db")
async def test_provider_invalid_output_without_usage_costs_zero(engine: AsyncEngine) -> None:
    gw, _ = _gateway(engine, FakeProvider({"JobEnrichment": LLMOutputInvalid("bad request")}))
    with pytest.raises(LLMOutputInvalid):
        await _call(gw)
    rows = await _rows(engine)
    assert rows[0]["cost_usd"] == 0
    assert rows[0]["input_tokens"] is None


@pytest.mark.usefixtures("clean_db")
async def test_timeout_records_worst_case_cost(engine: AsyncEngine) -> None:
    class Slow(FakeProvider):
        async def parse(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
            await asyncio.sleep(5)

    gw, _ = _gateway(engine, Slow(), llm_timeout_seconds=0.05)
    with pytest.raises(LLMUnavailable):
        await _call(gw)
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["cost_usd"] > 0  # pyright: ignore[reportOperatorIssue]


@pytest.mark.usefixtures("clean_db")
async def test_cancellation_still_writes_row(engine: AsyncEngine) -> None:
    class Slow(FakeProvider):
        async def parse(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
            await asyncio.sleep(5)

    gw, _ = _gateway(engine, Slow())
    task = asyncio.create_task(_call(gw))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await _rows(engine)
    assert len(rows) == 1
    assert rows[0]["error"] == "Cancelled"
    assert rows[0]["cost_usd"] > 0  # pyright: ignore[reportOperatorIssue]


@pytest.mark.usefixtures("clean_db")
async def test_unavailable_without_key_writes_no_row(engine: AsyncEngine) -> None:
    class NoKey(FakeProvider):
        def available(self) -> bool:
            return False

    gw, provider = _gateway(engine, NoKey())
    assert gw.available() is False
    with pytest.raises(LLMUnavailable):
        await _call(gw)
    assert provider.calls == []
    assert await _rows(engine) == []


@pytest.mark.usefixtures("clean_db")
async def test_messages_passed_through_unchanged(engine: AsyncEngine) -> None:
    blocks: list[MessageParam] = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hm", "signature": "sig123"},
                {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]
    gw, provider = _gateway(engine, FakeProvider({"JobEnrichment": lambda _m: ENRICH}))
    await gw.structured(
        purpose="job_enrich",
        tier="fast",
        schema=JobEnrichment,
        system="s",
        messages=blocks,
        max_tokens=50,
        est_input_tokens=100,
    )
    assert provider.calls[0]["messages"] is blocks


@pytest.mark.usefixtures("clean_db")
async def test_concurrent_calls_cannot_both_pass_budget(engine: AsyncEngine) -> None:
    class Slow(FakeProvider):
        async def parse(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
            await asyncio.sleep(0.4)
            return await super().parse(**kwargs)

    # Worst case per call is 0.00015 USD: each fits in 0.0002 alone, two do not.
    gw, provider = _gateway(
        engine,
        Slow({"JobEnrichment": lambda _m: ENRICH}),
        llm_monthly_budget_usd=0.0002,
    )
    results = await asyncio.gather(_call(gw), _call(gw), return_exceptions=True)
    ok = [r for r in results if isinstance(r, JobEnrichment)]
    denied = [r for r in results if isinstance(r, BudgetExceeded)]
    assert len(ok) == 1
    assert len(denied) == 1
    assert len(provider.calls) == 1
    rows = await _rows(engine)
    assert sorted(str(r["error"]) for r in rows) == ["None", "budget_exceeded"]
