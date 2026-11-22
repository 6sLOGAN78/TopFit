import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.evals.grading import (
    RUBRIC_VERSION,
    GradeOut,
    grade_pool,
    load_pool,
    pool_candidates,
)
from ranker.evals.ranking_eval import VARIANTS, Persona, exact_config, load_judgments
from ranker.llm.client import LLMGateway
from ranker.llm.errors import LLMUnavailable
from ranker.llm.providers.fake import FakeProvider
from ranker.llm.schemas import lint_schema
from ranker.search.service import SearchOptions, SearchRequest, search
from ranker.settings import get_settings
from tests.fixtures.search.jobs_seed import add_job

pytestmark = pytest.mark.usefixtures("clean_db")

AS_OF = datetime(2026, 11, 22, tzinfo=UTC)
PERSONAS = [
    Persona(id="p1", query="python backend engineer"),
    Persona(id="p2", query="react frontend developer"),
]


async def _seed(engine: AsyncEngine) -> list[int]:
    ids: list[int] = []
    for i in range(30):
        py = i % 2 == 0
        ids.append(
            await add_job(
                engine,
                title=f"{'Python Backend' if py else 'React Frontend'} Engineer {i}",
                skills=["Python"] if py else ["React"],
                description=f"{'python backend' if py else 'react frontend'} role number {i}",
                city="Bengaluru",
                country_code="IN",
                embed_text=f"job-{i}",
            )
        )
    return ids


async def _expected(engine: AsyncEngine, persona: Persona) -> set[int]:
    out: set[int] = set()
    with exact_config():
        for legs, signals in VARIANTS.values():
            resp = await search(
                engine,
                FakeEmbedder(),
                None,
                get_settings(),
                SearchRequest(q=persona.query, filters=None, auto_filters=True),
                options=SearchOptions(
                    legs=legs, apply_signals=signals, log=False, now=AS_OF, page_size=20
                ),
            )
            out |= {it.id for it in resp.items[:20]}
    return out


async def test_pool_is_union_of_four_variants(engine: AsyncEngine, tmp_path: Path) -> None:
    await _seed(engine)
    pool_path = tmp_path / "pool.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    pooled = await pool_candidates(
        engine,
        FakeEmbedder(),
        get_settings(),
        PERSONAS,
        as_of=AS_OF,
        pool_path=pool_path,
        judgments_path=judgments_path,
    )
    for p in PERSONAS:
        assert len(pooled[p.id]) == len(set(pooled[p.id]))
        assert set(pooled[p.id]) == await _expected(engine, p)
        assert 20 <= len(pooled[p.id]) <= 80
    lines = [json.loads(x) for x in pool_path.read_text().splitlines()]
    assert len(lines) == sum(len(v) for v in pooled.values())
    assert all(row["variants"] for row in lines)
    assert not judgments_path.exists()
    assert load_pool(pool_path) == pooled


async def test_grade_pool_writes_judgments_and_resumes(engine: AsyncEngine, tmp_path: Path) -> None:
    ids = await _seed(engine)
    pool = {"p1": ids[:3], "p2": ids[3:5]}
    provider = FakeProvider(
        {"GradeOut": lambda _m: GradeOut(grade=2, rationale_code="partial_skill_match")}
    )
    settings = get_settings()
    gateway = LLMGateway(engine, settings, provider)
    path = tmp_path / "judgments.jsonl"
    stats = await grade_pool(gateway, engine, PERSONAS, pool, path, model_label="claude-sonnet-5-5")
    assert (stats.graded, stats.stopped) == (5, None)
    rows = load_judgments(path)
    assert len(rows) == 5
    assert {(r.graded_by, r.reviewed, r.human_grade, r.rubric_version, r.grade) for r in rows} == {
        ("claude-sonnet-5-5", False, None, RUBRIC_VERSION, 2)
    }
    call = provider.calls[0]
    assert "untrusted" in call["system"]
    assert "<persona>" in call["messages"][0]["content"]
    assert "<job>" in call["messages"][0]["content"]

    again = await grade_pool(gateway, engine, PERSONAS, pool, path, model_label="m")
    assert (again.graded, again.skipped) == (0, 5)
    assert len(provider.calls) == 5


async def test_budget_exceeded_stops_and_resume_skips_graded(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    ids = await _seed(engine)
    pool = {"p1": ids[:4]}
    path = tmp_path / "judgments.jsonl"
    ok = FakeProvider({"GradeOut": lambda _m: GradeOut(grade=1, rationale_code="wrong_role")})
    await grade_pool(
        LLMGateway(engine, get_settings(), ok), engine, PERSONAS, {"p1": ids[:2]}, path,
        model_label="m",
    )  # fmt: skip
    broke = LLMGateway(
        engine, get_settings().model_copy(update={"llm_monthly_budget_usd": 0.0}), ok
    )
    stats = await grade_pool(broke, engine, PERSONAS, pool, path, model_label="m")
    assert stats.stopped == "budget_exceeded"
    assert stats.graded == 0
    assert len(stats.pending) == 2
    assert len(load_judgments(path)) == 2  # earlier progress intact


async def test_llm_unavailable_stops_cleanly(engine: AsyncEngine, tmp_path: Path) -> None:
    ids = await _seed(engine)
    provider = FakeProvider({"GradeOut": LLMUnavailable("down")})
    stats = await grade_pool(
        LLMGateway(engine, get_settings(), provider),
        engine,
        PERSONAS,
        {"p1": ids[:2]},
        tmp_path / "j.jsonl",
        model_label="m",
    )
    assert stats.stopped == "llm_unavailable"


def test_grade_schema_passes_lint() -> None:
    assert lint_schema(GradeOut) == []
