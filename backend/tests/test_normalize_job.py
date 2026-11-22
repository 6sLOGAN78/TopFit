from decimal import Decimal
from typing import Any, get_args

from ranker.ingest.hashing import content_hash, dedupe_key
from ranker.ingest.normalize.job import (
    NORMALIZER_VERSION,
    NormalizedJob,
    ScopeSkip,
    normalize_job,
)
from ranker.ingest.sources.base import RawJob
from ranker.llm import schemas


def _raw(**kw: Any) -> RawJob:
    base: dict[str, Any] = {
        "source": "test",
        "source_job_id": "1",
        "url": "https://example.com/j/1",
        "title": "Senior Backend Engineer (Go, Node.js)",
        "company_name": "Acme Technologies Pvt Ltd",
        "description_raw": "We build services in Go and Node.js. Postgres and Docker.",
        "location_text": "Bangalore",
    }
    base.update(kw)
    return RawJob.model_validate(base)


def _job(raw: RawJob) -> NormalizedJob:
    out = normalize_job(raw)
    assert isinstance(out, NormalizedJob), out
    return out


def test_core_fields() -> None:
    raw = _raw(salary_text="₹30-40 LPA")
    j = _job(raw)
    assert j.city == "Bengaluru"
    assert j.remote_type == "onsite"
    assert j.remote_eligibility == "india"
    assert j.seniority == "senior"
    assert j.salary_min_inr_annual == Decimal(3_000_000)
    assert {"go", "nodejs"} <= set(j.skills)
    assert j.company_normalized == "acme"
    assert j.normalizer_version == NORMALIZER_VERSION
    assert j.dedupe_key == _job(raw).dedupe_key


def test_hybrid_and_dealbreakers() -> None:
    j = _job(
        _raw(
            location_text="Bangalore (Hybrid)",
            description_raw=(
                "Hybrid - 3 days a week in office. We are a fintech startup. "
                "Visa sponsorship available. Relocation support provided."
            ),
        )
    )
    assert j.remote_type == "hybrid"
    assert j.onsite_days_per_week == 3
    assert j.industry == "fintech"
    assert j.visa_sponsorship == "offered"
    assert j.relocation == "offered"


def test_unknowns() -> None:
    j = _job(_raw(company_name="Zzyzx Qwerty", description_raw="Write code with Go."))
    assert j.industry == "unknown"
    assert j.company_stage == "unknown"
    assert j.company_size == "unknown"
    assert j.visa_sponsorship == "unknown"
    assert j.relocation == "unknown"
    assert j.onsite_days_per_week is None


def test_hints() -> None:
    assert _job(_raw(company_size_hint="51-200 employees")).company_size == "51-200"
    j = _job(_raw(skills_hint=["Django", "REST API"]))
    assert "django" in j.skills


def test_scope_skips() -> None:
    out = normalize_job(_raw(title="Sales Manager"))
    assert isinstance(out, ScopeSkip) and out.reason == "excluded"
    out = normalize_job(_raw(location_text="London, UK"))
    assert isinstance(out, ScopeSkip) and out.reason == "geo_out_of_scope"


def test_literals_match_schemas() -> None:
    fields = NormalizedJob.model_fields
    for name, lit in (
        ("remote_eligibility", schemas.RemoteEligibility),
        ("seniority", schemas.Seniority),
        ("employment_type", schemas.EmploymentType),
    ):
        assert get_args(fields[name].annotation) == get_args(lit)


def test_content_hash_source_level() -> None:
    a = _raw()
    assert content_hash(a) == content_hash(_raw())
    for change in (
        {"title": "Other"},
        {"company_name": "Other"},
        {"description_raw": "different"},
        {"location_text": "Pune"},
        {"salary_text": "10 LPA"},
        {"salary_min": 1.0},
        {"salary_max": 2.0},
    ):
        assert content_hash(_raw(**change)) != content_hash(a)
    # fields that are not source-level content do not move the hash
    assert content_hash(_raw(category_hint="x", skills_hint=["go"])) == content_hash(a)


def test_dedupe_key_keeps_seniority() -> None:
    assert dedupe_key("acme", "Senior Backend Engineer II", "senior", "Pune") != dedupe_key(
        "acme", "Backend Engineer", "mid", "Pune"
    )
    assert dedupe_key("acme", "Backend  Engineer!", "mid", "Pune") == dedupe_key(
        "acme", "backend engineer", "mid", "pune"
    )
