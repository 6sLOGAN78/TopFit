# ADR 0003: Salary, FX and dealbreaker normalisation

Status: accepted (Phase 2, plan 02-05). Date: 2026-11-22.

## Context

Jobs arrive with salary as free text (`₹12–18 LPA`, `$35,3k- $52k`, `$14/hour`) or as numbers (Adzuna). Search filters and ranking need one comparable figure. Dealbreaker fields (D-19) need honest `unknown`s instead of guesses.

## Salary (NORM-04)

- **D-12, fixed FX.** Rates live in `backend/config/fx.yaml` with an `as_of` date and are updated by hand. Every converted job stores `salary_fx_as_of`, so the UI can say "converted at ₹X/$ as of <date>". INR needs no rate and stores `salary_fx_as_of = NULL`. The rates committed in plan 02-05 are rounded approximations (no network was available when they were written); refresh them from the RBI reference rate page before launch.
- **Period multipliers.** year x1, month x12, hour x2080.
- **Units.** `lpa`, `lakh/lakhs/lac/l` = 1e5, `cr/crore` = 1e7, `k` = 1e3. Indian digit grouping (`8,00,000`) and a decimal comma before `k` (`35,3k` = 35,300) are handled.
- **Bare-k rule.** With ₹ and no period, a bare `k` or plain number under 200,000 is monthly; with `$/€/£` a bare `k` is annual. A plain non-INR number under 1,000 with no period is hourly.
- **CTC over fixed.** "CTC 15 LPA (fixed 12)" takes the CTC figure; parenthetical text is dropped before parsing.
- **Only the first range** is read; text after `+ <word>` ("+ ESOPs", "+ bonus") is ignored.
- **Sanity bounds.** INR annual outside [50,000, 50,000,000] keeps the raw min/max but nulls the INR fields and sets `out_of_bounds`. Raw source figures above 1e12 or non-finite are treated as unknown and any `DecimalException` during conversion yields unknown (poison-pill guard).
- **Unknown currency** yields no INR figures. No guessing.
- **D-13, estimates.** Adzuna predicted salaries are stored with `salary_is_estimate = true`. The UI shows them as "~₹X LPA (estimate)" with the "Adzuna Jobsworth" label that Adzuna's terms require, and salary filters include them only when the user allows estimates.
- **Adzuna period** is taken from ADR 0001 (`Adzuna salary period: year`).
- **ReDoS.** All regexes are linear, salary text is capped at 300 characters, and tests assert a time budget on adversarial input (T-02-18).

## Seniority and employment type (NORM-05, rules half)

Title rules run first (intern, lead, senior, level numerals I/II/III, junior words), then years of experience in the first 4,000 characters of the description (minimum of a range: under 2 junior, 2-4 mid, 5-9 senior, 10+ lead). Undecided stays `unknown` for the LLM fallback in plan 02-12. Employment type uses source hints (Adzuna `contract_type=contract` wins over `contract_time`), then the title.

## Company names (NORM-07)

NFKC + casefold, punctuation to spaces, hard suffixes (pvt, ltd, llp, inc, india, ...) dropped from the end, soft suffixes (technologies, labs, solutions) only when 3+ characters remain. Placeholders and staffing agencies set `is_placeholder` (T-02-20). Normalisation is idempotent.

## Dealbreaker fields (D-19)

| Field | Value set | Sources |
|-------|-----------|---------|
| visa_sponsorship | offered, not_offered, unknown | description phrases (first 6,000 chars; negations win) |
| relocation | offered, not_offered, unknown | description phrases (first 6,000 chars; negations win) |
| industry | fintech, edtech, healthtech, ecommerce, saas, devtools, ai_ml, gaming, media, logistics, mobility, crypto, hrtech, adtech, cybersecurity, consumer_internet, it_services, other, unknown | source hint table, then curated company map (`config/industries.yaml`), then explicit self-description ("<we are\|is> a <industry> company/startup/platform" in the first 1,500 chars) |
| company_stage | early, growth, late, public, unknown | source hint, then curated map |
| company_size | 1-10, 11-50, 51-200, 201-1000, 1001-5000, 5001+, unknown | source hint (open ranges map to the bucket holding the lower bound), then curated map |
| onsite_days_per_week | integer or NULL | plan 02-04 |

`unknown` (or NULL for `onsite_days_per_week`) means "not stated". A generic hint such as "Software Development" maps to `other` and never overrides a more specific company-map or self-description result. `industries.yaml` is hand-written from the companies' own public descriptions, not from any third-party dataset, and stage/size are omitted where not well known.

**Contract for later phases:** Phase 4 may set a job aside with a stated reason only on a known value, never on `unknown`. There is no LLM fallback for these fields in this phase.
