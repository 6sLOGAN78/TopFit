# ADR 0002: Remote eligibility, tech scope and geography scope

Status: accepted (Phase 2, plan 02-04). Related: D-05, D-06, D-07, D-08, NORM-02, NORM-03.

## Context

Jobs arrive from sources with very different location and eligibility information. Ranking
(Phase 3/4) and the UI need to tell an India-eligible remote job from a US-only one, and the
index must not fill up with off-scope roles (D-02).

## Decisions

### Scope: skip, do not store

Jobs outside the tech scope (D-05) or outside the geography scope (D-06) are **skipped at
ingest and not stored**. They are counted in `ingest_runs.skipped`, with a sample of titles in
`ingest_runs.notes` for audit. Reclassifying needs a refetch, which is acceptable for daily feeds.

- In scope: software, backend, frontend, full stack, mobile, DevOps/SRE/platform, data
  engineering, data science, ML/AI, QA/SDET, security, and engineering managers.
- **Data analyst is in scope** (data family). Analytics engineer too.
- Out of scope: product management, design, tech support (including "customer support
  engineer"), IT admin / sysadmin / helpdesk, sales (including "sales engineer"), recruiting,
  marketing, and other non-engineering roles.
- The exclude list always wins over the include list and over a tech category.
- A title that is ambiguous ("Associate") is included only when the source category is a tech
  category (`scope.yaml`).
- Geography: remote jobs from anywhere stay in; on-site or hybrid jobs in India stay in;
  on-site or hybrid jobs in another country are dropped (`geo_out_of_scope`); on-site with an
  unknown country is kept (`geo_unknown`) so Adzuna India rows are never lost.

### Eligibility value set and precedence

`remote_eligibility` is exactly one of `worldwide`, `india`, `apac`, `us_only`, `eu_only`,
`other_region`, `unknown`, plus `remote_regions` (sorted tokens `worldwide`, `india`, `apac`,
`us`, `eu`, `other`). Precedence:

1. `worldwide` token gives `worldwide`
2. else `india` token gives `india`
3. else `apac` token gives `apac`
4. else only `us` gives `us_only`
5. else only `eu` gives `eu_only`
6. else any other token gives `other_region`
7. else `unknown`

Choices: Singapore counts as APAC, and an APAC list that does not name India still counts as
`apac` (ranking may weaken this later). Phrase matching is whole-word only. On-site and
hybrid jobs take the job's own country (India gives `india`, anything else `other_region`)
before the description is consulted.

### D-06: US/EU-only remote jobs are stored

Remote jobs restricted to the US or EU are stored with `us_only` / `eu_only`. They are not
dropped at ingest; ranking filters them out for India users later.

### D-07: contract for `unknown` (Phase 3/4 ranking and UI)

`remote_eligibility = unknown` is **shown to India users**, ranked **below** jobs confirmed as
`india`, `apac` or `worldwide`, and the UI shows a **"region not stated"** badge. Phase 2 only
stores the value correctly; ranking and UI consume this contract.

### D-08: rules first, LLM fallback only for unknown

Rules (source fields, then description phrases over the first 3,000 and last 1,000
characters) run first. The LLM (Haiku via the gateway, typed output) is called only when the
rules return `unknown`, and the answer is cached by `content_hash` (plan 02-12). With no key or
budget the value stays `unknown` and an enrichment task is queued (D-11).

### D-19: work mode

`remote_type` (`onsite`, `hybrid`, `remote`, `unknown`) plus on-site days per week parsed from
the text (`NULL` means not stated, `0` for remote, minimum of a range).

## Consequences

- Rules live in `backend/config/{cities,regions,scope}.yaml` and are table-tested.
- Regexes are literal alternations with bounded input, so ReDoS is not a concern (T-02-15).
