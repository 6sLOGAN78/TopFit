# Phase 3: Hybrid Search & Eval Baseline - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md. This log keeps the alternatives that were considered.

**Date:** 2026-11-22
**Phase:** 03-hybrid-search-eval-baseline
**Areas discussed:** Search page & job cards, Query parsing & filter chips, Ranking before profiles, Eval set & grading

---

## Search page & job cards

| Option | Description | Selected |
|--------|-------------|----------|
| Search-first hero | Big search box, example queries, job count; results replace hero | ✓ |
| Results right away | Default feed under the box | |
| Chat-style box | Conversational input (chat is Phase 5) | |

| Card field (multi) | Selected |
|---|---|
| Location + work mode | ✓ |
| Salary (INR LPA) | ✓ |
| Matched skills | ✓ |
| Why it matched (rule-based) | ✓ |

| Ghost jobs | Selected |
|---|---|
| Show with warning, ranked lower | ✓ |
| Hide by default | |
| Show normally | |

| Results | Selected |
|---|---|
| 20 + Load more | ✓ |
| 10 per page, numbered | |
| Infinite scroll | |

---

## Query parsing & filter chips

| Parse strictness | Selected |
|---|---|
| Strict only on clear signals (city, salary, work mode, seniority) | ✓ |
| Everything becomes a chip | |
| Nothing hard by default | |

| Parse fallback model | Selected |
|---|---|
| Claude Haiku, Jev as optional ablation | |
| Jev first, Haiku fallback | |
| Rules only | |
| **Other:** "both options, user can decide if they want to use Haiku or Jev" | ✓ |

Follow-up: who picks? Options were a visitor toggle on the search page, an operator env setting, or both. **User's answer:** "can be option in user setting panel, it can be changed by user". The result is a settings-panel choice, stored per browser for guests until accounts arrive.

| Default geography | Selected |
|---|---|
| India + remote open to India | ✓ |
| Everything, ranked | |
| India only | |

| Salary filter | Selected |
|---|---|
| Keep unknown (penalised); estimates count with toggle | ✓ |
| Keep unknown, estimates off | |
| Strict | |

| Relax filter | Selected |
|---|---|
| Suggest per-filter relaxations with counts | ✓ |
| Auto-relax and say so | |
| Both | |

**Notes:** In the middle of the discussion, the owner pasted research notes on Jev (TypeSafe AI decision model, September 2026; claims mostly unverified) and asked to add it to Phase 3 and Phase 4.

---

## Ranking before profiles

| Signals (multi) | Selected |
|---|---|
| Freshness boost | ✓ |
| Ghost-job penalty | ✓ |
| Region certainty | ✓ |
| Salary known | ✓ |

| Weights | Selected |
|---|---|
| Hand-set in config, tuned on eval | ✓ |
| Pure RRF for now | |
| You decide | |

| Diversity | Selected |
|---|---|
| Max 2 per company in top 10 | ✓ |
| No cap | |
| Group by company | |

---

## Eval set & grading

| Grading | Selected |
|---|---|
| Claude drafts all, owner blind-reviews a subset | ✓ |
| Owner grades everything | |
| Claude only | |

| Snapshot | Selected |
|---|---|
| Private artifact; ids + labels public | ✓ |
| Commit publicly, trimmed | |
| Synthetic | |

| Jev eval in Phase 3 | Selected |
|---|---|
| Yes: query-parse accuracy (rules vs Haiku vs Jev) | ✓ |
| No, wait for Phase 4 | |

---

## Claude's Discretion

Left to Claude:
- exact vs HNSW (by benchmark)
- tsvector details and synonyms
- the OpenAPI client generator, Scalar and Schemathesis config
- the rate-limit table
- the `search_logs` schema
- deploy smoke wiring
- visual style and mobile layout
- decay and penalty sizes

## Deferred Ideas

- Jev for Phase 4: dealbreaker set-aside, fit label, and a Jev vs Haiku ablation row. Also added to the ROADMAP Phase 4 notes.
- Jev as a Phase 2 enrichment provider. Phase 2 is not changed now.
- Account-backed settings (Phase 5).
- Learned weights and persona cross-validation (Phase 4).
