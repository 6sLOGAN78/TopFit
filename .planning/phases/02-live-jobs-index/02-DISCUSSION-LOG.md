# Phase 2: Live Jobs Index - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md. This log keeps the alternatives that were considered.

**Date:** 2026-11-21
**Phase:** 02-live-jobs-index
**Areas discussed:** Job target & sources, Which jobs count, LLM key & budget now, Salary & currency

---

## Job target & sources

| Option | Description | Selected |
|--------|-------------|----------|
| Pull in Greenhouse/Lever | Add public company boards if Remotive+Adzuna fall short | ✓ |
| Accept the smaller number | Ship with two sources, record the real number | |
| Decide after measuring | Ask again after the ING-00 spike | |

| Option | Description | Selected |
|--------|-------------|----------|
| ≥ 10k, honest floor | Keep the SPEC goal; record the real number if unreachable | ✓ |
| Quality over volume | About 3–5k well-matched jobs | |
| You decide | Set from the ING-00 measurement | |

| Option | Description | Selected |
|--------|-------------|----------|
| Curated YAML | Hand-picked 100–200 boards | |
| Auto-discover boards | Discover slugs from ingested data | ✓ |
| Curated + discovery report | Curated list plus suggestions | |

| Option | Description | Selected |
|--------|-------------|----------|
| Seed list + relevance filter | ~30 seed boards; add discovered boards with ≥ N in-scope India/remote jobs; log additions | ✓ |
| Pure discovery | No seeds | |

| Option | Description | Selected |
|--------|-------------|----------|
| Not yet: add to checkpoint | Adzuna signup becomes a human step | ✓ |
| Already have it | | |

**Notes:** Auto-discovery was chosen first. A guardrails follow-up then settled on seed list + relevance filter.

---

## Which jobs count

| Option | Description | Selected |
|--------|-------------|----------|
| Engineering + data | SWE, mobile, DevOps, data, ML, QA, security | ✓ |
| Broad tech | Also PM, design, support, IT admin | |
| Engineering only | | |

| Option | Description | Selected |
|--------|-------------|----------|
| India + remote | India on-site/hybrid + remote worldwide, tagged | ✓ |
| India + India-open remote | Drop US/EU-only remote at ingest | |

| Option | Description | Selected |
|--------|-------------|----------|
| Show, flagged | Unknown region is shown below confirmed ones, with a badge | ✓ |
| Hide by default | | |
| Treat as worldwide | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Rules first, LLM fallback | Source fields + regex; LLM only if unknown, cached | ✓ |
| Rules only | No LLM cost in phase 2 | |

---

## LLM key & budget now

| Option | Description | Selected |
|--------|-------------|----------|
| Not yet: add to checkpoint | Build on the fake provider; key before the production backfill | ✓ |
| Have one already | | |
| Different provider | | |

| Option | Description | Selected |
|--------|-------------|----------|
| $5/month to start | Raise when chat arrives | ✓ |
| $20/month (SPEC default) | | |
| $0 rules only | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Save now, enrich later | Unknown + task queued for retry | ✓ |
| Save as unknown, no retry | | |

---

## Salary & currency

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed rates in config | `config/fx.yaml` with an as_of date | ✓ |
| Daily free FX API | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Store + show as estimate | Flagged; used in filters only if the user allows | ✓ |
| Store but hide | | |

---

## Claude's Discretion

- Schedule times, admin endpoint protection, skills taxonomy sourcing, the N threshold and seed list for discovery, the migration layout, expiry thresholds within ING-08, and simple dedupe.

## Deferred Ideas

- Live FX API; "region not stated" / "estimate" UI badges (later UI phases); HN ingestion and full dedupe stay in their roadmap phases.
