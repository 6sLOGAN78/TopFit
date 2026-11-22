"""Compose every normaliser into one pure ``normalize_job(raw)`` (NORM-01..07, D-19)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from ranker.ingest.hashing import content_hash, dedupe_key
from ranker.ingest.normalize.company import is_placeholder_company, normalize_company_name
from ranker.ingest.normalize.dealbreakers import (
    CompanySize,
    CompanyStage,
    Industry,
    Relocation,
    VisaSponsorship,
    detect_relocation,
    detect_visa_sponsorship,
    infer_industry,
    resolve_company_size,
    resolve_company_stage,
)
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.normalize.location import (
    RemoteType,
    detect_onsite_days,
    normalize_location,
)
from ranker.ingest.normalize.region import detect_remote_eligibility
from ranker.ingest.normalize.salary import (
    ParsedSalary,
    Period,
    parse_salary_text,
    salary_from_numbers,
)
from ranker.ingest.normalize.scope import classify_geo, classify_tech
from ranker.ingest.normalize.seniority import infer_employment_type, infer_seniority
from ranker.ingest.skills import extract_skills, skills_text
from ranker.ingest.sources.base import RawJob
from ranker.llm.schemas import EmploymentType, RemoteEligibility, Seniority

# Bump to force re-normalisation of every stored row on the next run.
NORMALIZER_VERSION = "1"


class ScopeSkip(BaseModel):
    reason: str
    title: str


class NormalizedJob(BaseModel):
    source: str
    source_job_id: str
    source_scope: str
    title: str
    url: str
    category: str | None
    company_name: str
    company_normalized: str
    company_is_placeholder: bool
    description_raw: str
    description_clean: str
    description_truncated: bool
    location_text: str | None
    city: str | None
    cities: list[str]
    country_code: str | None
    remote_type: RemoteType
    remote_eligibility: RemoteEligibility
    remote_regions: list[str]
    employment_type: EmploymentType
    seniority: Seniority
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: Period | None
    salary_min_inr_annual: Decimal | None
    salary_max_inr_annual: Decimal | None
    salary_text: str | None
    salary_is_estimate: bool
    salary_fx_as_of: date | None
    skills: list[str]
    skills_text: str
    posted_at: datetime | None
    content_hash: str
    dedupe_key: str
    normalizer_version: str
    onsite_days_per_week: int | None
    industry: Industry
    company_stage: CompanyStage
    company_size: CompanySize
    visa_sponsorship: VisaSponsorship
    relocation: Relocation


def _period_hint(hint: str | None) -> Period | None:
    if not hint:
        return None
    h = hint.strip().lower()[:20]
    if h in ("year", "yearly", "annual", "annually", "annum", "yr"):
        return "year"
    if h in ("month", "monthly", "mo"):
        return "month"
    if h in ("hour", "hourly", "hr"):
        return "hour"
    return None


def _salary(raw: RawJob) -> ParsedSalary | None:
    if raw.salary_min is not None or raw.salary_max is not None:
        return salary_from_numbers(
            min=raw.salary_min,
            max=raw.salary_max,
            currency=raw.salary_currency,
            period=_period_hint(raw.salary_period_hint) or "year",
            is_estimate=raw.salary_is_estimate,
            source_text=raw.salary_text,
        )
    parsed = parse_salary_text(raw.salary_text, default_currency=raw.salary_currency)
    if parsed is not None and raw.salary_is_estimate:
        parsed = parsed.model_copy(update={"is_estimate": True})
    return parsed


def normalize_job(raw: RawJob) -> NormalizedJob | ScopeSkip:
    tech = classify_tech(raw.title, raw.category_hint)
    if not tech.in_scope:
        return ScopeSkip(reason=tech.reason, title=raw.title)

    description = clean_description(
        raw.description_raw,
        is_html=raw.description_is_html,
        entity_escaped=raw.description_entity_escaped,
    )
    location = normalize_location(
        raw.location_text,
        parts=raw.location_parts,
        workplace_hint=raw.workplace_hint,
        country_hint=raw.country_hint,
        description=description,
    )
    geo = classify_geo(location, location.remote_type)
    if not geo.in_scope:
        return ScopeSkip(reason=geo.reason, title=raw.title)

    region = detect_remote_eligibility(
        region_hint=raw.region_hint,
        description=description,
        country_code=location.country_code,
        remote_type=location.remote_type,
    )
    salary = _salary(raw)
    seniority, _ = infer_seniority(raw.title, description)
    employment, _ = infer_employment_type(
        hint=raw.employment_type_hint, contract_type=raw.contract_type_hint, title=raw.title
    )
    company_normalized = normalize_company_name(raw.company_name, raw.company_canonical_hint)
    skills = extract_skills("\n".join([raw.title, description, ", ".join(raw.skills_hint)]))

    return NormalizedJob(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_scope=raw.source_scope,
        title=raw.title,
        url=str(raw.url),
        category=raw.category_hint,
        company_name=raw.company_name,
        company_normalized=company_normalized,
        company_is_placeholder=is_placeholder_company(raw.company_name),
        description_raw=raw.description_raw,
        description_clean=description,
        description_truncated=raw.description_truncated,
        location_text=raw.location_text,
        city=location.city,
        cities=location.cities,
        country_code=location.country_code,
        remote_type=location.remote_type,
        remote_eligibility=region.remote_eligibility,
        remote_regions=region.remote_regions,
        employment_type=employment,
        seniority=seniority,
        salary_min=salary.min if salary else None,
        salary_max=salary.max if salary else None,
        salary_currency=salary.currency if salary else None,
        salary_period=salary.period if salary else None,
        salary_min_inr_annual=salary.min_inr_annual if salary else None,
        salary_max_inr_annual=salary.max_inr_annual if salary else None,
        salary_text=raw.salary_text,
        salary_is_estimate=salary.is_estimate if salary else raw.salary_is_estimate,
        salary_fx_as_of=salary.fx_as_of if salary else None,
        skills=skills,
        skills_text=skills_text(skills),
        posted_at=raw.posted_at,
        content_hash=content_hash(raw),
        dedupe_key=dedupe_key(
            company_normalized,
            raw.title,
            seniority,
            location.city or location.remote_type,
        ),
        normalizer_version=NORMALIZER_VERSION,
        onsite_days_per_week=detect_onsite_days(description, location.remote_type),
        industry=infer_industry(
            company_normalized=company_normalized,
            description=description,
            hint=raw.industry_hint,
        ),
        company_stage=resolve_company_stage(
            hint=raw.company_stage_hint, company_normalized=company_normalized
        ),
        company_size=resolve_company_size(
            hint=raw.company_size_hint, company_normalized=company_normalized
        ),
        visa_sponsorship=detect_visa_sponsorship(description),
        relocation=detect_relocation(description),
    )
