"""D-17 parse eval: gold loading, per-variant scoring, forced fallback, not-run variants."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner

from ranker import cli
from ranker.cli import EVAL_DATA_DIR
from ranker.evals.parse_eval import (
    ParseCase,
    ParseEvalError,
    ParseGold,
    load_parse_cases,
    report_markdown,
    run_parse_eval,
)
from ranker.llm.client import ExtraProvider, LLMGateway
from ranker.llm.providers.fake import FakeProvider
from ranker.llm.schemas import QueryParseFallback
from ranker.settings import get_settings

GOLD_FILE = EVAL_DATA_DIR / "parse_queries.jsonl"


def _answer(**kw: str) -> QueryParseFallback:
    base = {
        "city": "Bengaluru",
        "work_mode": "unspecified",
        "seniority": "unspecified",
        "employment_type": "unspecified",
        "min_salary_lpa": "none",
        "role_family": "backend",
    }
    return QueryParseFallback.model_validate({**base, **kw})


def _fake() -> FakeProvider:
    return FakeProvider({"QueryParseFallback": lambda _m: _answer()})


def _case(cid: str, query: str, **gold: Any) -> ParseCase:
    base = {
        "city": "none",
        "min_salary_lpa": None,
        "work_mode": "unspecified",
        "seniority": "unspecified",
        "role_family": "backend",
    }
    return ParseCase(
        id=cid,
        query=query,
        gold=ParseGold(**{**base, **gold}),
        labelled_by="test",
        reviewed=False,
    )


CASES = [
    _case("a", "backend python bengaluru 10 LPA", city="Bengaluru", min_salary_lpa=10),
    _case("b", "senior java near whitefield", city="Bengaluru", seniority="senior"),
]


def test_loader_reports_line_of_missing_city(tmp_path: Path) -> None:
    good = GOLD_FILE.read_text(encoding="utf-8").splitlines()[0]
    bad = json.loads(good)
    del bad["gold"]["city"]
    path = tmp_path / "q.jsonl"
    path.write_text(good + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ParseEvalError, match=r"line 2: gold\.city"):
        load_parse_cases(path)


def test_loader_rejects_value_outside_closed_set(tmp_path: Path) -> None:
    rec = json.loads(GOLD_FILE.read_text(encoding="utf-8").splitlines()[0])
    rec["gold"]["min_salary_lpa"] = 11
    path = tmp_path / "q.jsonl"
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    with pytest.raises(ParseEvalError, match=r"line 1: gold\.min_salary_lpa"):
        load_parse_cases(path)


def test_shipped_gold_set_is_valid() -> None:
    cases = load_parse_cases(GOLD_FILE)
    assert 45 <= len(cases) <= 60
    assert len({c.id for c in cases}) == len(cases)
    assert all(c.labelled_by and not c.reviewed for c in cases)
    assert sum(c.gold.city == "none" and c.gold.min_salary_lpa is None for c in cases) >= 10


def _gateway(engine: AsyncEngine, jev: bool = False) -> tuple[LLMGateway, FakeProvider]:
    haiku = _fake()
    extra = (
        {"jev": ExtraProvider(_fake(), "typesafe/jev-router", Decimal("0.001"))} if jev else None
    )
    return LLMGateway(engine, get_settings(), haiku, extra), haiku


@pytest.mark.usefixtures("clean_db")
async def test_rules_variant_makes_no_model_calls_and_has_no_role_family(
    engine: AsyncEngine,
) -> None:
    gw, haiku = _gateway(engine)
    report = await run_parse_eval(engine, gw, get_settings(), CASES, ["rules"])
    res = report.results[0]
    assert res.status == "ok" and res.n == 2 and haiku.calls == []
    assert res.accuracy["role_family"] is None
    assert res.accuracy["city"] == 0.5  # case a by rules; whitefield is unknown to rules
    assert res.accuracy["min_salary"] == 1.0
    assert res.cost_usd_total == 0.0 and res.models_used == {}
    assert res.latency_p50_ms is not None and res.latency_p95_ms is not None


@pytest.mark.usefixtures("clean_db")
async def test_haiku_variant_calls_model_for_every_query_and_bypasses_cache(
    engine: AsyncEngine,
) -> None:
    gw, haiku = _gateway(engine)
    first = await run_parse_eval(engine, gw, get_settings(), CASES, ["haiku"])
    assert len(haiku.calls) == 2  # even the fully parsed query a is sent to the model
    second = await run_parse_eval(engine, gw, get_settings(), CASES, ["haiku"])
    assert len(haiku.calls) == 4  # no cache hit on the repeat run
    res = first.results[0]
    assert res.accuracy["city"] == 1.0  # model fills whitefield; rules city for a is kept
    assert res.accuracy["role_family"] == 1.0
    assert sum(res.models_used.values()) == 2
    assert res.cost_usd_total is not None and res.cost_per_1k_usd is not None
    assert second.results[0].model_calls_degraded == 0


@pytest.mark.usefixtures("clean_db")
async def test_jev_without_provider_is_not_run(engine: AsyncEngine) -> None:
    gw, _ = _gateway(engine)
    report = await run_parse_eval(engine, gw, get_settings(), CASES, ["jev"])
    res = report.results[0]
    assert (res.variant, res.status, res.reason) == (
        "jev",
        "not run",
        "jev provider not configured",
    )
    assert "not run" in report_markdown(report)


@pytest.mark.usefixtures("clean_db")
async def test_jev_variant_uses_jev_and_logs_routed_model(engine: AsyncEngine) -> None:
    gw, haiku = _gateway(engine, jev=True)
    report = await run_parse_eval(engine, gw, get_settings(), CASES, ["jev"])
    res = report.results[0]
    assert res.status == "ok" and haiku.calls == []
    assert "typesafe/jev-router" in res.models_used
    assert res.cost_usd_total is not None and res.cost_usd_total > 0


@pytest.mark.usefixtures("clean_db")
async def test_failed_model_call_counts_as_degraded_not_error(engine: AsyncEngine) -> None:
    haiku = FakeProvider({"QueryParseFallback": RuntimeError("boom")})
    gw = LLMGateway(engine, get_settings(), haiku)
    report = await run_parse_eval(engine, gw, get_settings(), CASES, ["haiku"])
    assert report.results[0].model_calls_degraded == 2
    assert "| haiku |" in report_markdown(report)


runner = CliRunner()


def _tiny_gold(tmp_path: Path) -> Path:
    lines = GOLD_FILE.read_text(encoding="utf-8").splitlines()[:3]
    path = tmp_path / "q.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cli_parse_requires_yes_for_model_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    res = runner.invoke(cli.app, ["eval", "parse", "--queries", str(_tiny_gold(tmp_path))])
    assert res.exit_code == 2
    assert "ConfirmationRequired" in res.output and '"estimated_model_calls": 6' in res.output
    bad = runner.invoke(cli.app, ["eval", "parse", "--variants", "gpt"])
    assert bad.exit_code == 2 and "UnknownVariant" in bad.output
    missing = runner.invoke(cli.app, ["eval", "parse", "--queries", str(tmp_path / "nope")])
    assert missing.exit_code == 2


def test_cli_parse_runs_rules_haiku_and_reports_jev_not_run(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    fake = _fake()

    def fake_gateway(engine: AsyncEngine, settings: Any) -> LLMGateway:
        return LLMGateway(engine, settings, fake)

    monkeypatch.setattr("ranker.llm.client.build_gateway", fake_gateway)
    out = tmp_path / "result.json"
    res = runner.invoke(
        cli.app,
        [
            "eval",
            "parse",
            "--queries",
            str(_tiny_gold(tmp_path)),
            "--out",
            str(out),
            "--yes",
        ],
    )
    get_settings.cache_clear()
    assert res.exit_code == 0, res.output
    saved = json.loads(out.read_text(encoding="utf-8"))["report"]
    by_variant = {r["variant"]: r for r in saved["results"]}
    assert by_variant["rules"]["status"] == "ok"
    assert by_variant["haiku"]["status"] == "ok" and len(fake.calls) == 3
    assert by_variant["jev"]["status"] == "not run"
    assert by_variant["jev"]["reason"] == "jev provider not configured"
    assert "| haiku |" in res.output


def test_cli_parse_rules_only_needs_no_confirmation(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    res = runner.invoke(
        cli.app,
        [
            "eval",
            "parse",
            "--queries",
            str(_tiny_gold(tmp_path)),
            "--variants",
            "rules",
            "--out",
            str(tmp_path / "r.json"),
        ],
    )
    get_settings.cache_clear()
    assert res.exit_code == 0, res.output
