---
phase: 03-hybrid-search-eval-baseline
plan: 08
subsystem: web-search-ui
tags: [nextjs, shadcn, base-ui, tanstack-query]
requires: [03-03, 03-05, 03-06]
provides:
  - editable parse chips, parse summary, set-aside line, relax suggestions
  - header settings sheet for the query parsing model
affects: []
key-files:
  created:
    - web/src/lib/filters.ts
    - web/src/lib/parse-model.ts
    - web/src/components/search/filter-chips.tsx
    - web/src/components/search/parse-summary.tsx
    - web/src/components/search/set-aside-line.tsx
    - web/src/components/search/relax-suggestions.tsx
    - web/src/components/search/settings-sheet.tsx
  modified:
    - web/src/components/search/results-list.tsx
    - web/src/components/search/search-page.tsx
    - web/src/components/search/site-header.tsx
requirements-completed: [UI-05, UI-06]
completed: 2026-11-22
---

# Phase 3 Plan 08: Parse feedback loop Summary

Users see how their query was parsed as editable chips, what was set aside, and one-click relaxations. Every edit writes explicit filters to the URL with auto=0.

## Commits
- 41d9c98 feat(03-08): filter transforms, set-aside/relax wording and parse-model storage
- 0d4d681 feat(03-08): parse feedback chips, set-aside line, relax suggestions and settings sheet

## Results (web/)
typecheck, lint, format:check, test (37 passed, 13 new) and build all pass. Only the generated typed client is used. localStorage is touched only in lib/parse-model.ts.

## Deviations / notes
- Relax suggestions skip `geo`: there is no way for a user to relax US/EU-only remote, and the UI-SPEC lists no label for it. It still appears in the set-aside line.
- Parse model is a `useSyncExternalStore` hook (`useParseModel`) so the header sheet, which is outside the page, re-runs the search through the changed query key. The re-run announcement is in the sheet's own live region, because the results region is behind the modal overlay.
- ResultsList is keyed on the URL (from 03-06), so chips are replaced by skeletons while a new search loads rather than kept (UI-SPEC says to keep previous chips). Focus after a chip edit/remove/relax is handed over through a module-level request consumed when the new results mount.
- Before /api/meta loads, a stored `jev` is treated as allowed. If meta then lacks jev, the search re-runs once with haiku.
- Added `useParseModel` and `relaxItems`/`stateFromFilters` helpers beyond the plan's export list.

## Not verified
The manual check against a seeded backend (chips, remove salary chip, auto=0 persisting after reload) was not run: no seeded DB here. The City combobox nested inside a popover, focus handoff and the sheet were also not exercised in a browser. Browser verification is owed.

## Known Stubs
None.

## Threat Flags
None. T-03-25: parse model restricted to meta.parse_models, enums filtered by readSearchParams. T-03-26: chip labels rendered as React text only.

## Self-Check: PASSED
