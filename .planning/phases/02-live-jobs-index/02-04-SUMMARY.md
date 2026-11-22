---
phase: 02-live-jobs-index
plan: 04
subsystem: ingest normalisation
tags: [location, remote-eligibility, scope, yaml-config, regex]
requires: [02-01, 02-02]
provides:
  - normalize_location, Location, detect_onsite_days, indian_city_names (location.py)
  - detect_remote_eligibility, RegionResult, RemoteEligibility (region.py)
  - classify_tech, classify_geo, classify_scope, ScopeDecision (scope.py)
  - cities.yaml, regions.yaml, scope.yaml; ADR 0002 (D-05/D-06/D-07/D-08 contract)
affects: [02-08, 02-12]
key-files:
  created:
    - backend/config/{cities,regions,scope}.yaml
    - backend/ranker/ingest/normalize/{location,region,scope}.py
    - backend/tests/{test_normalize_location,test_normalize_region,test_scope}.py
    - docs/decisions/0002-remote-eligibility-and-scope.md
key-decisions:
  - "Out-of-scope jobs are skipped, not stored; counted in ingest_runs.skipped with sample titles."
  - "Data analyst in scope; sysadmin/IT admin/helpdesk, support engineer and sales engineer out."
  - "Navi Mumbai and Thane are separate canonicals in the mumbai metro group."
  - "Singapore and APAC-without-India lists count as apac."
metrics:
  tasks: 3
  completed: 2026-11-22
---

# Phase 2 Plan 04: Location, region and scope rules Summary

Pure, YAML-driven normalisers for city/country/work mode (with on-site days per week), remote eligibility across seven values, and the tech plus geography scope filter, with ADR 0002 recording the downstream contract.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Location normaliser | 0513d3b | 23 cities, NCR multi-expansion, 73 tests incl. 17 on-site-days cases |
| 2 Remote eligibility | 04554f8 | 55 tests incl. the five live Remotive strings, hypothesis ReDoS check |
| 3 Scope rules + ADR | f295bc3 | 102 tests, 40 in-scope and 30 out-of-scope labelled titles |

## Verification

Full suite 280 passed; ruff check, ruff format --check and pyright (whole project) clean.

## Deviations from Plan

- [Rule 1 - Design] For on-site/hybrid jobs with a known country, region detection checks the job's country before the description (plan order was hint, description, location). A description naming Canada must not turn an Indian on-site job into `us_only`. Documented in ADR 0002.
- Hybrid keyword is checked before remote keyword in `remote_type`, so "hybrid ... remote" text resolves to hybrid.
- `classify_geo` returns reason `tech` as a neutral value for in-scope geography because the reason Literal has no dedicated "geo ok" value; `classify_scope` substitutes the tech reason.
- Bare "us", "eu", "anywhere", "global" are matched only in the short region hint (`hint_only` in regions.yaml), not in descriptions, to avoid false positives like "join us".

## Known Stubs

None.

## Self-Check: PASSED
