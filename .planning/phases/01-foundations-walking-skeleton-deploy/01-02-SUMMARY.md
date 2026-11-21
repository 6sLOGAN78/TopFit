---
phase: 01-foundations-walking-skeleton-deploy
plan: 02
subsystem: web
tags: [nextjs, tailwind4, shadcn, status-page]
requires: [01-01]
provides:
  - pinned Next.js 16 app in web/ with standalone output
  - status page doing healthz/readyz/cookie round-trip checks from the browser
affects: [01-04]
tech-stack:
  added: [next 16.4.6, react 19.3.1, typescript ~5.9.3, tailwindcss 4.3.3, shadcn 4.23, clsx, tailwind-merge, prettier 3.10.1]
key-files:
  created: [web/package.json, web/next.config.ts, web/src/components/status-panel.tsx, web/src/app/page.tsx, web/components.json, web/src/lib/utils.ts, web/src/components/ui/button.tsx]
  modified: []
key-decisions:
  - "Dev-only /api rewrite via API_PROXY_TARGET; no rewrites in production (Caddy routes /api)"
requirements-completed: [FND-02, OPS-04]
duration: ~25 min
completed: 2026-11-21
---

# Phase 1 Plan 02: Web walking skeleton Summary

Pinned Next.js 16 app (standalone output, Tailwind 4, shadcn/ui) whose landing page runs live API, DB, pgvector, embedding and cookie round-trip checks from the browser over same-origin /api.

## Tasks

| Task | Commit |
|------|--------|
| 1 Scaffold pinned app | 9b0e8ca |
| 2 Status page | 2d36eff |

## Verification

lint, typecheck and build pass; `.next/standalone/server.js` exists; tsc 5.9.3. Live check against the real backend (pgvector 0.8.7-pg18 container, EMBEDDER=fake) through the Next dev rewrite on :3000: `/api/readyz` returned status ok (pgvector 0.8.7, dim 384); `/api/session/ping` returned `cookie_received:false` then `true` with a curl cookie jar; `/` contained "Ranker". Servers and container were stopped afterwards. The browser UI itself was not rendered in a real browser.

## Deviations from Plan

1. **[Rule 3 - Blocking] create-next-app 16.4.6 template differs from plan**: it generated `@tailwindcss/turbopack` with a turbopack loader rule (no postcss.config.mjs), `AGENTS.md` and `README.md`, and added `cacheComponents`/`partialPrefetching` to next.config.ts. I kept the turbopack Tailwind rule, dropped the experimental flags, and committed the generated AGENTS.md/README.md. `web/postcss.config.mjs` was therefore not created.
2. **[Rule 1 - Bug/supply chain] shadcn `utils.ts` re-exported `cn` from the third-party npm package `cn`**, which is not in the legitimacy audit. Replaced with the standard `clsx` + `tailwind-merge` implementation, removed the `cn` dependency, and fixed the import in `button.tsx`.
3. shadcn init added the Geist Google font via `next/font/google` in layout.tsx (fetched at build time); the build succeeded here, but it needs network at image build in plan 01-04.
4. `npm audit` reports 4 moderate advisories via the `shadcn` CLI package (ts-morph chain); it is a runtime dependency entry as written by shadcn. Left as is; consider moving to devDependencies later.
5. Commit trailer uses `Co-Authored-By: Claude Sonnet 5.5` per the session attribution instruction, not the plan's Opus line.

## Known Stubs

None.

## Self-Check: PASSED

Files exist (web/package.json, web/next.config.ts, status-panel.tsx, page.tsx); commits 9b0e8ca and 2d36eff present.
