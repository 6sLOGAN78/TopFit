"""D-20 ingestion eval: score ``normalize_job`` against hand-labelled real postings.

The scoring path is pure: no network, no LLM, no database. ``capture_samples`` is the only
function that talks to a source, and only through the real adapters (so D-15 politeness and
per-source quotas apply); it is an operator command, never run by tests against the live web.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import load_config
from ranker.ingest.normalize.dealbreakers import Industry
from ranker.ingest.normalize.job import NORMALIZER_VERSION, NormalizedJob, normalize_job
from ranker.ingest.normalize.location import RemoteType
from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    RawJob,
    SourcesConfig,
    get_adapter_factory,
)
from ranker.llm.schemas import EmploymentType, RemoteEligibility, Seniority
from ranker.settings import Settings

MAX_DESCRIPTION_CHARS = 3000
SALARY_TOLERANCE = 0.05

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


class EvalSetError(ValueError):
    """A fixture line is malformed; the message names the 1-based line number."""


class GoldLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = ambiguous from the posting text (e.g. foreign location, work mode not stated):
    # the record is excluded from every metric.
    in_scope: bool | None
    remote_type: RemoteType = "unknown"
    onsite_days_per_week: int | None = None
    remote_eligibility: RemoteEligibility = "unknown"
    seniority: Seniority = "unknown"
    employment_type: EmploymentType = "unknown"
    salary_min_inr_annual: float | None = None
    salary_max_inr_annual: float | None = None
    city: str | None = None
    country_code: str | None = None
    industry: Industry = "unknown"


class EvalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: RawJob
    gold: GoldLabels
    labelled_by: str
    reviewed: bool
    notes: str | None = None


def load_eval_set(path: Path) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                records.append(EvalRecord.model_validate_json(line))
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first["loc"])
                raise EvalSetError(f"line {n}: {where}: {first['msg']}") from exc
    return records


class FieldMetrics(BaseModel):
    accuracy: float
    coverage: float
    precision: float | None
    n: int


class EvalReport(BaseModel):
    n: int
    normalizer_version: str
    fields: dict[str, FieldMetrics]
    per_source: dict[str, dict[str, FieldMetrics]]
    n_by_source: dict[str, int]


SCORED_FIELDS = (
    "in_scope",
    "remote_type",
    "onsite_days_per_week",
    "remote_eligibility",
    "seniority",
    "employment_type",
    "salary",
    "city",
    "country_code",
    "industry",
)


def _salary_close(gold: float | None, pred: Decimal | None) -> bool:
    if gold is None or pred is None:
        return gold is None and pred is None
    return abs(float(pred) - gold) <= SALARY_TOLERANCE * abs(gold)


def _salary_ok(rec: EvalRecord, job: NormalizedJob) -> bool:
    return _salary_close(rec.gold.salary_min_inr_annual, job.salary_min_inr_annual) and (
        _salary_close(rec.gold.salary_max_inr_annual, job.salary_max_inr_annual)
    )


def _salary_predicted(job: NormalizedJob) -> bool:
    return job.salary_min_inr_annual is not None or job.salary_max_inr_annual is not None


class _Tally:
    def __init__(self) -> None:
        self.n = 0
        self.correct = 0
        self.predicted = 0
        self.predicted_correct = 0

    def add(self, *, correct: bool, predicted: bool) -> None:
        self.n += 1
        self.correct += int(correct)
        if predicted:
            self.predicted += 1
            self.predicted_correct += int(correct)

    def metrics(self) -> FieldMetrics:
        if self.n == 0:
            return FieldMetrics(accuracy=0.0, coverage=0.0, precision=None, n=0)
        return FieldMetrics(
            accuracy=round(self.correct / self.n, 4),
            coverage=round(self.predicted / self.n, 4),
            precision=round(self.predicted_correct / self.predicted, 4) if self.predicted else None,
            n=self.n,
        )


_Tallies = dict[str, _Tally]


def _tally(tallies: _Tallies, field: str, *, correct: bool, predicted: bool) -> None:
    tallies.setdefault(field, _Tally()).add(correct=correct, predicted=predicted)


def _score_one(rec: EvalRecord, tallies: _Tallies) -> None:
    if rec.gold.in_scope is None:
        return
    pred = normalize_job(rec.raw)
    predicted_in_scope = isinstance(pred, NormalizedJob)
    _tally(tallies, "in_scope", correct=predicted_in_scope == rec.gold.in_scope, predicted=True)
    if not rec.gold.in_scope or not isinstance(pred, NormalizedJob):
        return
    gold = rec.gold
    categorical: list[tuple[str, object, object, object]] = [
        ("remote_type", gold.remote_type, pred.remote_type, "unknown"),
        ("onsite_days_per_week", gold.onsite_days_per_week, pred.onsite_days_per_week, None),
        ("remote_eligibility", gold.remote_eligibility, pred.remote_eligibility, "unknown"),
        ("seniority", gold.seniority, pred.seniority, "unknown"),
        ("employment_type", gold.employment_type, pred.employment_type, "unknown"),
        ("city", gold.city, pred.city, None),
        ("country_code", gold.country_code, pred.country_code, None),
        ("industry", gold.industry, pred.industry, "unknown"),
    ]
    for field, gold_value, pred_value, unknown in categorical:
        _tally(
            tallies,
            field,
            correct=gold_value == pred_value,
            predicted=pred_value != unknown,
        )
    _tally(tallies, "salary", correct=_salary_ok(rec, pred), predicted=_salary_predicted(pred))


def _metrics(tallies: _Tallies) -> dict[str, FieldMetrics]:
    return {f: tallies[f].metrics() for f in SCORED_FIELDS if f in tallies}


def score_normalisation(records: list[EvalRecord]) -> EvalReport:
    overall: _Tallies = {}
    by_source: dict[str, _Tallies] = {}
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.raw.source] = counts.get(rec.raw.source, 0) + 1
        _score_one(rec, overall)
        _score_one(rec, by_source.setdefault(rec.raw.source, {}))
    return EvalReport(
        n=len(records),
        normalizer_version=NORMALIZER_VERSION,
        fields=_metrics(overall),
        per_source={s: _metrics(t) for s, t in sorted(by_source.items())},
        n_by_source=dict(sorted(counts.items())),
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _table(rows: dict[str, FieldMetrics]) -> list[str]:
    lines = ["| field | n | accuracy | coverage | precision |", "|---|---|---|---|---|"]
    for field, m in rows.items():
        lines.append(
            f"| {field} | {m.n} | {_fmt(m.accuracy)} | {_fmt(m.coverage)} | {_fmt(m.precision)} |"
        )
    return lines


def report_markdown(report: EvalReport) -> str:
    out = [
        f"normalizer_version: {report.normalizer_version}, records: {report.n}",
        "",
        "### Per field",
        "",
        *_table(report.fields),
        "",
        "### Per source (accuracy / n)",
        "",
    ]
    fields = [f for f in SCORED_FIELDS if f in report.fields]
    out.append("| source | records | " + " | ".join(fields) + " |")
    out.append("|---|---|" + "---|" * len(fields))
    for source, metrics in report.per_source.items():
        cells = [
            f"{metrics[f].accuracy:.2f} ({metrics[f].n})" if f in metrics else "n/a" for f in fields
        ]
        out.append(f"| {source} | {report.n_by_source[source]} | " + " | ".join(cells) + " |")
    return "\n".join(out)


# --- capture -------------------------------------------------------------------------------


def scrub(text: str) -> str:
    """Remove emails and phone-like digit runs (T-02-67)."""
    text = _EMAIL.sub("[email]", text)

    def phone(m: re.Match[str]) -> str:
        digits = sum(c.isdigit() for c in m.group(0))
        return "[phone]" if 10 <= digits <= 15 else m.group(0)

    return _PHONE.sub(phone, text)


def scrub_raw(raw: RawJob) -> RawJob:
    desc = scrub(raw.description_raw)[:MAX_DESCRIPTION_CHARS]
    return raw.model_copy(
        update={
            "description_raw": desc,
            "location_text": scrub(raw.location_text) if raw.location_text else None,
            "salary_text": scrub(raw.salary_text) if raw.salary_text else None,
        }
    )


# Keep one capture polite and small (D-15): narrow the scraper and capped feeds.
_CAPTURE_OVERRIDES: dict[str, Callable[[BaseSourceConfig], dict[str, Any]]] = {
    "internshala": lambda cfg: {
        "job_categories": list(cfg.model_dump().get("job_categories", []))[:2],
        "internship_categories": list(cfg.model_dump().get("internship_categories", []))[:2],
        "max_pages_per_category": 2,
    },
    "arbeitnow": lambda cfg: {"max_pages": 3},
}


def _capture_config(source: str) -> BaseSourceConfig:
    section = load_config("sources.yaml", SourcesConfig).root.get(source) or BaseSourceConfig()
    overrides = _CAPTURE_OVERRIDES.get(source)
    if overrides is None:
        return section
    return BaseSourceConfig.model_validate({**section.model_dump(), **overrides(section)})


def _spread(pool: list[RawJob], n: int) -> list[RawJob]:
    """Deterministic spread: round-robin across scopes, low-discrepancy order within each."""
    groups: dict[str, list[RawJob]] = {}
    for raw in pool:
        groups.setdefault(raw.source_scope, []).append(raw)
    ordered = [
        [g[i] for i in sorted(range(len(g)), key=lambda i: (i * 0.6180339887) % 1)]
        for _, g in sorted(groups.items())
    ]
    chosen: list[RawJob] = []
    depth = 0
    while len(chosen) < n and any(depth < len(g) for g in ordered):
        for g in ordered:
            if depth < len(g) and len(chosen) < n:
                chosen.append(g[depth])
        depth += 1
    return chosen


def _seen_ids(path: Path) -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)["raw"]
                seen.add((r["source"], r["source_job_id"]))
    return seen


def _append_unlabelled(path: Path, records: list[RawJob]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for raw in records:
            record = {
                "raw": raw.model_dump(mode="json"),
                "gold": None,
                "labelled_by": "",
                "reviewed": False,
                "notes": None,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


async def capture_samples(
    engine: AsyncEngine, settings: Settings, source: str, n: int, out_path: Path
) -> int:
    """Fetch ``source`` once through its adapter and append ``n`` scrubbed, unlabelled records."""
    factory = get_adapter_factory(source)
    adapter = factory(
        AdapterContext(
            settings=settings,
            engine=engine,
            config=_capture_config(source),
            mode="incremental",
            now=datetime.now(UTC),
        )
    )
    pool = [raw async for raw in adapter.fetch()]
    seen = _seen_ids(out_path)
    fresh = [r for r in pool if (r.source, r.source_job_id) not in seen]
    chosen = [scrub_raw(r) for r in _spread(fresh, n)]
    _append_unlabelled(out_path, chosen)
    return len(chosen)
