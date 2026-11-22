---
phase: 02-live-jobs-index
plan: 06
subsystem: ingest
tags: [skills, taxonomy, regex, nh3, html-cleaning, normalisation]

requires:
  - phase: 02-live-jobs-index
    provides: config_loader.load_config (02-02), nh3 dependency (02-01)
provides:
  - "SkillMatcher / extract_skills / skills_text over a 522-skill curated taxonomy"
  - "clean_description: untrusted or entity-escaped HTML to safe plain text"
affects: [02-08 pipeline wiring, Phase 3 full-text index (skills_text tokens)]

tech-stack:
  added: []
  patterns:
    - "Custom regex boundaries instead of \\b for symbol-bearing names"
    - "Bounded-cost matching: input caps, bisect lookups, fixed windows"

key-files:
  created:
    - backend/ranker/ingest/skills.py
    - backend/ranker/ingest/normalize/html.py
    - backend/config/skills.yaml
    - backend/tests/test_skills.py
    - backend/tests/test_skills_taxonomy.py
    - backend/tests/test_normalize_html.py
    - backend/tests/fixtures/skills_jd_samples.yaml
  modified: []

key-decisions:
  - "Ambiguous entries split aliases into weak (`aliases`, need context or skills-list position) and `strong_aliases` (always match), so Node.js is free while bare 'node' is gated"
  - "`implies` field generalises the ASP.NET -> dotnet compound rule"
  - "List context = alias stands alone as a separator-delimited item AND (>= 2 other strong skills on the line OR a skills/languages keyword before it)"

requirements-completed: [NORM-06, NORM-01]

duration: ~60min
completed: 2026-11-22
---

# Phase 2 Plan 06: Skills taxonomy and description cleaner Summary

**522-skill taxonomy with a symbol-safe, ambiguity-guarded matcher (C++/C#/.NET/Go/R handled, JavaScript never yields Java) plus an nh3-based HTML/entity-escaped description cleaner.**

## Accomplishments
- `SkillMatcher` compiled once from `skills.yaml`: BOUND_L/BOUND_R boundaries, longest alias first, case-sensitive and context-gated ambiguous aliases (Go, R, C, Swift, Rust, Express, Excel, Make, Unity, Spring, Spark ...), `skills_text` token strings (`cplusplus csharp dotnet golang nodejs`).
- `skills.yaml`: 522 entries, provenance header (Linguist MIT, O*NET CC BY 4.0, hand additions). Labelled check on 40 synthetic JD snippets passes the precision >= 0.90 / recall >= 0.80 gate.
- `clean_description`: Greenhouse unescape-then-sanitise, block tags to paragraph breaks, `li` to "- ", script/style content dropped, input cap 200k.

## Task Commits
1. Task 1 matcher + tests (54 cases): `0af63ae`
2. Task 3 description cleaner: `5667e09`
3. Task 2 taxonomy, fixture, taxonomy tests: `eb28c09`
4. Security fix, HTML regexes linear: `c31c1e2`
5. Security fix, skill matcher cost bounds: `8e0a072`

## Verification
`uv run pytest -q` 151 passed; `ruff check .` clean; `pyright` 0 errors.

## Deviations from Plan

### Auto-fixed / coordinator-directed
**1. [Rule 2 - Security, coordinator request] HTML tag regexes quadratic on unterminated openers**
- `[^>]*>` replaced by `[^<>]*>` in `_BLOCK_OPEN/_BLOCK_CLOSE/_BREAK/_LI_OPEN`; tests clean 100k chars of `<p`, `<br`, `<li`, `</p` in under 1 s. Commit `c31c1e2`.

**2. [Rule 2 - Security, coordinator request] Skill matcher quadratic on long single-line text**
- Separators computed once and looked up with `bisect`; list-keyword search and line bounds limited to a 200-char window; found canonicals skipped; weak matches capped at 5 per canonical; strong-overlap check via bisect. Separator regex rewritten without `\s+` runs (`\s+(?:and|or)\s+` was itself quadratic on whitespace). Tests: 50k-char single-line inputs finish < 1 s (observed 0.08-0.22 s). Commit `8e0a072`.

**3. [Plan wording] Hypothesis idempotence property**
- Plan suggested "re-cleaning is unchanged" generally; that does not hold when output legitimately contains `<` (e.g. unescaped `<C++>`). Property test restricted to inputs without `<>&` (stable, stripped, no triple newline) plus a never-raises/no-nbsp property over arbitrary text.

**4. [Schema addition]** `strong_aliases`, `implies`, and per-entry `extra=forbid` added to `SkillEntry` (beyond the plan's field list) to express per-alias ambiguity and compound rules.

### Provenance note
Linguist and O*NET were consulted as naming references and entered by hand; no network fetch or bulk import was made, and the exact O*NET release was not pinned (attribution line states "30.x" as the plan prescribed). Review before treating the provenance as audited.

## Known Stubs
None.

## Threat Flags
None. T-02-21/22/23 mitigated (nh3 no tags, script/style removed, caps and linear regexes, licence test).

## Self-Check: PASSED
All created files present; commits 0af63ae, 5667e09, eb28c09, c31c1e2, 8e0a072 exist.
