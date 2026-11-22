# Ingestion normalisation eval (D-20)

Purpose: measure how well `normalize_job` (scope filter, work mode, remote eligibility, seniority,
employment type, salary in INR, city/country, industry) agrees with hand-written labels on real
postings, and stop silent regressions with a CI floor.

Run it (no network, no LLM, no database):

```
cd backend
uv run python -m ranker.cli eval ingest --markdown     # table
uv run python -m ranker.cli eval ingest                # one JSON object
uv run python -m ranker.cli runs report --since-hours 24   # D-20 run counters, needs the DB
```

Measured on 2026-11-22, `normalizer_version` 1, 78 records.

## Sample

Captured once per source through the real adapters (`ranker eval capture <source> --n K`),
unlabelled, then scrubbed (emails and phone-like numbers replaced, `description_raw` cut to 3,000
characters). Records are a deterministic spread across each source's scopes (boards), not a random
sample of in-scope tech jobs.

| source | records | notes |
|---|---|---|
| greenhouse | 20 | 13 seed boards, alphabetical spread: almost all non-engineering or non-India |
| ashby | 20 | 8 seed boards |
| lever | 10 | 5 seed boards |
| arbeitnow | 10 | German listings, first 3 pages |
| remoteok | 7 | |
| remotive | 6 | |
| jobicy | 5 | |
| internshala | 0 | the first request returned a bot-challenge page (`challenge:200`), so the fetcher stopped by design (D-15); nothing was retried or bypassed |
| adzuna | 0 | no API keys (ADR 0001, quota budget 0) |

The plan asked for about 100 records including 20 from Internshala. The result is 78. A second
capture pass to top up was not made, to stay inside the one-capture-per-source rule.

Gold scope split: 10 in scope, 60 out of scope, 8 ambiguous (gold `in_scope: null`, excluded from
every metric, reason in each record's `notes`). Only 10 records are tech roles that should be
ingested, so every per-field number below rests on 9 to 10 postings (one skipped by the
normaliser leaves n = 9). One wrong record moves a field by 0.11 to 0.10. Treat these numbers as a
smoke test, not as precision estimates, and grow the set before quoting them externally.

## Labelling protocol

- Labels were written by Claude from reading each posting's text and source fields, before the
  first scoring run. They were not copied from normaliser output and the normaliser was not run on
  the set before labelling. Every record has `labelled_by: "claude"` and `reviewed: false`.
- Owner-reviewed labels: **0 of 78**. Plan 02-15 asks the owner to review at least 20 and set
  `reviewed: true`; update this count then.
- `in_scope` follows ADR 0002: engineering and data roles in; product, design, support, sales,
  recruiting and marketing out; remote anywhere in; on-site or hybrid in India in; on-site or
  hybrid elsewhere out. A tech role abroad whose work mode the text does not state is ambiguous
  (`null`), not out.
- Seniority rubric: an explicit title level wins (intern, junior, senior; staff, principal, lead,
  manager and director count as `lead`); otherwise years required (under 2 junior, 2 to 5 mid,
  5 or more senior); otherwise `unknown`.
- Remote work mode gives `onsite_days_per_week` 0. `city` is the Indian canonical city or null.
  `remote_eligibility` follows the ADR 0002 precedence (two non-worldwide regions give
  `other_region` unless one of them is `apac` or `india`).
- Salary gold is the stated range converted at the rates in `config/fx.yaml` (USD 88.5, as of
  2026-11-22); scoring tolerance is 5% on both bounds, or both null.
- The normaliser rules were not tuned against this set. Suggested fixes are listed below.

## Results

Accuracy is exact agreement with gold. Coverage is the share of predictions that are not
`unknown`/null. Precision is accuracy among those non-unknown predictions.

| field | n | accuracy | coverage | precision |
|---|---|---|---|---|
| in_scope | 70 | 0.91 | 1.00 | 0.91 |
| remote_type | 9 | 0.89 | 0.89 | 1.00 |
| onsite_days_per_week | 9 | 0.89 | 0.67 | 1.00 |
| remote_eligibility | 9 | 0.67 | 1.00 | 0.67 |
| seniority | 9 | 0.89 | 0.89 | 0.88 |
| employment_type | 9 | 1.00 | 0.56 | 1.00 |
| salary | 9 | 1.00 | 0.22 | 1.00 |
| city | 9 | 1.00 | 0.22 | 1.00 |
| country_code | 9 | 0.89 | 0.33 | 0.67 |
| industry | 9 | 0.44 | 0.56 | 0.80 |

Per source, accuracy (n): see `ranker eval ingest --markdown`. Only `in_scope` has more than 10
records per source; the other columns are 1 to 3 postings per source and are not worth quoting.

CI floors (`backend/tests/fixtures/ingest_eval/floors.json`) are the measured accuracy minus 0.05,
rounded down: in_scope 0.86, remote_type 0.83, onsite_days_per_week 0.83, remote_eligibility 0.61,
seniority 0.83, employment_type 0.95, salary 0.95, city 0.95, country_code 0.83, industry 0.39.
Raising a floor is fine; lowering one needs a dated note here.

## Error patterns (titles only)

1. Industry is `unknown` for companies outside the hand-curated map: "Sr Analyst, Analytics &
   Insights" (BeyondTrust), "Senior Software Engineer - Behavioral Security Products" (Abnormal),
   "Staff Scientist, Machine Learning (Genomics)" (Natera), "Full-Stack Developer" (Fluence).
   Linear was mapped to `devtools` where the label says `saas`. Suggested: infer from the
   description for unmapped companies, or accept `unknown` and let the LLM enrichment fill it.
2. Region lists that name a country or "North America": "UK Only" (Abnormal) became `eu_only`,
   and "Remote (US)" plus "Remote (Canada)" (Ramp) and "North America" (BeyondTrust) became
   `us_only`. Suggested: a UK token separate from EU, and Canada/North America as `other_region`.
3. Seniority for "Senior / Staff" (Linear) came out `lead`. Suggested: take the first level in a
   slash list.
4. Remote jobs with no location (Canonical "Site Reliability Engineering Manager", "Home Based")
   got `remote_type` unknown and no on-site days. Suggested: treat "home based" as remote.
5. Scope: "AM/ Manager - Risk & Decision Science" (Meesho) was skipped as `not_tech` (gold: data
   science family, in scope). Five non-engineering roles stayed in because the location was
   remote ("AI Transformation Owner, CRO", "Head of Ecosystem", "Developer Relations", "AI Trainer
   - Video Annotation") or the source hint said remote for an office role ("Software Engineer, AI
   Connections" at Notion, gold: hybrid in the US, so out). Suggested: title rules for `ai`
   should not admit non-engineering titles, and a source remote hint should not override a
   description that names office days.

Not fixed in this plan on purpose; fixing them against this set would overfit it.

## Next steps

- Capture a second, in-scope-biased sample (filter to tech titles before spreading) and the
  Internshala listings once the challenge page clears, to reach at least 100 records with 40 or
  more in-scope.
- Owner review of 20 or more labels (plan 02-15), then record the count here.
