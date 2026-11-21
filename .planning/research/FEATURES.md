# Feature Research

**Domain:** Conversational AI job matching / AI career agent (job-seeker side), India-focused, solo portfolio build
**Researched:** 2026-11-21
**Confidence:** MEDIUM. Competitor features come from vendor pages and press coverage, which is a mix of MEDIUM and LOW confidence. Remotive API behaviour was checked live today (HIGH).

---

## Context: what the market looks like (late 2026)

| Product | What it actually does for job seekers | Relevance to Ranker |
|---|---|---|
| **Elip (YC W25), the target employer** | You share a resume and chat about what you want. It **remembers preferences across conversations**, scans listings, **sets aside roles that hit your dealbreakers**, and delivers **a few curated matches each morning** (some days none). Each match has **reasons it fits**, a **"strong fit" / "stretch" label**, and **"what still needs checking"**. A **"kept" vs "set aside" view shows what was ruled out and why**. It drafts a tailored resume that you approve. Employers pay; candidates use it free. Source: tryelip.ai (fetched 2026-11-21). | This is the bar the reviewer will compare against. Ranker already covers memory, reasons and concerns. It is missing the **fit label**, the **"set aside and why"** transparency, and the "a few good matches, not a firehose" framing. |
| **Jobright.ai** | Resume-based match score built from skills, experience and industry. An "Orion" copilot explains fit (experience, seniority, skills, gaps, highlights). Ranked recommendations, auto-apply and resume tailoring. | Match explanation plus gaps is now **expected**. One reviewer estimated the score is "right ~70–80% of the time" (LOW confidence), and says the agent and auto-apply features are weaker than the marketing. |
| **LinkedIn AI job search** | Natural-language job search ("describe your ideal job") that surfaces roles without exact keyword matches. "Job match" insights compare your profile with each posting; the free vs Premium split has changed over time. | NL search plus a per-job fit summary is **table stakes at the top of the market**. |
| **Indeed Career Scout** (Sep 2025, mobile only) | Chat-based coach with an onboarding quiz, personalised recommendations from skills, commute, schedule, salary, chat context and behaviour, company insights, resume builder, application autofill, mock interviews. | It shows how far "career agent" scope can sprawl. Ranker should **not** copy the coaching, interview and resume-builder parts. |
| **HiringCafe** | Fast, clean, no-account search over company career pages crawled several times a day. Granular filters. AI filtering of **ghost jobs, scams and consultancies**. Links go straight to the employer's portal. | Users prize **freshness, no signup and no junk** more than they prize AI. Ranker's guest mode and expiry match this. Junk and consultancy filtering is a gap. |
| **Naukri (India)** | AI recommendations and alerts, AI resume maker and mock interviews behind Pro. G2 reviews complain about **irrelevant recommendations and alerts**. | Local users have low expectations. A noticeably relevant short list with honest reasons stands out. |

**Recurring user complaints across the category** (MEDIUM/LOW: press and review sites):
1. Irrelevant matches or alerts (Naukri, JobHire.AI).
2. Opaque or meaningless match percentages. In one report a score moved from 41% to 44% and the user quit.
3. Ghost and stale listings. Greenhouse estimates 18–22% of its jobs are ghost jobs in a given quarter.
4. Paywalls and surprise subscriptions.
5. Auto-apply spam that harms the candidate.

### Live check: Remotive API (HIGH confidence, fetched 2026-11-21)

- `GET https://remotive.com/api/remote-jobs` returned **`total-job-count: 16`**: 16 jobs in total, not thousands.
- About **half are not open to India**: `candidate_required_location` was "USA" or "USA, Canada" for 7 of 16. Only 9 of 16 were Worldwide, APAC or similar.
- The legal notice says jobs are **delayed 24h**, you must **link back and credit Remotive**, poll at most **4 times a day**, and "**displaying our jobs in order to collect signups/email addresses … constitutes a breach of our terms**".

**Feature implications:**
- (a) Remote jobs need a **location-eligibility field and filter**. Otherwise P1/P2 users in India will see US-only remote jobs at the top of their feed, which is the fastest way to lose trust.
- (b) Remotive jobs must stay **visible without signup**. Guest mode already allows this, so do not add a signup wall later.
- (c) The index will be **dominated by Adzuna India**. Whether ≥ 10k relevant jobs (ING-12) is reachable now depends almost entirely on Adzuna, plus HN after the MVP. Flag this to the stack and pitfalls work. Pulling ING-13 (Greenhouse/Lever boards) earlier may be needed.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Missing any of these makes the product feel broken or untrustworthy. "In SPEC?" maps each feature to SPEC IDs.

| Feature | Why Expected | Complexity | In SPEC? / Notes |
|---|---|---|---|
| **Instant search without signup** | HiringCafe, LinkedIn and Indeed all allow it. P3 persona. Remotive terms effectively require it. | LOW | Yes: UI-01, AUTH-01, US-01. Keep the result list free of auth walls. |
| **Natural-language query → applied filters, shown as editable chips** | LinkedIn ships NL search. Users must be able to **see and correct** what the system understood ("Remote · ≥ ₹10 LPA · Python"). | MEDIUM | Partly: SRCH-08 parses, but no UI requirement shows parsed filters back to the user. **Gap: add a filter-chip bar on search and feed.** Chips also make degraded mode honest, because a rule-parsed query is still visible and editable. |
| **Core hard filters**: location / remote, seniority incl. intern, employment type incl. internship, min salary, posted-within | Every job board has them. For P1, "internship" is a must. | LOW | Yes: SRCH-04. |
| **Remote eligibility (which countries can apply)** | A "remote" job restricted to the USA is irrelevant to an Indian user. Verified: about 44% of Remotive's sample is US/NA-only. | LOW–MEDIUM | **Missing from SPEC.** Add `remote_eligibility` (e.g. `worldwide \| india \| apac \| restricted \| unknown`) parsed from Remotive's `candidate_required_location` and from text for other sources. By default, hide `restricted` jobs that exclude India for India-based users. Changes NORM-02, SRCH-04 and the `jobs` schema. |
| **Job card essentials**: title, company, location/remote, **salary in INR/LPA when known**, posted age, source badge, link to original | US-10 and attribution terms. Salary is the first thing Indian seekers scan for. | LOW | Yes: UI-03, NORM-03. Show "Salary not listed" explicitly rather than hiding the field. For foreign-currency jobs show the original and the INR estimate ("$90k (~₹75 LPA)"). |
| **Freshness signal on every card** ("Posted 3d ago · checked 6h ago") | Ghost and stale jobs are the #1 trust complaint in the category. | LOW | Partly: `posted_at` and `last_seen_at` exist but no UI requirement shows them. **Gap: show both.** Also directly shows off G3 (freshness) to the reviewer. |
| **"Why this matches" on each result** | Jobright, LinkedIn job match and Elip all have it. It is Ranker's core value. | MEDIUM (LLM) / LOW (rule fallback) | Yes: RANK-03, §10.3. Rule-generated reasons in degraded mode keep this table stakes even when the LLM is down. |
| **Skill gaps / "what you're missing"** | Expected after Jobright and LinkedIn Premium. | LOW (from rerank output) | Yes: `missing_skills`, `concerns`. |
| **Resume upload → review and edit extracted profile** | Parsing errors are inevitable, and users must be able to fix them. | MEDIUM | Yes: PROF-01..03, UI-02. |
| **A no-resume path** (a short form or chat) | Many students do not have a polished PDF handy. Elip allows a resume or a chat. | LOW | Yes: PROF-05 plus the preferences step in UI-02. Make sure the form path is 4–5 fields (role, city/remote, seniority, employment type, min salary) and needs no chat. |
| **Save / dismiss and a saved list** | Universal. | LOW | Yes: UI-03, UI-06, RANK-08. |
| **Duplicates removed** | Seeing the same job 3 times (Adzuna re-posts, consultancies) instantly makes a feed look low quality. | MEDIUM | Yes: ING-10. The MVP's "simple dedupe" is acceptable, but test it on real Adzuna India data, which is dup-heavy (LOW confidence; verify in Phase 2). |
| **Per-company cap in the top results** | Without it, one big recruiter or consultancy floods the first page. | LOW | Yes: RANK-07. **The MVP makes MMR *and* the cap optional. Keep the cap in the MVP**: it takes about 15 lines and has high visible impact. Defer MMR. |
| **Helpful empty and weak-result states** ("0 results for ≥ ₹30 LPA intern roles; try removing the salary floor") | Over-filtered queries are common, and a blank page reads as broken. | LOW | Partly: AGENT-04 and the system prompt cover chat. UI §17 Phase 7 requires designed empty states. Add one-click "relax filter" chips. |
| **Clear, honest LLM-down behaviour** | Users forgive "AI explanations temporarily unavailable"; they do not forgive errors. | LOW | Yes: RANK-04, AGENT-08. |
| **Mobile-usable** | Students share links on WhatsApp and open them on phones. | MEDIUM | Yes: UI-09. The MVP needs "basic responsive" only. |
| **See and delete what's stored about me** | Privacy expectation, and SPEC's own US-07/US-08. | LOW | Yes: MEM-05, AUTH-04, but **the MVP UI list (§18) has no profile/preferences/memory page**. Without it there is no place to edit preferences, see memories or press "delete my data". **Gap: add a minimal profile page to the MVP.** |
| **Chat that applies stated preferences and confirms them** ("Got it: remote only, ≥ ₹12 LPA. Updated your feed.") | Elip's pitch is that it remembers and you can update it any time. | MEDIUM | Yes: AGENT-01, `update_preferences`, system prompt rule 3. |
| **Streaming chat responses with progress** | Waiting 5 s on a blank chat bubble feels broken. | MEDIUM | Yes: AGENT-02 (SSE, tool_status). |

### Differentiators (Competitive Advantage)

These line up with the Core Value (honest, well-ranked short list) and with what Elip screens for. Do not try to win on every row.

| Feature | Value Proposition | Complexity | In SPEC? / Notes |
|---|---|---|---|
| **Fit label instead of a fake-precise %** (Strong fit / Good fit / Stretch) | It mirrors Elip's own "strong fit / stretch" language, avoids the "41% vs 44%" problem, and maps directly onto rerank relevance 3/2/1. | LOW | **Not in SPEC.** Derive from `RerankItem.relevance` (and from blended score buckets in degraded mode). Very cheap and high signal to the reviewer. |
| **Match breakdown (feature contributions bar)** | Explainable ranking that most products cannot show. Proves the ranking is real and not just an LLM wrapper. | LOW–MEDIUM | Yes: UI-05, §10.3. **Not in the MVP list** (no job detail page). Cheap alternative for the MVP: an expandable "Why ranked here" panel on the card. |
| **"Set aside" transparency** ("43 jobs hidden by your dealbreakers: 30 US-only remote, 13 below ₹10 LPA. Show anyway") | Elip ships a "kept vs set aside" view. It builds trust and helps users notice over-strict filters. | LOW–MEDIUM | **Not in SPEC.** MVP version: count filtered-out candidates per filter reason in the retrieval SQL (one extra aggregate query) and show one line. The full "show set-aside jobs" list can come after the MVP. |
| **Grounded agent (never invents a job), with job cards streamed alongside text** | Hallucinated listings are the classic failure of LLM job bots. A provable "0 hallucinated IDs" is a strong portfolio claim. | MEDIUM | Yes: AGENT-03. Keep in the MVP (already there). |
| **Memory across sessions that the user can see and edit** | Elip's headline feature. Few competitors expose the memory itself. | MEDIUM–HIGH | Yes: MEM-01..06. See the over-scoping note below: the MVP can ship a thinner version. |
| **Skip with a reason** (chips: "Too senior", "Wrong location", "Not this company", "Not my stack", "Job closed") | One tap turns implicit feedback into **explicit, deterministic preference updates** (penalise company, adjust seniority) and catches dead listings. It is more useful early than Rocchio, which needs many interactions. | LOW–MEDIUM | **Not in SPEC** (skip has no reason). Store the reason on `interactions`. Map to `preference_patch` and memories. "Job closed" feeds a re-check or expiry. Recommend this as the **MVP's personalisation feature** in place of Rocchio. |
| **Public evaluation page / honest metrics** | No consumer job product publishes nDCG or ablations. This is aimed squarely at the hiring reviewer. | LOW (page) once evals exist | Yes: EVAL-08, UI-08. The MVP has the README ablation table, and the `/evals` page comes after the MVP. Fine. |
| **India-native normalisation** (LPA, ₹, Bangalore↔Bengaluru, NCR, internship/SDE-I seniority) | Global tools handle Indian salary text and seniority conventions badly. | MEDIUM | Yes: NORM-02..04. A quiet but real quality edge. |
| **"New since your last visit" marker in the feed** | A cheap retention hook while email digests are deferred. Supports G6 (real usage). | LOW | **Not in SPEC.** Compare `first_seen_at` with the user's `last_active_at`. |
| **Compare 2–3 jobs** | A nice agent demo. | MEDIUM | Yes: AGENT-06. Already post-MVP. Fine. |
| **Daily digest (email/Telegram)** | Elip's main delivery channel. Drives return visits. | MEDIUM–HIGH (email infra, deliverability, unsubscribe) | v2 (AGENT-09). Keep it deferred. Mention in the README as "what I'd do next". |
| **Consultancy / junk filtering** | HiringCafe's main promise. Adzuna India likely has many staffing-agency posts (LOW confidence; verify in Phase 2). | MEDIUM | **Not in SPEC.** Post-MVP: a curated `companies.is_staffing_agency` flag plus a ranking penalty, and an optional "hide recruiters/consultancies" filter. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---|---|---|---|
| **Auto-apply / mass apply** | Jobright, JobCopilot, LoopCV and others market it heavily. | Spams employers, damages candidates (the "AI doom loop" in Fortune 2025), needs ATS automation that breaks often, and brings legal and ToS risk. SPEC already excludes it. | Link to the original posting. Track `apply_click`. |
| **Resume rewriting / tailored resume generation / cover letters** | Elip, Jobright and Indeed all ship it, so it looks expected. | A whole second product (document generation, formatting, PDF export, review UX). Adds nothing to the matching and ranking story, and it would eat the MVP timeline. | Show `missing_skills` and `concerns` per job ("what to emphasise"). List tailored resumes under "what I'd do next". |
| **Precise match percentage ("87% match")** | Popular in Jobright and Naukri-style UIs. | False precision. Users read it as a promise and lose trust when it is wrong. It cannot be calibrated without outcome data. | Categorical fit label plus a feature-contribution breakdown. |
| **General career coaching, interview prep, salary negotiation chat** | Indeed Career Scout and others bundle them, and users will ask. | Unbounded scope, hard to evaluate, raises hallucination and liability risk (SPEC rule 7). Dilutes the agent eval. | The agent politely declines off-topic requests (already an eval category: "off-topic requests (2)") and steers back to search. |
| **Scraping LinkedIn/Naukri/Indeed to grow the index** | Index size is a real problem (Remotive returns only 16 jobs). | ToS and legal risk, brittle, and a bad look in a portfolio reviewed by a startup. | Adzuna plus HN, and pull **Greenhouse/Lever public boards (ING-13)** forward if the index is too small. |
| **Signup wall before results** | Common growth tactic. | Kills P3 conversion and the "20 real users" goal, and likely **breaches Remotive's terms** (no showing jobs to collect signups/emails). | Guest mode with "save your profile" prompts after value is delivered. |
| **Infinite scroll feed / "more is better"** | Feels like a job board. | Ranking quality only matters in the top 10–20. Long tails show weak matches and dilute the "honest short list" value (Elip sends "a few" a day on purpose). | A top 20 with a "load more" cursor, and explicit "results get weaker below here" messaging once blended scores fall below a threshold. |
| **Real-time push / WebSocket everything** | Feels modern. | Infra cost and complexity for no benefit at this scale. | SSE for chat only (already decided, D7). |
| **Password accounts in the MVP** | Feels like "real product". | argon2, CSRF, reset emails and brute-force limits take 1.5–2 days and are not needed to demo matching. | Guest-only MVP (already in §18). Accept that memory is per-browser and say so in the UI ("Your profile is saved in this browser"). |
| **Employer side / talent marketplace** | Elip's revenue model. | Different product. | Out of scope (already). |

---

## Feature Dependencies

```
Ingestion (Adzuna + Remotive) ──> Normalisation (location, remote_eligibility*, salary INR, seniority, skills)
        │                                   │
        │                                   ├──> Hard filters + filter chips* + "set aside" counts*
        │                                   └──> Job card essentials (salary display, freshness*)
        └──> Dedupe ──> Per-company cap

Embeddings ──> Hybrid retrieval ──> Feature ranking ──> LLM rerank ──> Reasons / missing skills / concerns
                                         │                   └──> Fit label* (from relevance)
                                         └──> Feature contributions ──> "Why ranked here" panel / match breakdown

Guest session ──> Profile (resume parse OR quick form) ──> Profile embedding ──> Personalised feed
                       └──> Preferences (structured) ──> Hard filters + f_pref penalties
                                    ▲
       Chat agent (update_preferences tool) ─┘
       Skip-with-reason* ──> preference_patch / memories ─┘
       Save/skip history ──> Rocchio feedback vector (post-MVP) ──> f_feedback

Agent loop ──requires──> search tool (= ranking pipeline) + grounding check + SSE
Memory extraction ──requires──> conversations/messages + user_memories ──> Profile/memory page* (view/delete)
"New since last visit"* ──requires──> users.last_active_at + jobs.first_seen_at
Fit label / reasons ──enhances──> Feed and chat job cards
Signup wall ──conflicts──> Remotive ToS + guest-first growth
Auto-apply / resume tailoring ──conflicts──> MVP timeline (separate product surface)

(* = not currently in SPEC, recommended addition)
```

### Dependency Notes

- **Remote eligibility needs normalisation before ranking:** it must be a column, not a ranking signal added afterwards, so that hard filters and "set aside" counts can use it. Add it in the Phase 2 normalisation work and the `jobs` migration. Retrofitting means a schema change and a re-normalise.
- **Fit label needs rerank, with a degraded fallback:** in degraded mode, derive the label from blended-score thresholds so the card layout never changes shape.
- **The profile/memory page is needed by memory extraction and by "delete my data":** if the MVP ships memory extraction (§18 says it does), it must ship a place to view and delete memories, or it contradicts US-07 and the privacy page.
- **Skip-with-reason feeds preferences:** it reuses the same `preference_patch` merge path as the agent's `update_preferences`. Build one merge function and call it from both.
- **Filter chips depend on query parsing (SRCH-08) and work with the rule fallback:** chips let the search box use the **rule parser first**, keeping US-01 sub-second, with LLM parsing as an enhancement. In chat, the agent's tool arguments already carry the parsed filters.
- **Per-company cap needs company normalisation (NORM-06):** without normalised company names the cap is easy to get around ("TCS" vs "Tata Consultancy Services").

---

## MVP Definition

Measured against SPEC §18. Verdict: **§18 is close to right, with three gaps and two items to slim down.**

### Launch With (MVP, ~10–12 days)

Keep from §18:
- [ ] Remotive + Adzuna ingest, normalisation, taxonomy skills, expiry, simple dedupe: there are no matches without an index.
- [ ] Hybrid search + baseline eval (10 personas): core value and the main portfolio proof.
- [ ] Resume parse → editable profile, plus the quick-form path: personalisation input.
- [ ] Feature ranking + LLM rerank + degraded mode: honest reasons are the core value.
- [ ] Agent with `search_jobs`, `get_job_details`, `update_preferences`, `save_job`, grounding check, 10 scenarios: Elip screens for tool use and structured output.
- [ ] Landing/search, onboarding, feed, chat, saved; guest mode; rate limits; budget cap; deploy, schedule, Sentry; README with ablation.

**Add** (all LOW complexity, together about 1 day):
- [ ] **Remote eligibility field + default filter**, so Indian users don't see US-only roles. Verified data problem.
- [ ] **Minimal profile page**: edit preferences, list and delete memories, "delete my data". Required by memory and privacy. Currently missing from §18's UI list.
- [ ] **Filter chips** showing the parsed query, editable, with "relax filter" on empty results.
- [ ] **Fit label** (Strong / Good / Stretch) and **freshness line** on cards.
- [ ] **Per-company cap in the top 10** (keep it; drop MMR).

**Slim down:**
- [ ] **Long-term memory:** ship extraction that writes `preference_patch` plus `add` ops only. Defer `update`/`delete` ops and `superseded_by` chains (MEM-04) until after the MVP. Users can delete wrong memories by hand on the profile page. This cuts the trickiest prompt and eval work.
- [ ] **Personalisation:** replace "Rocchio optional" with **skip-with-reason chips** that write deterministic preference patches. They are visible, testable and useful from the first interaction. Rocchio comes after the MVP, together with the "+ personalisation (simulated feedback)" ablation row.
- [ ] **Search-box query parsing:** rule parser plus chips by default. Make LLM parsing behind a flag optional so that US-01 (< 1 s) is not put at risk.

### Add After Validation (v1.x, post-MVP but still v1)

- [ ] Job detail page with full match breakdown (UI-05): when the first users ask "why is this ranked here?"
- [ ] "Set aside" counts, then a "show set-aside jobs" list: once filter chips exist.
- [ ] HN source with LLM extraction (ING-03): when the index is short of startup/remote-friendly roles.
- [ ] Greenhouse/Lever boards (ING-13, **consider pulling forward**): if Adzuna alone doesn't reach a useful India-relevant index (Remotive returns only 16 jobs today).
- [ ] Rocchio feedback + MMR (RANK-05, RANK-07 MMR half): once there are enough saves and skips to measure.
- [ ] Compare tool, `/evals` page, admin dashboard, full eval suites, judge calibration: as already listed in §18.
- [ ] Email+password accounts: when users ask for cross-device access or start losing guest data.
- [ ] Consultancy/staffing-agency penalty: if Adzuna feeds look agency-heavy.
- [ ] "New since last visit" marker: when retention is measured (EVAL-09).

### Future Consideration (v2+)

- [ ] Daily digest (email/Telegram), Elip's own channel: needs deliverability and unsubscribe work and real users first.
- [ ] Tailored resume suggestions: a separate product surface.
- [ ] Learned ranker (RANK-10), interleaving (EVAL-10): need interaction volume.
- [ ] Google OAuth.

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---|---|---|---|
| Hybrid search + hard filters | HIGH | MEDIUM | P1 |
| Remote eligibility filter | HIGH | LOW | P1 (new) |
| Job card essentials + freshness line | HIGH | LOW | P1 |
| Reasons / missing skills / concerns (+ rule fallback) | HIGH | MEDIUM | P1 |
| Fit label | MEDIUM | LOW | P1 (new) |
| Filter chips + relax-filter empty state | HIGH | LOW | P1 (new) |
| Resume parse + edit; quick-form path | HIGH | MEDIUM | P1 |
| Save/skip + saved list | MEDIUM | LOW | P1 |
| Per-company cap | MEDIUM | LOW | P1 |
| Chat agent (4 tools) + grounding + SSE | HIGH (portfolio) / MEDIUM (users) | HIGH | P1 |
| Minimal profile page (prefs, memories, delete data) | MEDIUM | LOW | P1 (gap) |
| Long-term memory (add-only + preference_patch) | MEDIUM | MEDIUM | P1 (slimmed) |
| Skip-with-reason → preference patch | MEDIUM | LOW | P1/P2 (new) |
| Match breakdown / job detail page | MEDIUM | MEDIUM | P2 |
| "Set aside" counts / list | MEDIUM | LOW–MEDIUM | P2 (new) |
| HN source | MEDIUM | MEDIUM | P2 |
| Rocchio + MMR | LOW–MEDIUM | MEDIUM | P2 |
| Compare tool | LOW | MEDIUM | P2 |
| Memory supersession (MEM-04) | LOW | MEDIUM | P2 |
| `/evals` page, admin dashboard | LOW (users) / HIGH (reviewer) | LOW–MEDIUM | P2 |
| Email+password accounts | MEDIUM | MEDIUM | P2 |
| Consultancy filter | MEDIUM | MEDIUM | P2 |
| Daily digest | MEDIUM | HIGH | P3 |
| Resume tailoring, auto-apply, coaching | (perceived) HIGH | HIGH | Anti / P3 |

---

## Competitor Feature Analysis

| Feature | Elip | Jobright | LinkedIn | HiringCafe | Our Approach |
|---|---|---|---|---|---|
| Input | Resume + chat | Resume | Profile + NL search | Search + filters | Resume, quick form, or chat |
| Delivery | A few matches each morning | Feed | Search + alerts | Search | Ranked short list (feed/search) + chat; digest in v2 |
| Fit expression | "Strong fit" / "stretch" + reasons + "needs checking" | Match % + Orion explanation | Job match insights | None | Fit label + reasons + missing skills + concerns + feature breakdown |
| Dealbreakers | Sets aside, shows why | Filters | Filters | Rich filters | Hard filters + chips; "set aside" counts (P2) |
| Memory | Remembers across conversations, updatable | Profile prefs | Profile | None | Structured prefs + visible, deletable memories |
| Freshness / junk | n/a | n/a | n/a | Crawls several times a day, filters ghost/scam/consultancy | Expiry after 2 missed runs, freshness line, dedupe; agency filter P2 |
| Apply | Tailored resume, intros | Auto-apply | Easy Apply | Employer portal link | Link to original only (anti: auto-apply) |
| Transparency of ranking | Partial | Score | Opaque | n/a | **Public eval numbers + ablation**: the unique angle |

---

## Sources

- Elip product page: https://tryelip.ai (fetched 2026-11-21). MEDIUM, first-party.
- Elip profile: https://yespress.io/elip-ai-yc-w25 (search snippet only; fetch returned 403). LOW.
- Jobright match and Orion: https://jobright.ai/ai-job-match, https://jobright.ai/orion-copilot. MEDIUM (vendor). Independent review: https://zplatform.ai/ai-reviews/jobright-ai/. LOW. The Register: https://www.theregister.com/2025/06/24/ai_may_take_jobs_but/
- LinkedIn AI job search: https://www.fastcompany.com/91467930/linkedin-ai-powered-job-search, https://www.engadget.com/social-media/linkedin-wants-you-to-tell-its-ai-about-your-dream-job-133046457.html, https://www.linkedin.com/help/linkedin/answer/a1655947. MEDIUM.
- Indeed Career Scout: https://www.indeed.com/careerscout, https://www.indeed.com/news/releases/indeed-introduces-new-suite-of-hiring-products-career-scout-talent-scout-premium-sponsored-jobs-and-indeed-connect. MEDIUM.
- HiringCafe: https://careerservices.smumn.edu/resources/hiringcafe-job-search-board/, https://app.dealroom.co/companies/hiringcafe, https://thenextweb.com/news/hiringcafe-6-8m-pre-seed-ai-talent-agent. LOW–MEDIUM.
- Naukri: https://www.g2.com/products/naukri-com/reviews, https://yourstory.com/2026/07/naukri-launches-ai-recruitment-platform-india. LOW–MEDIUM.
- Category complaints: https://www.boston.com/community/jobs/2025/11/07/ai-hiring-job-hunt/, https://fortune.com/2025/11/18/hiring-job-seekers-recruiters-talent-acquisition-ai-doom-loop-application-technology/, https://www.adzuna.co.uk/blog/jobhire-ai-review-2025/. LOW–MEDIUM.
- Remotive API live response (job count, `candidate_required_location`, legal notice): `GET https://remotive.com/api/remote-jobs`, 2026-11-21. HIGH.

---
*Feature research for: conversational AI job matching / career agent*
*Researched: 2026-11-21*
