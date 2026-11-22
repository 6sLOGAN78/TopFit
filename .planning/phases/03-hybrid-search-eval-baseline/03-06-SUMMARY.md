---
phase: 03-hybrid-search-eval-baseline
plan: 06
subsystem: web-search-ui
tags: [nextjs, tanstack-query, shadcn, hey-api, tailwind]
requires: [03-02]
provides:
  - guest search page at / (hero, URL state, cards, Load more, degraded/429/error states)
  - format.ts and search-params.ts with vitest coverage
  - TopFit theme tokens and 20 shadcn components
affects: [03-08]
key-files:
  created:
    - web/src/lib/format.ts
    - web/src/lib/search-params.ts
    - web/src/lib/api-contract.test.ts
    - web/src/lib/__fixtures__/search-response.json
    - web/src/app/providers.tsx
    - web/src/components/search/
    - web/src/components/ui/ (19 new shadcn files)
  modified:
    - web/openapi-ts.config.ts
    - web/src/lib/api/zod.gen.ts
    - web/src/app/globals.css
    - web/src/app/layout.tsx
    - web/src/app/page.tsx
requirements-completed: [UI-01, UI-04]
completed: 2026-11-22
---

# Phase 3 Plan 06: Guest search page Summary

Guest search page on the generated hey-api client: navy TopFit hero with live job count, URL-driven search, attributed job cards with freshness and stale popover, cursor-based Load more, and degraded, 429 and error alerts.

## Commits
- 9ad1b6f feat(03-06): formatting and URL-state libs with contract test
- 2d8f4d4 feat(03-06): TopFit theme, shadcn components, hero, search bar and Suspense page
- 890b52f feat(03-06): job cards, results list with Load more, degraded/429/error states

## Results (web/)
- test: 24 passed (format, search-params, api-contract). typecheck, lint (max-warnings 0), format:check and build pass.
- No `fetch(` in components/search; data only via generated SDK/TanStack options.

## Deviations from Plan
- [Rule 3 - Blocking] `npx shadcn add` wrote `import { cn } from "cn"` in 10 registry files and added the unrelated npm package `cn` to package.json. I rewrote the imports to `@/lib/utils` and uninstalled `cn`; package.json and the lockfile are unchanged versus main. The unrelated package was never kept.
- [Rule 1] zod `iso.datetime()` rejected `+05:30` offsets, as the plan anticipated. Fixed with `dates: { offset: true }` in the zod plugin and regenerated (only zod.gen.ts changed).
- Fixture is built by serialising the backend Pydantic `SearchResponse` (UTC `Z` and `+05:30`, null `posted_at`), not captured from a running server, because that needs a seeded DB.
- Settings button omitted from the header: the sheet it opens is 03-08. Parse model is read inline from localStorage in results-list.tsx for 03-08 to replace.
- ResultsList is keyed on the URL so a new search starts clean (skeletons) rather than keeping previous results; previous results do stay visible when a refetch of the same search fails.
- shadcn `Skeleton` pulse made `motion-safe:`.

## Not verified
- The plan's manual smoke against a seeded backend was not run (no seeded DB in this worktree). Only built output and a `next start` check were done. `/` is statically prerendered with the Suspense fallback; the hero renders after hydration (expected, Pitfall 11). Visual/browser verification is still owed.

## Known Stubs
None.

## Threat Flags
None. T-03-18: no dangerouslySetInnerHTML, View posting only rendered for http(s) URLs with rel="noopener noreferrer". T-03-19: unknown mode/seniority dropped, responses zod-validated by the SDK.

## Self-Check: PASSED
