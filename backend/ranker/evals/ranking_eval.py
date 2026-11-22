"""Offline ranking evaluation (EVAL-03, EVAL-08).

Runs the production ``search()`` path with no LLM, no logging and exact vector search over a
persona set and scores it against graded judgments. Exact mode is forced by pointing
``CONFIG_DIR`` at a temporary copy of the config directory whose ``vector_mode`` is rewritten
(byte-preserving, so ``pipeline_version`` only changes when the real config does).
"""

import json
import math
import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import clear_config_cache, config_dir
from ranker.embeddings.embedder import Embedder
from ranker.evals.metrics import (
    bootstrap_ci,
    mrr,
    ndcg_at_k,
    p_at_k,
    recall_at_k,
    unjudged_rate_at_k,
)
from ranker.search.config import pipeline_version, ranking_config
from ranker.search.models import SearchFilters
from ranker.search.service import PROMPT_VERSION, SearchOptions, SearchRequest, search
from ranker.settings import Settings

Variant = Literal["lex", "vec", "hybrid", "hybrid_signals"]
METRICS = ("ndcg_at_10", "recall_at_50", "mrr", "p_at_5")
EVAL_PAGE_SIZE = 50
_VECTOR_MODE = re.compile(r"^(\s*vector_mode:\s*)\S+", re.MULTILINE)

VARIANTS: dict[Variant, tuple[Literal["both", "lex", "vec"], bool]] = {
    "lex": ("lex", False),
    "vec": ("vec", False),
    "hybrid": ("both", False),
    "hybrid_signals": ("both", True),
}


class RankingEvalError(ValueError):
    """Bad persona/judgment file; the message carries the 1-based line number."""


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    explicit_filters: SearchFilters | None = None
    notes: str | None = None


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str
    job_id: int
    grade: int = Field(ge=0, le=3)
    graded_by: str
    reviewed: bool = False
    human_grade: int | None = Field(default=None, ge=0, le=3)
    rubric_version: str

    @property
    def effective_grade(self) -> int:
        """The human grade wins where the owner reviewed it (D-14)."""
        return self.human_grade if self.human_grade is not None else self.grade


def _load[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    out: list[M] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                out.append(model.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise RankingEvalError(f"{path.name}:{n}: {type(exc).__name__}") from exc
    return out


def load_personas(path: Path) -> list[Persona]:
    return _load(path, Persona)


def load_judgments(path: Path) -> list[Judgment]:
    return _load(path, Judgment)


class PersonaResult(BaseModel):
    id: str
    ndcg_at_10: float
    recall_at_50: float | None
    mrr: float
    p_at_5: float
    unjudged_at_10: float
    judged: int


class Interval(BaseModel):
    mean: float | None
    lo: float | None
    hi: float | None


class RankingReport(BaseModel):
    pipeline_version: str
    vector_mode: str
    variant: Variant
    as_of: str
    personas: int
    skipped_personas: list[str]
    aggregate: dict[str, Interval]
    unjudged_at_10_mean: float | None
    per_persona: list[PersonaResult]


def _num(v: float) -> float | None:
    return None if math.isnan(v) else v


@contextmanager
def exact_config(ranking_yaml: Path | None = None) -> Generator[None]:
    """Point CONFIG_DIR at a copy of the config with ``vector_mode: exact`` (D-16)."""
    src = config_dir()
    prev = os.environ.get("CONFIG_DIR")
    with tempfile.TemporaryDirectory(prefix="ranker-eval-cfg-") as tmp:
        dst = Path(tmp)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        if ranking_yaml is not None:
            shutil.copyfile(ranking_yaml, dst / "ranking.yaml")
        target = dst / "ranking.yaml"
        target.write_text(
            _VECTOR_MODE.sub(r"\1exact", target.read_text(encoding="utf-8")), encoding="utf-8"
        )
        os.environ["CONFIG_DIR"] = str(dst)
        clear_config_cache()
        try:
            yield
        finally:
            if prev is None:
                os.environ.pop("CONFIG_DIR", None)
            else:
                os.environ["CONFIG_DIR"] = prev
            clear_config_cache()


async def run_ranking_eval(
    engine: AsyncEngine,
    embedder: Embedder | None,
    settings: Settings,
    personas: list[Persona],
    judgments: list[Judgment],
    *,
    as_of: datetime,
    variant: Variant = "hybrid_signals",
    ranking_config_path: Path | None = None,
) -> RankingReport:
    legs, signals = VARIANTS[variant]
    judged: dict[str, dict[int, int]] = defaultdict(dict)
    for j in judgments:
        judged[j.persona_id][j.job_id] = j.effective_grade
    results: list[PersonaResult] = []
    skipped: list[str] = []
    with exact_config(ranking_config_path):
        mode = ranking_config().retrieval.vector_mode
        pv = pipeline_version(PROMPT_VERSION)
        for p in personas:
            grades = judged.get(p.id)
            if not grades:
                skipped.append(p.id)
                continue
            resp = await search(
                engine,
                embedder,
                None,
                settings,
                SearchRequest(q=p.query, filters=p.explicit_filters, auto_filters=True),
                options=SearchOptions(
                    legs=legs,
                    apply_signals=signals,
                    log=False,
                    now=as_of,
                    page_size=EVAL_PAGE_SIZE,
                ),
            )
            ranked = [item.id for item in resp.items]
            results.append(
                PersonaResult(
                    id=p.id,
                    ndcg_at_10=ndcg_at_k(ranked, grades, 10),
                    recall_at_50=_num(recall_at_k(ranked, grades, 50)),
                    mrr=mrr(ranked, grades),
                    p_at_5=p_at_k(ranked, grades, 5),
                    unjudged_at_10=unjudged_rate_at_k(ranked, grades, 10),
                    judged=len(grades),
                )
            )
    aggregate: dict[str, Interval] = {}
    for name in METRICS:
        values = [
            float("nan") if getattr(r, name) is None else float(getattr(r, name)) for r in results
        ]
        mean, lo, hi = bootstrap_ci(values)
        aggregate[name] = Interval(mean=_num(mean), lo=_num(lo), hi=_num(hi))
    unj = [r.unjudged_at_10 for r in results]
    return RankingReport(
        pipeline_version=pv,
        vector_mode=mode,
        variant=variant,
        as_of=as_of.isoformat(),
        personas=len(results),
        skipped_personas=skipped,
        aggregate=aggregate,
        unjudged_at_10_mean=sum(unj) / len(unj) if unj else None,
        per_persona=results,
    )


@dataclass(frozen=True)
class GateResult:
    status: Literal["pass", "fail", "no_baseline"]
    baseline: float | None = None
    current: float | None = None
    drop: float | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "gate": {"pass": "passed", "fail": "failed", "no_baseline": "no_baseline"}[self.status],
            "baseline": self.baseline,
            "current": self.current,
            "drop": self.drop,
        }


def check_gate(report: RankingReport, baseline_path: Path | None, max_drop: float) -> GateResult:
    """Fail when nDCG@10 fell by more than ``max_drop`` below the committed baseline."""
    if baseline_path is None or not baseline_path.is_file():
        return GateResult("no_baseline")
    baseline = float(json.loads(baseline_path.read_text(encoding="utf-8"))["ndcg_at_10"])
    current = report.aggregate["ndcg_at_10"].mean
    if current is None:
        return GateResult("fail", baseline, None, None)
    drop = round(baseline - current, 12)
    return GateResult("fail" if drop > max_drop else "pass", baseline, current, drop)


def report_markdown(report: RankingReport) -> str:
    def f(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.3f}"

    lines = [
        f"Ranking eval: variant `{report.variant}`, {report.personas} personas, "
        f"vector_mode `{report.vector_mode}`, pipeline `{report.pipeline_version}`",
        "",
        "| metric | mean | 95% CI |",
        "|---|---|---|",
    ]
    for name in METRICS:
        iv = report.aggregate[name]
        lines.append(f"| {name} | {f(iv.mean)} | [{f(iv.lo)}, {f(iv.hi)}] |")
    lines.append("")
    lines.append(f"unjudged@10 mean: {f(report.unjudged_at_10_mean)}")
    if report.skipped_personas:
        lines.append(f"skipped (no judgments): {', '.join(report.skipped_personas)}")
    return "\n".join(lines)
