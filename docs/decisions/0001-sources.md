# ADR 0001: Job sources, volume, terms and targets (ING-00)

Status: accepted (owner decisions D-14..D-16 on 2026-11-22). Measured with `uv run python -m ranker.spike.sources` (raw numbers: `0001-sources-spike.json`, measured 2026-11-22 ~03:58 IST; Adzuna credentials are never written to it).

## Context

SPEC asks for a fresh index of India/remote tech jobs. Research found Remotive too small and Adzuna's quota unknown, so the source mix was left to a measurement spike before any adapter code. The owner then chose (D-14) to use every source reachable without logins or anti-bot bypass, which overrides the "official APIs only" default for this non-commercial portfolio project.

## Measurements

"In scope" is the spike heuristic (tech title regex AND India or remote location text). It over-counts India eligibility for "remote" postings, so treat it as an upper bound. Production scope rules come in plan 02-04.

| Source | Method | Total seen | In scope (est.) | Notes |
|---|---|---|---|---|
| Remotive | API | 16 | 6 | Location mix: Worldwide 7, USA-only 6, other 3. Tiny feed. |
| Adzuna (IN) | API | 102,143 all, 35,034 in `it-jobs` last 30 days | not computed (snippets only) | See Adzuna section. |
| Greenhouse (42 slugs) | API | 4,340 on 14 live boards | 326 | 28 slugs 404 (most Indian unicorns are not on public Greenhouse boards). |
| Lever (10 slugs) | API | 310 on 5 live boards | 29 | cred, meesho, paytm, zeta, mindtickle answered 200; EU host fallback found nothing. |
| Ashby (9 slugs) | API | 1,410 on 8 live boards | 414 | ramp, notion, openai, linear, supabase, posthog, replit, sarvam. retool 404. |
| Arbeitnow | API | 1,500 (5 pages, capped) | 32 (in scope AND India/remote-eligible) | 421 tech titles, only 106 India/remote eligible. Germany-centric. |
| Himalayas | API | 120,098 listed | 5 of 20 sampled were tech; 0 of 20 India-eligible | Max 20 per request; practically nothing India-eligible in the sample. |
| Jobicy | API | 100 | 0 India-eligible | The `geo=india` filter returned HTTP 400, so only the unfiltered feed was measured. |
| RemoteOK | API | 100 | 7 | 19 of 100 India/remote eligible. |
| HN "Who is hiring" | Algolia API | 242 top-level comments (thread of 2026-11, 381 total) | 142 mention remote or an India city | Needs LLM extraction (later phase); no LLM used in the spike. |
| Internshala | scrape (listing pages) | Jobs: python 159, software-dev 132, web-dev 189, data-science 244, java 56 (780 listed, overlapping) | all India | Internship categories: 3 to 8 pages each (28 pages, about 1,400 cards, overlapping). |
| Naukri | probe | n/a | n/a | robots.txt disallows: blocked, not used. |
| LinkedIn guest jobs | probe | n/a | n/a | robots.txt disallows: blocked, not used. |
| Wellfound | probe | n/a | n/a | robots allow but the page returned a challenge: blocked, not used. |
| Indeed (in.indeed.com) | probe | n/a | n/a | One plain request returned HTTP 200. Not used: no adapter planned, and Indeed's terms forbid automated collection. |

Adzuna probe details (9 calls used of the 25 test calls): IT category tag `it-jobs`; `max_days_old` 1/7/30 gave 4,019 / 12,295 / 35,034 in-category jobs; `what_or` developer/engineer/software/data/devops last day 7,641; Bangalore and Bengaluru both 24,486 (same city); `results_per_page=100` still returns 50 (cap is 50); page 40 works (50 results) so deep paging is available; 350 sampled adverts: `salary_is_predicted` share 0.0, `full_description` share 0.0 (search results carry a 500-character snippet only, so descriptions need another source or a detail call), `contract_time` present 96.6%, `salary_max` under 200,000 only 2.3% (salaries are annual INR). No rate-limit headers were returned.

Internshala cards (listing pages only, 50 per page): title, company, location, salary and the detail link are present on 100% of cards; skills 86-100%; posted age 100%; experience present on jobs (66-100%) but never on internships. Internship pages measured 50 to 57 cards because some pages include promoted blocks.

## Quota

Adzuna dashboard text, verbatim: "Plan: Pending activation: 25 test calls". The dashboard asks to add a card and choose a plan; the banner says Pro gives 20,000 calls a month, with prices shown only when choosing a plan. The owner will not add a card.

## Terms per source

- Remotive: link back and credit Remotive, at most 4 fetches per day, no signup wall in front of its jobs, no resubmission to third-party job sites.
- Adzuna: owner chose to use it ("use adzuna too", treated as terms-ok). Show the Adzuna label and `redirect_url` on every advert, "Adzuna Jobsworth" label for estimated salaries, and remove all Adzuna data on termination via `ranker purge-source adzuna`.
- Greenhouse, Lever, Ashby: public unauthenticated board APIs; 1 request per second; attribute the company board on each card.
- Arbeitnow, Himalayas, Jobicy, RemoteOK: free public APIs that require a visible credit and a link back to the original listing; show the source name on every card. Himalayas and RemoteOK ask for the link to point at their page.
- HN: public Algolia API; link each posting to its HN comment.
- Internshala: scraping rules below; listing-card fields only, every card links to the Internshala detail URL.

## D-15 scraper rules

Honest User-Agent `RankerBot/0.1 (+https://topfit.duckdns.org; portfolio project)`; robots.txt fetched and obeyed at runtime with `urllib.robotparser`; at most 1 request per 3 seconds per host; no login, CAPTCHA or anti-bot bypass, no proxies or UA rotation, no retries on a refusal; only listing fields plus the source URL are stored; the source is attributed on every card; `purge-source` must work for each source. Internshala's robots.txt disallows query strings, commas, `/job/details/`, `/internship/details/` and search pages, so only `/jobs/<category>-jobs/` and `/internships/<category>-internship/` listing pages are fetched and detail pages never are. Measured with this behaviour on 2026-11-22: 10 category pages, robots fetched once.

## Volume math

Adzuna steady state = min(new in-scope per day x 30-day TTL, calls/day x results_per_page x 30). With 0 calls/day this is 0. Had a plan been active, 35,034 IT jobs per 30 days would exceed any target, so Adzuna alone could carry the index.

Measured free sources (upper bounds, before cross-source dedupe): boards 769 + Remotive 6 + Arbeitnow 32 + RemoteOK 7 = 814 from APIs; Internshala about 2,180 listed across the chosen categories, halved for category overlap = about 1,090; HN adds up to 142 after LLM extraction (not counted). Sum about 1,900. After dedupe and scope filtering, 1,500 is the honest figure.

## Decision

All reachable sources are used (D-14). Cadence follows D-17: one morning batch per source finishing before 06:30 IST (about 01:00 UTC), re-reading every live posting so closed jobs leave the same day; extra intraday runs only where terms allow (Remotive up to 4 per day). Method: Remotive, Greenhouse, Lever, Ashby, Arbeitnow, Himalayas, Jobicy, RemoteOK, HN and Adzuna are APIs; Internshala is the only scrape. Naukri, LinkedIn guest, Wellfound and Indeed are blocked, not used.

Adzuna is pending activation with 25 test calls and paid plans need a card, so the adapter ships (plan 02-10) but its schedule stays disabled until a plan is active. The budget below is therefore 0; set it to at most 0.8 x daily quota once a plan exists.

"data analyst" titles count as in scope (data family); sysadmin and IT admin do not (D-05 default).

Himalayas: not ingested — 0 of 20 sampled postings were India-eligible (spike 2026-11-22); revisit if its API gains a location filter.
Jobicy: ingested without the geo filter (geo=india returned HTTP 400); expected India-eligible yield is near 0, kept for remote-worldwide postings.

Active-job target: 1500
The 10,000 of D-02 is unreachable without Adzuna; the free sources measured about 1,900 listed jobs, about 1,500 after dedupe and scope filtering. Raise the target if an Adzuna plan is activated.

Adzuna calls/day budget: 0
Adzuna results_per_page: 50
Adzuna IT category: it-jobs
Adzuna salary period: year
ING-13 decision: pull-in
Pull-in is forced by D-14 (use every reachable source), not by the D-01 volume rule alone; Greenhouse, Lever and Ashby also supply most of the measured API volume (769 of about 814).

## Production measurement (2026-11-22)

Host topfit.duckdns.org (GCP e2-medium, 4 GB), image `sha-f2d24ec`. Initial backfill ran 14:59 to 15:16 UTC, one source at a time, as one-off `run --rm --no-deps worker` containers. Checks before the backfill: readyz ok (pgvector 0.8.7, 384-d embedding), `alembic current` 0005 (head), `ranker.selfcheck` ok with all eight configs, exactly one `scheduler_lock_acquired` in the worker logs.

### Counts

| Measure | Value |
|---|---|
| Active canonical jobs with an embedding (`BAAI/bge-small-en-v1.5`) | 1838 |
| Active-job target | 1500 |
| Active jobs, all rows | 2063 (1838 canonical, 225 duplicates grouped under a canonical) |
| Canonical without an embedding | 0 |
| Ghost-flagged (active) | 841 |
| Multi-source groups (active) | 13 |
| Adzuna calls | 0 (disabled, budget 0) |

Per source (active canonical embedded): arbeitnow 707, greenhouse 621, ashby 431, lever 38, remoteok 26, jobicy 8, remotive 7, internshala 0. That is seven of the eight enabled sources. Internshala is the gap: the first request from the production host returned a bot-challenge page (`source_refused`, reason `challenge:200`), so the fetcher stopped after 2 calls by design (D-15, no bypass or retry). The run is recorded as `failed` with no rows written, and nothing was expired. The 1500 target is met without it, but the scheduled Internshala runs will keep failing from this host unless the page stops challenging; there is no fix inside the D-15 rules.

Cities: Bengaluru 142 and Gurugram 27 active jobs; no "Bangalore" or "Gurgaon" spellings remain.

### Ingest runs (D-20 counters)

| source | mode | status | start UTC | fetched | in_scope | inserted | updated | unchanged | expired | failed | skipped | dup grouped | ghost | reposts | calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| greenhouse | incremental | succeeded | 14:59 | 4337 | 782 | 782 | 0 | 0 | 0 | 0 | 3555 | 149 | 568 | 0 | 13 |
| lever | incremental | succeeded | 15:04 | 309 | 38 | 38 | 0 | 0 | 0 | 0 | 271 | 0 | 28 | 0 | 5 |
| ashby | incremental | succeeded | 15:05 | 1410 | 458 | 458 | 0 | 0 | 0 | 0 | 952 | 27 | 245 | 0 | 8 |
| internshala | incremental | failed | 15:07 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| arbeitnow | incremental | succeeded | 15:07 | 5993 | 744 | 744 | 0 | 0 | 0 | 0 | 5249 | 49 | 0 | 0 | 54 |
| remoteok | incremental | succeeded | 15:14 | 100 | 26 | 26 | 0 | 0 | 0 | 0 | 74 | 4 | 0 | 0 | 1 |
| jobicy | incremental | succeeded | 15:14 | 50 | 8 | 8 | 0 | 0 | 0 | 0 | 42 | 1 | 0 | 0 | 1 |
| remotive | incremental | succeeded | 15:14 | 16 | 7 | 7 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 1 |
| remotive (admin endpoint) | incremental | succeeded | 15:19 | 16 | 7 | 0 | 0 | 7 | 0 | 0 | 9 | 0 | 0 | 0 | 1 |

Embedding ran inside each ingest run; the separate `embed --missing` pass found nothing left. The admin endpoint `POST /api/admin/ingest/remotive` returned 202 and the run completed.

### Board discovery

`boards discover`: 40 probes, 0 added, 40 rejected (almost all `not_found`: the Indian candidates such as browserstack, cashfree, chargebee, cleartax, clevertap and zepto have no public board on these ATSes; one `below_threshold:0`). Active seed boards: greenhouse 14, lever 5, ashby 8 (as measured in the spike).

### Enrichment and LLM spend

Anthropic key is not set on the server (owner chose skip-anthropic). `enrich_job` tasks: 1788 queued, none run. `llm_calls` this month: 0 calls, $0. Unknown D-19 fields stay `unknown` (industry is set from the curated company map where known).

### Memory (docker stats and cgroup `memory.peak`, MB)

| snapshot | worker (scheduler) | api | db | one-off run container |
|---|---|---|---|---|
| before-backfill | 78 (peak 78) | 298 (peak 574) | 43 (peak 104) | none |
| during greenhouse (stats sampled) | 78 | 298 | 81 | 1023 of 1024 limit, host swap used 513 |
| during arbeitnow ingest | 78 (peak 78) | 298 (peak 574) | 212 (peak 343) | 189 |
| during embed pass | 78 (peak 78) | 298 (peak 574) | 231 (peak 416) | 97 (peak 102) |

Finding: the Greenhouse one-off container sat at the 1 GiB `mem_limit` with host swap in use for about 5 minutes (it holds all 4337 raw postings plus the embedding model) and slowed down (some 32-row embed batches took 22 to 30 s against 6 to 8 s normally). It finished without an OOM kill. The scheduled Greenhouse run happens in the long-running worker, which has the same 1 GiB limit plus the model, so it can be killed; the next step is to stream or chunk the Greenhouse fetch, or raise the worker limit, before relying on the schedule.

### Sample (5 random active rows)

| source | title | country | remote_type | eligibility | salary INR/yr | seniority | employment | onsite days | industry |
|---|---|---|---|---|---|---|---|---|---|
| greenhouse | Principal Engineer - Privacy | none | unknown | us_only | none | lead | unknown | none | ai_ml |
| ashby | Software Engineer, Financial Engineering | US | remote | worldwide | 23,541,000 to 39,382,500 (stated) | senior | full_time | 0 | ai_ml |
| arbeitnow | Senior Consultant, Agentic AI & AI Ventures | none | unknown | eu_only | none | senior | unknown | none | unknown |
| greenhouse | Software Quality Assurance Engineer | none | unknown | unknown | none | mid | unknown | none | fintech |
| ashby | Security Engineer, Detection and Response | IE | remote | unknown | none | mid | full_time | 0 | saas |

Spot checks: Bengaluru and Gurugram canonical names are present; `visa_sponsorship` and `relocation` are `unknown` on all five rows (allowed); `industry` and `onsite_days_per_week` are populated where known. No Internshala rows exist, so Internshala detail URLs and source labels could not be checked on production.

### Ingestion eval headline (docs/evals/ingest-normalisation.md)

78 labelled real postings, 0 of 78 owner-reviewed so far. in_scope accuracy 0.91 (n 70); remote_type 0.89, seniority 0.89, employment_type 1.00, salary 1.00, city 1.00, remote_eligibility 0.67, industry 0.44 (n 9 each). Small-sample smoke test, not precision estimates.

ING-12 status: pending-backfill
Count is above target (1838 vs 1500) but the first full morning read and one intraday scheduled run have not completed yet, so the verdict stays pending until the Task 3 recheck after 24 h.
