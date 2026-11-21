---
phase: 01-foundations-walking-skeleton-deploy
plan: 07
subsystem: infra
tags: [deploy, azure, duckdns, caddy, adr, measurements]
requires: [01-06]
provides:
  - "Live walking skeleton at https://topfit.duckdns.org (Azure Central India VM, Caddy auto-HTTPS, single origin)"
  - "docs/decisions/0000-hosting-and-memory.md: hosting choice, RSS after startup and after 100 embeddings, cold start, image sizes"
  - "README live link and Deploy section"
affects: [02-live-jobs-index]
key-files:
  created: [docs/decisions/0000-hosting-and-memory.md]
  modified: [README.md, deploy/bootstrap.sh, web/src/components/status-panel.tsx]
decisions:
  - "Provider: Azure for Students, Central India, Standard_B2ls_v2 (2 vCPU/4 GB) instead of DigitalOcean BLR1; no card needed, subscription policy restricts regions and blocks B2als_v2/B2as_v2"
  - "Domain: topfit.duckdns.org (owner's existing DuckDNS name on the 6sLOGAN78 account); the topfit-jobs name created on another account is unused"
  - "GHCR owner is 6slogan78 (repo 6sLOGAN78/TopFit), not ayushmaurya-tech; bootstrap.sh default fixed"
  - "VM created with az CLI in Azure Cloud Shell (portal form is an iframe); NSG allows 22/80/443 only"
metrics:
  completed: 2026-11-22
---

# Phase 1 Plan 07: Live deploy, measurements and hosting ADR Summary

The walking skeleton is live at https://topfit.duckdns.org: Caddy (Let's Encrypt, valid to 2027-02-19) serves Next.js on `/` and FastAPI on `/api/*` from one Azure VM in Central India, with Postgres 18 + pgvector 0.8.7, the baked embedding model and a stub worker. Every push to `main` deploys through CI. Three consecutive push-to-deploy runs succeeded (44950000973, 44951854473, 44952204254).

## Task 1: owner setup (checkpoint)
- DuckDNS `topfit.duckdns.org` → 52.140.122.107 (verified at all 9 DuckDNS nameservers, 1.1.1.1 and 8.8.8.8).
- Azure VM `topfit-vm` (resource group `topfit-rg`), Ubuntu 24.04.5, static IP, NSG 22/80/443. Created via `az vm create` in Cloud Shell with the owner's approval of the settings.
- `deploy/bootstrap.sh` run over SSH as `azureuser` with sudo: deploy user, ufw, Docker 29.10, 2 GB swap, `/srv/ranker/.env` (mode 600).
- CI deploy key `~/.ssh/ranker_deploy` created. Secrets DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS and variable PUBLIC_HOSTNAME set on 6sLOGAN78/TopFit.
- The owner made the GHCR packages ranker-api and ranker-web public (anonymous pull returns 200).

## Task 2: deploy verification, measurements, ADR
- `deploy/smoke.sh topfit.duckdns.org`: all PASS (readyz ok, pgvector 0.8.7, dim 384, cookie round-trip, HTTP→HTTPS 308, `/` 200).
- Ports 5432/8000/3000 closed from the internet.
- Memory (docker stats): api 278 MiB, web 52 MiB, worker 31 MiB, db 36 MiB, caddy 15 MiB. Flat after 100 embeddings. Host 1.1 GB used of 3.9 GB, swap unused.
- Cold start: healthz 3.44 s, readyz 3.50 s. Images: api 729 MB, web 417 MB.
- The ADR records the provider, the credit runway (~3 months), the DuckDNS ISP block and follow-ups. The README has the live URL and the Deploy section. Commit 4bf2822.

## Task 3: browser check (checkpoint)
The owner approved all five rows on the live page, the Re-run, the cookie flags, the HTTPS redirect and `/api/docs`.

## Deviations from Plan
- **[Rule 1 - Bug] Cookie round-trip false failure** (`web/src/components/status-panel.tsx`): the check required the first ping to carry no cookie, so any reload or Re-run within the cookie's 1 h lifetime showed ✗. It now passes when the second ping returns the cookie set by the first. Commit 4d2fcec, deployed and re-verified.
- **[Rule 1 - Bug] Wrong GHCR owner default** in `deploy/bootstrap.sh` (`ayushmaurya-tech` → `6slogan78`). Commit 919836a.
- Provider and domain differ from the plan's primary path (Azure + DuckDNS instead of DigitalOcean + Student Pack domain). Both are allowed by D-02/D-03 (provider-agnostic deploy, DDNS fallback). Recorded in the ADR.
- The plan's Task 1 instructions assumed repo `AyushMaurya-tech/ranker`. The actual repo is `6sLOGAN78/TopFit`.

## Issues / follow-ups
- One of the owner's ISPs blocks `*.duckdns.org` (TLS reset and HTTP 503 from the ISP). The site works from other networks and from GitHub runners. Move to a registrable domain before the Phase 5 launch.
- The Azure credit ($100) lasts about 3 months at ~$30–35/month.
- Set `PermitRootLogin no` (currently `prohibit-password`).

## Self-Check: PASSED
- docs/decisions/0000-hosting-and-memory.md exists and contains "after startup", "after 100 embeddings", "cold start", api MB values and the pgvector version.
- README has no `LIVE_URL` placeholder; the live URL is present.
- Latest main run (44952204254) concluded success including the deploy job; smoke.sh passes against production.
