"""D-17 query-parse measurement: rules-only vs rules+Haiku vs rules+Jev.

Every variant goes through the production ``parse_query``. Model variants set
``force_fallback=True`` so the residue gate and the cache are skipped and every query measures
the model itself (production would call it for only a fraction of queries). Scoring is pure;
the only paid path is the gateway, which applies its own budget lock. Live runs are an
operator action (``ranker eval parse --yes``); tests use the fake provider.
"""

import math
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.llm.client import LLMGateway
from ranker.llm.schemas import ROLE_FAMILY_ANSWERS, SALARY_BUCKETS_LPA, WORK_MODE_ANSWERS
from ranker.search.models import ParseModel
from ranker.search.parse import QueryParse, parse_query
from ranker.settings import Settings

ParseVariant = Literal["rules", "haiku", "jev"]
VARIANTS: tuple[ParseVariant, ...] = ("rules", "haiku", "jev")
FILTER_FIELDS = ("city", "min_salary", "work_mode", "seniority")
ALL_FIELDS = (*FILTER_FIELDS, "role_family")
PURPOSE = "query_parse"

_SENIORITY_GOLD = ("intern", "junior", "mid", "senior", "lead", "unspecified")

_COST_SQL = text(
    "SELECT model, COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS n FROM llm_calls "
    "WHERE purpose = :p AND id > :after GROUP BY model"
)
_LAST_ID_SQL = text("SELECT COALESCE(MAX(id), 0) FROM llm_calls")


class ParseEvalError(ValueError):
    """A gold line is malformed; the message names the 1-based line number."""


class ParseGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str  # canonical city name or "none"
    min_salary_lpa: int | None  # a bucket from SALARY_BUCKETS_LPA, or None
    work_mode: str
    seniority: str
    role_family: str


class ParseCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    gold: ParseGold
    labelled_by: str
    reviewed: bool
    notes: str | None = None


def load_parse_cases(path: Path) -> list[ParseCase]:
    cases: list[ParseCase] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                case = ParseCase.model_validate_json(line)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first["loc"])
                raise ParseEvalError(f"line {n}: {where}: {first['msg']}") from exc
            g = case.gold
            problems = [
                (g.work_mode not in WORK_MODE_ANSWERS, "gold.work_mode"),
                (g.seniority not in _SENIORITY_GOLD, "gold.seniority"),
                (g.role_family not in ROLE_FAMILY_ANSWERS, "gold.role_family"),
                (
                    g.min_salary_lpa is not None and g.min_salary_lpa not in SALARY_BUCKETS_LPA,
                    "gold.min_salary_lpa",
                ),
            ]
            for bad, name in problems:
                if bad:
                    raise ParseEvalError(f"line {n}: {name}: value not in the closed set")
            cases.append(case)
    return cases


class FieldMiss(BaseModel):
    id: str
    field: str
    gold: str
    predicted: str


class VariantResult(BaseModel):
    variant: str
    status: Literal["ok", "not run"]
    reason: str | None = None
    n: int = 0
    # Per-field accuracy in [0, 1]; role_family is None for rules (the rules read no family).
    accuracy: dict[str, float | None] = {}
    exact_match: float | None = None  # all four filter fields right
    model_calls_degraded: int = 0  # queries where the model call failed and rules stood alone
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    cost_usd_total: float | None = None
    cost_per_1k_usd: float | None = None
    models_used: dict[str, int] = {}  # llm_calls.model (the routed model for Jev) -> calls
    misses: list[FieldMiss] = []


class ParseEvalReport(BaseModel):
    n_queries: int
    results: list[VariantResult]


def _predicted(parse: QueryParse) -> dict[str, str]:
    f = parse.filters
    if len(f.cities) == 1:
        city = f.cities[0]
    else:
        city = "none" if not f.cities else "multi"
    if f.cities and f.remote_ok:
        mode = "remote_or_city"
    elif len(f.work_modes) == 1:
        mode = f.work_modes[0]
    elif f.work_modes:
        mode = "multi"
    else:
        mode = "unspecified"
    return {
        "city": city,
        "min_salary": "none" if f.min_salary_lpa is None else str(f.min_salary_lpa),
        "work_mode": mode,
        "seniority": f.seniority[0] if len(f.seniority) == 1 else "unspecified",
        "role_family": parse.role_family or "unspecified",
    }


def _gold(case: ParseCase) -> dict[str, str]:
    g = case.gold
    return {
        "city": g.city,
        "min_salary": "none" if g.min_salary_lpa is None else str(g.min_salary_lpa),
        "work_mode": g.work_mode,
        "seniority": g.seniority,
        "role_family": g.role_family,
    }


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


async def _run_variant(
    variant: ParseVariant,
    cases: list[ParseCase],
    engine: AsyncEngine,
    gateway: LLMGateway,
    settings: Settings,
) -> VariantResult:
    model_variant = variant != "rules"
    if model_variant and not gateway.available(variant):
        return VariantResult(
            variant=variant, status="not run", reason=f"{variant} provider not configured"
        )
    async with engine.connect() as conn:
        after = int((await conn.execute(_LAST_ID_SQL)).scalar_one())
    correct = dict.fromkeys(ALL_FIELDS, 0)
    exact = 0
    degraded = 0
    latencies: list[float] = []
    misses: list[FieldMiss] = []
    parse_model: ParseModel = "jev" if variant == "jev" else "haiku"
    for case in cases:
        started = time.perf_counter()
        parse = await parse_query(
            case.query,
            None,
            True,
            engine=engine,
            gateway=gateway if model_variant else None,
            parse_model=parse_model,
            settings=settings,
            force_fallback=model_variant,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        degraded += parse.degraded
        pred, gold = _predicted(parse), _gold(case)
        all_filters = True
        for field in ALL_FIELDS:
            if field == "role_family" and not model_variant:
                continue
            ok = pred[field] == gold[field]
            correct[field] += ok
            if field in FILTER_FIELDS:
                all_filters = all_filters and ok
            if not ok:
                misses.append(
                    FieldMiss(id=case.id, field=field, gold=gold[field], predicted=pred[field])
                )
        exact += all_filters
    n = len(cases)
    cost = 0.0
    models: dict[str, int] = {}
    if model_variant:
        async with engine.connect() as conn:
            rows = (await conn.execute(_COST_SQL, {"p": PURPOSE, "after": after})).all()
        cost = float(sum(r.cost for r in rows))
        models = {str(r.model): int(r.n) for r in rows}
    accuracy: dict[str, float | None] = {f: (correct[f] / n if n else 0.0) for f in FILTER_FIELDS}
    accuracy["role_family"] = correct["role_family"] / n if model_variant and n else None
    return VariantResult(
        variant=variant,
        status="ok",
        n=n,
        accuracy=accuracy,
        exact_match=exact / n if n else 0.0,
        model_calls_degraded=degraded,
        latency_p50_ms=_percentile(latencies, 50) if latencies else None,
        latency_p95_ms=_percentile(latencies, 95) if latencies else None,
        cost_usd_total=cost,
        cost_per_1k_usd=cost / n * 1000 if n else 0.0,
        models_used=models,
        misses=misses,
    )


async def run_parse_eval(
    engine: AsyncEngine,
    gateway: LLMGateway,
    settings: Settings,
    cases: list[ParseCase],
    variants: list[ParseVariant],
) -> ParseEvalReport:
    results = [await _run_variant(v, cases, engine, gateway, settings) for v in variants]
    return ParseEvalReport(n_queries=len(cases), results=results)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def report_markdown(report: ParseEvalReport) -> str:
    lines = [
        f"Query-parse eval over {report.n_queries} queries",
        "",
        "| variant | city | salary | work mode | seniority | role family | exact (4 filters) "
        "| p50 ms | p95 ms | $/1k queries | degraded |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.results:
        if r.status != "ok":
            lines.append(f"| {r.variant} | not run: {r.reason} |||||||||")
            continue
        a = r.accuracy
        lines.append(
            f"| {r.variant} | {_pct(a['city'])} | {_pct(a['min_salary'])} | "
            f"{_pct(a['work_mode'])} | {_pct(a['seniority'])} | {_pct(a['role_family'])} | "
            f"{_pct(r.exact_match)} | {r.latency_p50_ms:.0f} | {r.latency_p95_ms:.0f} | "
            f"{r.cost_per_1k_usd:.4f} | {r.model_calls_degraded} |"
            if r.latency_p50_ms is not None
            and r.latency_p95_ms is not None
            and r.cost_per_1k_usd is not None
            else f"| {r.variant} | no queries |||||||||"
        )
    for r in report.results:
        if r.models_used:
            lines.append(
                f"\n{r.variant} models: " + ", ".join(f"{k} x{v}" for k, v in r.models_used.items())
            )
    return "\n".join(lines)
