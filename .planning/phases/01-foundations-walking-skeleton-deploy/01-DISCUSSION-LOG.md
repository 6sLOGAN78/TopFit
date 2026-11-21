# Phase 1: Foundations & Walking-Skeleton Deploy - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-11-21
**Phase:** 01-foundations-walking-skeleton-deploy
**Areas discussed:** none individually (user delegated all areas to Claude)

---

## Area selection

| Option | Description | Selected |
|--------|-------------|----------|
| Hosting & domain | VPS provider/region, domain name, OK to buy now? | |
| Deploy pipeline | GitHub Actions auto-deploy vs manual; public/private repo | |
| Web shell hosting | Vercel vs same VPS behind Caddy | |
| Dev workflow | All-Docker vs native; pre-commit; branching | |

**User's choice (free text):** "you can choose everything according to yourself and this project man"
**Follow-up (mid-turn):** "is there any way i can get free domain and all"
**Notes:** User is a student and wants zero-cost options. Claude chose the GitHub Student Developer Pack (free domain + DigitalOcean credit, Bangalore droplet), free DDNS / Oracle Always Free as fallbacks, and a single-origin Caddy topology so any hostname works without cross-site cookie issues.

---

## Claude's Discretion

All Phase 1 decisions (D-01..D-22 in CONTEXT.md):
- Hosting: DigitalOcean BLR1 via student credit (overrides the research default of Hetzner Singapore).
- Topology: single origin with web on the server (overrides the research default of Vercel + an api subdomain).
- Deploy: public repo, GHCR + SSH CD on push to `main`, plus a `make deploy` fallback.
- Local: `make dev` via Compose with hot reload.
- Skeleton: `/api/readyz` checks, RSS ADR, cookie round-trip, PII-redacting logs.

## Deferred Ideas

- Sentry/uptime → Phase 5
- Backups → Phase 6 (maybe an earlier nightly pg_dump)
- Vercel previews → not planned
- Prometheus metrics → Phase 6
