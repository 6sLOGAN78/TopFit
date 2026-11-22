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
