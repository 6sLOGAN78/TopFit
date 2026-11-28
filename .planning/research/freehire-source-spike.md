# Spike: freehire.me as a job source (2026-11-28)

**Verdict: blocked under current terms and budget.** The adapter is straightforward to build, but the free API can't be used for Ranker. Do not build it unless the owner buys an API plan.

## What was checked
All requests used an honest user agent with 3 s between them.

- robots.txt: `/api/v1/jobs` is allowed for `*` (Googlebot is disallowed from `/api/`).
  - The apply redirect `/api/v1/go/` is disallowed, so any adapter must use the job's `url` field, never `apply_url`.
- `GET https://freehire.me/api/v1/jobs?limit=N&offset=M` works without a key.
  - Reply shape: `{data:[...], meta:{total, limit, offset}}`. Total was 8,556,074 jobs.
  - Fields: public_slug, source, external_id, url, apply_url, title, company, company_slug, location, cities, countries, regions, metros, description (HTML), skills, collections, is_tech, enrichment, posted_at, last_seen_at, closed_at, created_at, updated_at.
  - The free tier hides some data, such as the employer link: `url` was empty on the sample row.
- Terms of Service:
  > "Without a plan, the API is for personal and non-commercial use"
  - Showing their postings in a product offered to others, paid or not, needs an API plan. Ranker is a public site, so it needs one.
  - With a plan, storing the postings and showing them in your product is allowed, commercially too. Resale or redistribution of the raw data is not. When the plan ends, you must stop showing the postings.
- Pricing:
  - API plan: $29/month (goes to $39 on 1 Dec). Includes the original employer link and the right to show postings.
  - Data plan: $99/month (bulk files).
  - $29/month is about the whole ₹2,500/month project budget (CLAUDE.md constraint).

## Overlap
freehire mostly re-aggregates the same ATS boards we already ingest directly for free: Greenhouse, Lever and Ashby, plus Workday, iCIMS, aggregators and Telegram channels.

Its extra value for us would be Workday/iCIMS and Indian internship listings.

## If the owner buys the API plan later
Build `backend/ranker/ingest/sources/freehire.py` on the `apiutil` pattern:
- filter by countries=IN / remote / employment_type internship
- incremental by posted_at
- dedupe against the direct ATS copies via `external_id` + company
- source attribution "via freehire"
- link to `url`, never `/api/v1/go/`
- stop on 403/429 (D-15)

## Free alternatives with the same gain
- Add more Indian startup boards to the existing Greenhouse, Lever and Ashby adapters.
- Add a Workday adapter for the public CXS JSON endpoint, per tenant, under D-15.
