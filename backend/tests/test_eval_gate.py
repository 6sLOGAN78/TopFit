import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typer.testing import CliRunner

from ranker import cli
from ranker.embeddings.embedder import FakeEmbedder
from ranker.evals.ranking_eval import (
    Interval,
    RankingEvalError,
    RankingReport,
    check_gate,
    load_judgments,
    load_personas,
    report_markdown,
    run_ranking_eval,
)
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import add_job

FIXTURES = Path(__file__).parent / "fixtures" / "ranking_eval"
RANKING_YAML = Path(__file__).resolve().parents[1] / "config" / "ranking.yaml"
AS_OF = datetime(2026, 11, 22, tzinfo=UTC)
runner = CliRunner()


def _report(ndcg: float) -> RankingReport:
    iv = Interval(mean=ndcg, lo=ndcg, hi=ndcg)
    return RankingReport(
        pipeline_version="x",
        vector_mode="exact",
        variant="hybrid_signals",
        as_of=AS_OF.isoformat(),
        personas=1,
        skipped_personas=[],
        aggregate={"ndcg_at_10": iv, "recall_at_50": iv, "mrr": iv, "p_at_5": iv},
        unjudged_at_10_mean=0.0,
        per_persona=[],
    )


def _baseline(tmp_path: Path, ndcg: float) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"ndcg_at_10": ndcg}))
    return path


def test_gate_fail_pass_and_missing(tmp_path: Path) -> None:
    base = _baseline(tmp_path, 0.60)
    failed = check_gate(_report(0.57), base, 0.02)
    assert failed.status == "fail"
    assert failed.drop == pytest.approx(0.03)
    assert check_gate(_report(0.59), base, 0.02).status == "pass"
    assert check_gate(_report(0.70), base, 0.02).status == "pass"
    assert check_gate(_report(0.1), tmp_path / "nope.json", 0.02).status == "no_baseline"
    assert check_gate(_report(0.1), None, 0.02).status == "no_baseline"


def test_loaders_report_line_numbers(tmp_path: Path) -> None:
    bad = tmp_path / "p.jsonl"
    bad.write_text('{"id": "a", "query": "q"}\n{"id": "b"}\n')
    with pytest.raises(RankingEvalError, match=r"p.jsonl:2"):
        load_personas(bad)
    bad.write_text(
        '{"persona_id": "a", "job_id": 1, "grade": 9, "graded_by": "x", "rubric_version": "v"}\n'
    )
    with pytest.raises(RankingEvalError, match=r"p.jsonl:1"):
        load_judgments(bad)


def test_effective_grade_prefers_human() -> None:
    judgments = load_judgments(FIXTURES / "judgments.jsonl")
    reviewed = next(j for j in judgments if j.reviewed)
    assert reviewed.grade == 2
    assert reviewed.effective_grade == 3


async def _seed_jobs(engine: AsyncEngine) -> None:
    await add_job(
        engine,
        title="Senior Python Backend Engineer",
        skills=["Python", "Kafka"],
        description="senior python backend kafka services",
    )
    await add_job(
        engine, title="Python Developer", skills=["Python"], description="python developer apis"
    )
    await add_job(engine, title="Gardener", description="plants trees lawn")
    await add_job(
        engine,
        title="React Frontend Engineer",
        skills=["React"],
        description="react frontend engineer",
    )


@pytest.mark.usefixtures("clean_db")
async def test_run_ranking_eval_exact_and_deterministic(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_jobs(engine)
    src = RANKING_YAML
    hnsw = tmp_path / "ranking.yaml"
    hnsw.write_text(src.read_text().replace("vector_mode: exact", "vector_mode: hnsw"))
    assert "vector_mode: hnsw" in hnsw.read_text()
    personas = load_personas(FIXTURES / "personas.jsonl")
    judgments = load_judgments(FIXTURES / "judgments.jsonl")
    before = os.environ.get("CONFIG_DIR")

    async def run() -> RankingReport:
        return await run_ranking_eval(
            engine,
            FakeEmbedder(),
            get_settings(),
            personas,
            judgments,
            as_of=AS_OF,
            ranking_config_path=hnsw,
        )

    first = await run()
    second = await run()
    assert first.vector_mode == "exact"
    assert os.environ.get("CONFIG_DIR") == before
    assert first.model_dump_json() == second.model_dump_json()
    assert first.personas == 3
    assert [r.id for r in first.per_persona] == ["p1", "p2", "p3"]
    for name in ("ndcg_at_10", "recall_at_50", "mrr", "p_at_5"):
        iv = first.aggregate[name]
        assert iv.mean is not None
        assert iv.lo is not None
        assert iv.hi is not None
        assert iv.lo <= iv.mean <= iv.hi
    assert first.aggregate["recall_at_50"].mean == 1.0
    assert "| metric | mean | 95% CI |" in report_markdown(first)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM search_logs"))).scalar_one() == 0


@pytest.mark.usefixtures("clean_db")
async def test_variants_run(engine: AsyncEngine) -> None:
    await _seed_jobs(engine)
    personas = load_personas(FIXTURES / "personas.jsonl")
    judgments = load_judgments(FIXTURES / "judgments.jsonl")
    for variant in ("lex", "vec", "hybrid", "hybrid_signals"):
        report = await run_ranking_eval(
            engine,
            FakeEmbedder(),
            get_settings(),
            personas,
            judgments,
            as_of=AS_OF,
            variant=variant,  # pyright: ignore[reportArgumentType]
        )
        assert report.variant == variant
        assert report.personas == 3


@pytest.fixture
def seeded_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    async def seed() -> None:
        eng = create_async_engine(database_url)
        try:
            async with eng.begin() as conn:
                names = [
                    r[0]
                    for r in await conn.execute(
                        text(
                            "SELECT quote_ident(tablename) FROM pg_tables "
                            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                        )
                    )
                ]
                await conn.execute(
                    text("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY CASCADE")
                )
            await _seed_jobs(eng)
        finally:
            await eng.dispose()

    asyncio.run(seed())
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    return database_url


def _invoke(tmp_path: Path, *extra: str) -> tuple[int, str]:
    res = runner.invoke(
        cli.app,
        [
            "eval",
            "ranking",
            "--personas",
            str(FIXTURES / "personas.jsonl"),
            "--judgments",
            str(FIXTURES / "judgments.jsonl"),
            "--as-of",
            "2026-11-22T00:00:00+00:00",
            "--out",
            str(tmp_path / "results"),
            *extra,
        ],
    )
    return res.exit_code, res.output


def test_cli_gate_exit_codes_and_result_file(seeded_url: str, tmp_path: Path) -> None:
    code, out = _invoke(tmp_path)  # no baseline: exit 0 with a notice
    assert code == 0, out
    assert "no baseline" in out
    assert "| metric | mean | 95% CI |" in out
    files = list((tmp_path / "results").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    ndcg = data["report"]["aggregate"]["ndcg_at_10"]["mean"]

    code, out = _invoke(tmp_path, "--baseline", str(_baseline(tmp_path, ndcg + 0.03)))
    assert code == 1
    assert '"gate": "failed"' in out
    assert '"drop"' in out

    code, out = _invoke(tmp_path, "--baseline", str(_baseline(tmp_path, ndcg + 0.01)))
    assert code == 0
    assert '"gate": "passed"' in out


def test_cli_usage_errors(tmp_path: Path) -> None:
    res = runner.invoke(cli.app, ["eval", "ranking", "--personas", str(tmp_path / "missing.jsonl")])
    assert res.exit_code == 2
    assert _invoke(tmp_path, "--mode", "llm")[0] == 2
    assert _invoke(tmp_path, "--variant", "nope")[0] == 2
