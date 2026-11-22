import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from ranker import cli
from ranker.config_loader import clear_config_cache
from ranker.evals.ingest_eval import (
    EvalRecord,
    EvalSetError,
    GoldLabels,
    capture_samples,
    load_eval_set,
    score_normalisation,
)
from ranker.ingest.normalize.job import NormalizedJob, normalize_job
from ranker.ingest.sources import RawJob, apiutil
from ranker.settings import get_settings

runner = CliRunner()


def _raw(title: str, source: str = "t", **kw: Any) -> RawJob:
    return RawJob(
        source=source,
        source_job_id=title,
        url="https://x.test/1",  # pyright: ignore[reportArgumentType]
        title=title,
        company_name="Acme",
        description_raw="Join us in Bengaluru. Work with Python.",
        location_text="Bengaluru, India",
        **kw,
    )


def _gold_from(raw: RawJob) -> GoldLabels:
    job = normalize_job(raw)
    assert isinstance(job, NormalizedJob)
    return GoldLabels(
        in_scope=True,
        remote_type=job.remote_type,
        onsite_days_per_week=job.onsite_days_per_week,
        remote_eligibility=job.remote_eligibility,
        seniority=job.seniority,
        employment_type=job.employment_type,
        salary_min_inr_annual=float(job.salary_min_inr_annual)
        if job.salary_min_inr_annual is not None
        else None,
        salary_max_inr_annual=float(job.salary_max_inr_annual)
        if job.salary_max_inr_annual is not None
        else None,
        city=job.city,
        country_code=job.country_code,
        industry=job.industry,
    )


def _rec(raw: RawJob, gold: GoldLabels | None = None) -> EvalRecord:
    return EvalRecord(raw=raw, gold=gold or _gold_from(raw), labelled_by="test", reviewed=False)


def _four() -> list[EvalRecord]:
    return [
        _rec(
            _raw(
                "Senior Python Engineer",
                salary_min=2400000,
                salary_max=3000000,
                salary_currency="INR",
            )
        ),
        _rec(_raw("Backend Engineer", source="u")),
        _rec(_raw("Senior Data Engineer", source="u")),
        _rec(_raw("Lead Python Developer")),
    ]


def test_perfect_predictions_score_one() -> None:
    report = score_normalisation(_four())
    assert report.n == 4
    assert set(report.per_source) == {"t", "u"}
    for metrics in report.fields.values():
        assert metrics.accuracy == 1.0


def test_unknown_prediction_lowers_accuracy_and_coverage_not_precision() -> None:
    recs = _four()
    recs[1] = _rec(recs[1].raw, recs[1].gold.model_copy(update={"seniority": "senior"}))
    m = score_normalisation(recs).fields["seniority"]
    assert (m.accuracy, m.coverage, m.precision) == (0.75, 0.75, 1.0)


def test_salary_tolerance_is_five_percent() -> None:
    base = _four()
    first = base[0]
    for factor, ok in ((1.03, True), (1.10, False)):
        gold = first.gold.model_copy(
            update={
                "salary_min_inr_annual": (first.gold.salary_min_inr_annual or 0) * factor,
                "salary_max_inr_annual": (first.gold.salary_max_inr_annual or 0) * factor,
            }
        )
        m = score_normalisation([_rec(first.raw, gold)]).fields["salary"]
        assert (m.accuracy == 1.0) is ok


def test_scope_skip_on_in_scope_gold_counts_only_for_in_scope() -> None:
    skip = _raw("Marketing Manager")
    recs = [*_four(), _rec(skip, GoldLabels(in_scope=True))]
    report = score_normalisation(recs)
    assert report.fields["in_scope"].accuracy == 0.8
    assert report.fields["seniority"].n == 4


def test_gold_out_of_scope_is_scored_only_for_scope() -> None:
    skip = _raw("Marketing Manager")
    report = score_normalisation([_rec(skip, GoldLabels(in_scope=False))])
    assert report.fields["in_scope"].accuracy == 1.0
    assert "seniority" not in report.fields


def _write(path: Path, recs: list[EvalRecord]) -> None:
    path.write_text("\n".join(r.model_dump_json() for r in recs) + "\n", encoding="utf-8")


def test_load_rejects_bad_label_with_line_number(tmp_path: Path) -> None:
    good = _four()[0].model_dump(mode="json")
    bad = json.loads(json.dumps(good))
    bad["gold"]["seniority"] = "wizard"
    path = tmp_path / "p.jsonl"
    path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(EvalSetError, match=r"line 2: gold\.seniority"):
        load_eval_set(path)


def test_cli_eval_ingest_json_markdown_and_missing(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    _write(path, _four())
    res = runner.invoke(cli.app, ["eval", "ingest", "--fixture", str(path)])
    assert res.exit_code == 0
    body = json.loads(res.stdout)
    assert body["n"] == 4 and body["normalizer_version"] and "seniority" in body["fields"]
    assert set(body["per_source"]) == {"t", "u"}
    md = runner.invoke(cli.app, ["eval", "ingest", "--fixture", str(path), "--markdown"])
    assert md.exit_code == 0 and "| seniority |" in md.stdout
    missing = runner.invoke(cli.app, ["eval", "ingest", "--fixture", str(tmp_path / "nope")])
    assert missing.exit_code == 2


@pytest.fixture
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(seconds: float) -> None:
        return None

    monkeypatch.setattr(apiutil, "sleep", fake)
    clear_config_cache()


@respx.mock
@pytest.mark.usefixtures("_no_waiting")
async def test_capture_scrubs_pii_and_truncates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused/unused")
    get_settings.cache_clear()
    fix = Path(__file__).parent / "fixtures"
    p1 = json.loads((fix / "arbeitnow_page1.json").read_text())
    p1["data"][0]["description"] = (
        "<p>Mail jobs@acme.example or call +49 170 1234567 now.</p>" + "<p>filler</p>" * 1000
    )
    p1["links"] = {}
    respx.get("https://www.arbeitnow.com/api/job-board-api").mock(
        return_value=httpx.Response(200, json=p1)
    )
    out = tmp_path / "out.jsonl"
    engine = create_async_engine("postgresql+psycopg://unused/unused")
    try:
        written = await capture_samples(engine, get_settings(), "arbeitnow", 2, out)
    finally:
        await engine.dispose()
        get_settings.cache_clear()
    assert written == 2
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert all(rec["gold"] is None for rec in lines)
    blob = out.read_text()
    assert "jobs@acme.example" not in blob and "1234567" not in blob
    assert all(len(rec["raw"]["description_raw"]) <= 3000 for rec in lines)


def _sql(url: str, stmt: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def go() -> list[Any]:
        eng = create_async_engine(url)
        try:
            async with eng.begin() as conn:
                res = await conn.execute(text(stmt), params or {})
                return list(res.mappings().all()) if res.returns_rows else []
        finally:
            await eng.dispose()

    return asyncio.run(go())


def test_runs_report_prints_quality_counters(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    _sql(database_url, "TRUNCATE ingest_runs RESTART IDENTITY CASCADE")
    _sql(
        database_url,
        "INSERT INTO ingest_runs (source, mode, started_at, finished_at, status, fetched, "
        "in_scope, inserted, updated, expired, duplicates_grouped, ghost_flagged, reposts) "
        "VALUES ('remotive', 'incremental', now(), now(), 'succeeded', 16, 14, 3, 2, 1, 4, 5, 6)",
    )
    _sql(
        database_url,
        "INSERT INTO ingest_runs (source, mode, started_at, status) "
        "VALUES ('old', 'incremental', now() - interval '3 days', 'succeeded')",
    )
    res = runner.invoke(cli.app, ["runs", "report", "--since-hours", "24"])
    assert res.exit_code == 0
    rows = [json.loads(line) for line in res.stdout.splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "remotive" and row["status"] == "succeeded"
    assert (row["fetched"], row["in_scope"], row["inserted"], row["updated"]) == (16, 14, 3, 2)
    assert (row["closed"], row["duplicates_grouped"], row["ghost_flagged"], row["reposts"]) == (
        1,
        4,
        5,
        6,
    )
    get_settings.cache_clear()


def test_ambiguous_scope_gold_is_excluded_from_every_metric() -> None:
    skip = _raw("Marketing Manager")
    report = score_normalisation([_rec(skip, GoldLabels(in_scope=None))])
    assert report.n == 1 and report.fields == {}


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ingest_eval"
# Internshala is absent: its first request returned a bot-challenge page on 2026-11-22 and the
# fetcher stops there by design (D-15). Adzuna has no keys (ADR 0001).
EXPECTED_SOURCES = {"greenhouse", "ashby", "lever", "arbeitnow", "remoteok", "remotive", "jobicy"}


def test_real_eval_set_is_complete_and_scrubbed() -> None:
    path = FIXTURE_DIR / "postings.jsonl"
    records = load_eval_set(path)
    assert len(records) >= 75
    assert {r.raw.source for r in records} == EXPECTED_SOURCES
    assert sum(r.gold.in_scope is False for r in records) >= 10
    assert sum(r.gold.in_scope is True for r in records) >= 8
    assert all(r.labelled_by for r in records)
    assert all(len(r.raw.description_raw) <= 3000 for r in records)
    blob = path.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob)


def test_normalisation_meets_recorded_floors() -> None:
    floors = json.loads((FIXTURE_DIR / "floors.json").read_text())["fields"]
    report = score_normalisation(load_eval_set(FIXTURE_DIR / "postings.jsonl"))
    assert set(floors) == set(report.fields)
    below = {
        f: (report.fields[f].accuracy, floor)
        for f, floor in floors.items()
        if report.fields[f].accuracy < floor
    }
    assert not below, f"accuracy below floor (measured, floor): {below}"
