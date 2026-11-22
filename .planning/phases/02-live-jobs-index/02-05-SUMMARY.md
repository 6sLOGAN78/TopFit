---
phase: 02-live-jobs-index
plan: 05
subsystem: ingest
tags: [normalisation, salary, fx, seniority, company, dealbreakers, hypothesis]

requires:
  - phase: 02-live-jobs-index
    provides: config_loader.load_config (02-02), Adzuna salary period decision (02-01)
provides:
  - parse_salary_text / salary_from_numbers / to_inr_annual with dated fixed FX
  - infer_seniority / infer_employment_type (rules, explicit unknown)
  - normalize_company_name / is_placeholder_company
  - D-19 dealbreaker rules (visa, relocation, industry, stage, size) and curated industries map
affects: [02-07 migration CHECKs, 02-08 pipeline wiring, 02-12 LLM fallback, Phase 4 dealbreakers]

tech-stack:
  added: []
  patterns: [pure normalisers with YAML config via load_config, linear length-capped regexes, table-driven + hypothesis tests]

key-files:
  created:
    - backend/config/fx.yaml
    - backend/config/industries.yaml
    - backend/ranker/ingest/normalize/salary.py
    - backend/ranker/ingest/normalize/seniority.py
    - backend/ranker/ingest/normalize/company.py
    - backend/ranker/ingest/normalize/dealbreakers.py
    - backend/tests/test_normalize_salary.py
    - backend/tests/test_normalize_seniority.py
    - backend/tests/test_normalize_company.py
    - backend/tests/test_normalize_dealbreakers.py
    - docs/decisions/0003-salary-normalisation.md
  modified: []

key-decisions:
  - "Fixed dated FX in fx.yaml; salary_fx_as_of stored per job (D-12)"
  - "Generic industry hints (e.g. Software Development) map to other and never override the company map or self-description"
  - "Source hint > curated map > unknown for stage and size"

requirements-completed: [NORM-04, NORM-05, NORM-07]

duration: ~35min
completed: 2026-11-22
---

# Phase 2 Plan 05: Salary, seniority, company and dealbreaker normalisers Summary

**Pure, tested rule normalisers: salary text to INR-annual with dated FX and estimate flag, seniority/employment type with explicit unknowns, stable company keys, and D-19 dealbreaker fields defaulting to `unknown`.**

## Accomplishments
- Salary parser handles Indian units/grouping, bare-k rule, decimal comma, hourly/monthly, CTC-over-fixed, sanity bounds (out_of_bounds), unknown currency, poison-pill guard.
- Seniority/employment rules from title, years text and source hints.
- Company normaliser (idempotent, property-tested) with placeholder and staffing-agency flags.
- Dealbreakers: visa/relocation phrase rules (negation wins), industry/stage/size with `config/industries.yaml` (86 curated entries covering the spike seed boards).
- ADR 0003 records the FX, estimate, bare-k, CTC and D-19 contracts.

## Task Commits
1. Task 1 salary parser: `d031a63`
2. Task 2 seniority + visa/relocation: `c568e18`
3. Fix (coordinator-requested) decimal poison-pill guard: `7ecbee0`
4. Task 3 company, industry/stage/size, ADR: `bc5452c`

## Verification
`uv run pytest -q`: 291 passed. `ruff check .` clean; `pyright` 0 errors. Test counts: salary 52 table cases + properties, seniority/employment 51 cases, company 42, dealbreakers 70+.

## D-19 Literal value sets (verbatim, for plan 02-07 CHECKs)
- Industry: fintech, edtech, healthtech, ecommerce, saas, devtools, ai_ml, gaming, media, logistics, mobility, crypto, hrtech, adtech, cybersecurity, consumer_internet, it_services, other, unknown
- CompanyStage: early, growth, late, public, unknown
- CompanySize: 1-10, 11-50, 51-200, 201-1000, 1001-5000, 5001+, unknown
- VisaSponsorship: offered, not_offered, unknown
- Relocation: offered, not_offered, unknown
- Seniority: intern, junior, mid, senior, lead, unknown; EmploymentType: full_time, part_time, contract, internship, unknown

## Deviations from Plan

**1. [Rule 2 - Security, coordinator request] Decimal poison-pill guard**
- Issue: `_to_decimal` accepted any finite Decimal, so "1e999999" could overflow in conversion and crash ingest.
- Fix: `_to_decimal` returns None for non-finite or abs > 1e12; `to_inr_annual` and the text number parser catch `DecimalException`. Tests added for "1e999999", "-1e400", "NaN", "Infinity", a 400-digit integer, "1e13" and a forced-overflow conversion.
- Files: salary.py, test_normalize_salary.py. Commit: `7ecbee0`.

**2. [Rule 3 - Blocking] FX rates not fetched from RBI**
- No network access during execution, so `fx.yaml` holds rounded approximate rates dated 2026-11-22 with a comment saying so. **Refresh from the RBI reference rate page before launch.**

**3. Minor:** `# ruff: noqa: RUF001` added to files containing intentional en/em-dash and fullwidth characters. Extra helpers `resolve_company_stage` / `resolve_company_size` added to implement "source hint > map > unknown". Generic "other" hint precedence rule added (documented in ADR).

## Known Stubs
None. `industries.yaml` stage/size values are hand-curated from general knowledge of the companies (not fetched) and omitted where uncertain; review before launch.

## Threat Flags
None new. T-02-18/19/20 mitigated (linear regexes with length caps and time-budget tests; sanity bounds; is_placeholder).

## Self-Check: PASSED
All listed files exist and commits d031a63, c568e18, 7ecbee0, bc5452c are present.
