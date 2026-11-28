---
type: quick
slug: more-ats-boards
created: 2026-11-28
---
# Quick: add more India-hiring ATS boards (free alternative to freehire)

- Check about 150 Indian startup slugs on the public Greenhouse, Lever and Ashby board APIs.
  - Honest UA, 3 s per host, stop on 403/429 (D-15).
- Add boards that have India-located jobs to `backend/config/sources.yaml` seeds, plus their display names.
- Record the check results in `docs/decisions/0001-sources-spike.json`, so `test_yaml_seeds_match_spike` still requires evidence for every seed.
- Done when the full backend test run passes and prod ingests the new boards on the next scheduled run.
