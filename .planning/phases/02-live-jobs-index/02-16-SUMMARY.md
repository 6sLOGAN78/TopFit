---
phase: 02-live-jobs-index
plan: 16
subsystem: ingest
tags: [arbeitnow, remoteok, jobicy, internshala, polite-fetcher, robots, scraping]
requires: [02-11, 02-13, 02-14]
provides:
  - Arbeitnow (absence), RemoteOK and Jobicy (ttl) adapters
  - PoliteFetcher (D-15) and the Internshala listing-card adapter
  - sources.yaml sections with D-17 schedules; ADR 0001 Himalayas/Jobicy lines
affects: [02-17]
key-files:
  created:
    - backend/ranker/ingest/polite.py
    - backend/ranker/ingest/sources/apiutil.py
    - backend/ranker/ingest/sources/arbeitnow.py
    - backend/ranker/ingest/sources/remoteok.py
    - backend/ranker/ingest/sources/jobicy.py
    - backend/ranker/ingest/sources/internshala.py
    - backend/tests/test_sources_free_apis.py
    - backend/tests/test_polite.py
    - backend/tests/test_sources_internshala.py
    - backend/tests/test_new_sources_e2e.py
    - backend/tests/fixtures/ (arbeitnow_page1/2, remoteok, jobicy json; internshala robots + 3 html)
  modified:
    - backend/config/sources.yaml
    - backend/ranker/ingest/sources/__init__.py
    - docs/decisions/0001-sources.md
requirements-completed: [ING-04, ING-08, ING-12]
completed: 2026-11-22
---

# Phase 2 Plan 16: Free APIs and Internshala

Four more sources run through the shared pipeline as plugins; only `ingest/sources/`, the new `ingest/polite.py`, `config/sources.yaml`, fixtures, tests and the ADR changed (pipeline, store, cli, expiry and base untouched).

## Commits
- 288aefb: Arbeitnow, RemoteOK, Jobicy adapters + yaml + fixtures + tests
- d701ea7: PoliteFetcher and Internshala adapter
- 1b77d5c: security fix (no redirects) and Internshala category hint
- dc9e2e1: end-to-end tests, ADR lines

## Interfaces
- `PoliteFetcher(client, *, user_agent=USER_AGENT, min_interval_s=3.0, sleep=asyncio.sleep, clock=time.monotonic)`; `await allowed(url) -> bool`; `await get_text(url) -> str`; `.requests_made`. Raises `RobotsDisallowed` (no request made) or `FetchRefused(reason, status)` (str is `reason:status`, no URL). A refusal latches the host for the rest of the fetcher's life.
- Internshala card mapping: `data-href` (or title href) detail path -> `source_job_id` (trailing digits) and absolute url; `job-title-href` -> title; `company-name` -> company; `locations` -> location_parts (Work from home -> workplace remote, no location_text); `ic-16-money` container -> salary_text (kept only if it has a digit; currency INR); `ic-16-briefcase` container -> experience (jobs only); `job_skill` -> skills_hint; age label -> posted_at = run now minus age. description_raw = title, company, location, experience, salary, skills only (no age text), so content_hash is stable. scope = `jobs/<slug>` or `internships/<slug>`. country IN, region India.

## Decisions
- Internshala sets `category_hint` ("software development", "data", "artificial intelligence") from the listing category, because titles like "Web Development Intern" fail the title-only scope regex; exclude rules still win (a Marketing Intern fixture stays out).
- Challenge regex narrowed from the spike (bare "challenge" removed; it appears in ordinary job text).
- Other 4xx (404) raise FetchRefused without latching; 403/429/5xx/3xx/challenge/transport errors latch the host.
- A card listed in two categories is yielded once under the first category (so absence expiry could mark it missed if only its first category drops it; accepted, noted).

## Deviations from Plan
1. [Rule 2 - Security, coordinator-requested] PoliteFetcher originally inherited `follow_redirects=True` from `build_client()`. Now every request passes `follow_redirects=False`; any 3xx is a refusal (latches host, no request to Location); robots 3xx fails closed; response origin must match the request. Tests: 301 to a detail page, 302 to another host, 302 to a listing page, robots redirect, all with zero requests to the target. Commit 1b77d5c.
2. [Rule 1] `urllib.robotparser` has no wildcard support, so ADR's `Disallow: /*?*` is not honoured by it; the adapter's path guard (no `?`, `,`, `#`, strict path regex) enforces those rules instead. Tests use literal prefixes.
3. [Constraint] No live Internshala sample was taken; fixtures are small hand-made HTML in the card shape recorded by the spike markers (real class names, not copied postings). Real-markup differences are possible; the first live run should be checked via `ranker ingest internshala` skipped/invalid counts.
4. Commit d701ea7 landed with one ruff E501 in a docstring; fixed in 1b77d5c (full suite is clean at HEAD).
5. RemoteOK/Jobicy/Arbeitnow invalid items show as `failed` in RunSummary (existing pipeline behaviour).

## Verification
Full suite 886 passed; `ruff check .`, `ruff format --check .` and `pyright` clean. No live network (respx everywhere; detail-page routes assert-fail if hit).

## Known Stubs
None.

## Threat Flags
None beyond the plan's register (T-02-62..66 mitigated; redirect handling added above).

## Self-Check: PASSED
