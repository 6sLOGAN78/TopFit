"""Model fallback in query parsing: gated, cached, per-request provider, never fatal."""

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.client import ExtraProvider, LLMGateway
from ranker.llm.errors import BudgetExceeded, LLMUnavailable
from ranker.llm.providers.fake import FakeProvider
from ranker.llm.schemas import QueryParseFallback
from ranker.search.models import ParseModel, SearchFilters
from ranker.search.parse import parse_query
from ranker.settings import Settings, get_settings

QUERY = "senior java near whitefield"


def _answer(**kw: str) -> QueryParseFallback:
    base = {
        "city": "none",
        "work_mode": "unspecified",
        "seniority": "unspecified",
        "employment_type": "unspecified",
        "min_salary_lpa": "none",
        "role_family": "backend",
    }
    return QueryParseFallback.model_validate({**base, **kw})


def _fake(**kw: str) -> FakeProvider:
    return FakeProvider({"QueryParseFallback": lambda _m: _answer(**kw)})


def _gateway(
    engine: AsyncEngine, haiku: Any, jev: Any | None = None, **overrides: object
) -> tuple[LLMGateway, Settings]:
    settings = get_settings().model_copy(update=overrides)
    extra = {"jev": ExtraProvider(jev, "typesafe/jev-router", Decimal("0.001"))} if jev else None
    return LLMGateway(engine, settings, haiku, extra), settings


async def _parse(
    engine: AsyncEngine,
    gw: LLMGateway | None,
    settings: Settings,
    q: str = QUERY,
    *,
    model: ParseModel = "haiku",
    auto: bool = True,
    explicit: SearchFilters | None = None,
) -> Any:
    return await parse_query(
        q, explicit, auto, engine=engine, gateway=gw, parse_model=model, settings=settings
    )


@pytest.mark.usefixtures("clean_db")
async def test_fully_parsed_query_makes_no_model_call(engine: AsyncEngine) -> None:
    haiku = _fake(city="Pune")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st, "backend python bengaluru 10 LPA")
    assert haiku.calls == []
    assert p.parse_source == "rules" and not p.degraded


@pytest.mark.usefixtures("clean_db")
async def test_fallback_fills_city_then_second_call_is_cached(engine: AsyncEngine) -> None:
    haiku = _fake(city="Bengaluru")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st)
    assert p.parse_source == "rules+haiku" and not p.cached and not p.degraded
    assert p.filters.cities == ["Bengaluru"]
    sources = {c.field: c.source for c in p.chips}
    assert sources == {"city": "haiku", "seniority": "rules"}
    assert len(haiku.calls) == 1
    # only the residue goes to the model, wrapped as untrusted data
    sent = haiku.calls[0]["messages"][0]["content"]
    assert sent == "<query>whitefield</query>"
    p2 = await _parse(engine, gw, st)
    assert len(haiku.calls) == 1
    assert p2.cached and p2.filters.cities == ["Bengaluru"] and p2.parse_source == "rules+haiku"
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT result::text FROM llm_enrichment_cache"))).all()
    assert len(rows) == 1
    stored = json.loads(rows[0][0])
    assert set(stored) == set(QueryParseFallback.model_fields)
    assert "whitefield" not in rows[0][0].lower() and "java" not in rows[0][0].lower()


@pytest.mark.usefixtures("clean_db")
async def test_jev_selected_per_request_and_cached_separately(engine: AsyncEngine) -> None:
    haiku, jev = _fake(city="Pune"), _fake(city="Bengaluru")
    gw, st = _gateway(engine, haiku, jev)
    p = await _parse(engine, gw, st, model="jev")
    assert p.parse_source == "rules+jev"
    assert {c.field: c.source for c in p.chips}["city"] == "jev"
    assert p.filters.cities == ["Bengaluru"]
    assert len(jev.calls) == 1 and haiku.calls == []
    assert jev.calls[0]["model"] == "typesafe/jev-router"
    h = await _parse(engine, gw, st, model="haiku")
    assert h.filters.cities == ["Pune"] and len(haiku.calls) == 1


@pytest.mark.usefixtures("clean_db")
async def test_jev_requested_but_disabled_uses_haiku(engine: AsyncEngine) -> None:
    haiku = _fake(city="Pune")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st, model="jev")
    assert p.parse_source == "rules+haiku" and len(haiku.calls) == 1


@pytest.mark.usefixtures("clean_db")
async def test_model_never_overrides_rules(engine: AsyncEngine) -> None:
    haiku = _fake(city="Bengaluru", seniority="junior", min_salary_lpa="20")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st, "senior java in Pune near Whitefield")
    assert p.filters.cities == ["Pune"]
    assert p.filters.seniority == ["senior"]
    assert p.filters.min_salary_lpa == 20  # unset by rules, so the model fills it
    assert {c.field: c.source for c in p.chips}["city"] == "rules"


@pytest.mark.usefixtures("clean_db")
async def test_explicit_user_filters_are_kept(engine: AsyncEngine) -> None:
    haiku = _fake(city="Bengaluru")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st, explicit=SearchFilters(cities=["Pune"]))
    assert p.filters.cities == ["Pune"]
    assert {c.field: c.source for c in p.chips}["city"] == "user"


@pytest.mark.usefixtures("clean_db")
async def test_auto_filters_off_makes_no_call(engine: AsyncEngine) -> None:
    haiku = _fake(city="Bengaluru")
    gw, st = _gateway(engine, haiku)
    p = await _parse(engine, gw, st, auto=False)
    assert haiku.calls == [] and p.parse_source == "rules" and not p.degraded


class _Sleepy(FakeProvider):
    async def parse(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        await asyncio.sleep(2)
        return await super().parse(**kwargs)


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "provider",
    [
        FakeProvider({"QueryParseFallback": LLMUnavailable("down")}),
        FakeProvider({"QueryParseFallback": BudgetExceeded()}),
        FakeProvider(),  # no response configured: a non-LLM error must not escape either
        _Sleepy({"QueryParseFallback": lambda _m: _answer(city="Pune")}),
    ],
    ids=["unavailable", "budget", "unexpected", "timeout"],
)
async def test_failures_degrade_to_rules_only(engine: AsyncEngine, provider: FakeProvider) -> None:
    gw, st = _gateway(engine, provider, parse_fallback_timeout_seconds=0.05)
    p = await _parse(engine, gw, st)
    assert p.degraded is True
    assert p.parse_source == "rules"
    assert p.filters.cities == [] and p.filters.seniority == ["senior"]


@pytest.mark.usefixtures("clean_db")
async def test_default_timeout_is_1_2_seconds() -> None:
    assert get_settings().parse_fallback_timeout_seconds == 1.2


@pytest.mark.usefixtures("clean_db")
async def test_no_gateway_or_unavailable_gateway_degrades_only_when_needed(
    engine: AsyncEngine,
) -> None:
    st = get_settings()
    needed = await _parse(engine, None, st)
    assert needed.degraded is True and needed.filters.seniority == ["senior"]
    done = await _parse(engine, None, st, "backend python bengaluru 10 LPA")
    assert done.degraded is False

    class _NoKey(FakeProvider):
        def available(self) -> bool:
            return False

    gw, st2 = _gateway(engine, _NoKey())
    assert (await _parse(engine, gw, st2)).degraded is True


def test_search_api_stays_200_and_degraded_when_fallback_fails(
    client: TestClient, database_url: str
) -> None:
    # The keyless fake gateway has no QueryParseFallback response and raises inside the call.
    r = client.get("/api/jobs/search", params={"q": QUERY})
    assert r.status_code == 200
    assert r.json()["degraded"] is True
    assert r.json()["parsed"]["parse_source"] == "rules"
