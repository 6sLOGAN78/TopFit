# ADR 0000: Hosting, domain and memory budget

- **Status:** Accepted
- **Date:** 2026-11-22
- **Phase:** 1 (Foundations & Walking-Skeleton Deploy)

## Context

Ranker must be live, cheap (a student budget, ideally $0 for infrastructure) and fast for users in India. One machine has to hold Postgres 18 + pgvector, the FastAPI API with an in-process ONNX embedding model, a worker process, the Next.js web app and a TLS proxy. The planned host was a DigitalOcean droplet in Bangalore, with GCP, Oracle and Hetzner as fallbacks (01-CONTEXT D-02). The planned domain was a free GitHub Student Pack domain, with a free DuckDNS name as the fallback (D-03).

## Decision

| Item | Choice |
|---|---|
| Provider | **Microsoft Azure, "Azure for Students" subscription** ($100 credit, no card) |
| Region | Central India (Pune). The subscription policy only allows eastasia, koreacentral, centralindia, uaenorth and indonesiacentral |
| VM size | **Standard_B2ls_v2**: 2 vCPU, 4 GB RAM (3.9 GB usable), burstable. B2als_v2 and B2as_v2 are `NotAvailableForSubscription` on student subscriptions |
| OS / arch | Ubuntu 24.04.5 LTS, x86_64, 30 GB Standard SSD (6.2 GB used after first deploy) |
| Network | Static public IP 52.140.122.107. The Azure NSG allows 22, 80 and 443 only; ufw on the host allows the same |
| Domain | **`topfit.duckdns.org`** (DuckDNS, free). The hostname is one variable (`PUBLIC_HOSTNAME`), so moving to a registrable domain is a config change plus a redeploy |
| TLS | Caddy automatic HTTPS. The certificate is issued by Let's Encrypt (YE1) and valid until 2027-02-19; renewal is automatic |
| Topology | Single origin (D-04): Caddy serves `/` from Next.js (standalone, on the same VM) and `/api/*` from FastAPI. No Vercel (D-05), so there are no cross-site cookies and no CORS |
| Setup | `deploy/bootstrap.sh` only (provider-agnostic): `deploy` user with key-only SSH, ufw, unattended upgrades, Docker 29.10, 2 GB swap, `/srv/ranker/.env` (mode 600) |
| Deploys | Push to `main` → CI → GHCR images (`ghcr.io/6slogan78/ranker-{api,web}`, public) → SSH deploy → `alembic upgrade head` → `up --wait` → Caddy reload → smoke test |

Why Azure: the Student Pack/DigitalOcean path was not set up, and GCP needed a card for its free trial. Azure for Students was already active on the owner's college account, needs no card, and has an Indian region.

## Measurements (production host, 2026-11-22)

Measured with `deploy/measure-memory.sh all` on the VM, image tag `sha-919836a`.

### Memory after startup

| Service | `docker stats` | cgroup `memory.peak` | Process RSS |
|---|---|---|---|
| api (uvicorn, 1 worker, model loaded) | 277.7 MiB | 292.7 MB | 334.4 MB |
| web (Next.js standalone) | 52.1 MiB | 91.7 MB | 102.1 MB |
| worker (stub) | 31.1 MiB | 31.4 MB | 46.6 MB |
| db (Postgres 18 + pgvector 0.8.7) | 35.7 MiB | 103.0 MB | ~30.8 MB main + background workers |
| caddy | 15.1 MiB | 23.1 MB | 50.0 MB |

### Memory after 100 embeddings

100 `/api/readyz` calls, each computing one 384-dim embedding:

| Service | `docker stats` |
|---|---|
| api | 277.8 MiB (flat: no growth from embedding) |
| web | 52.1 MiB |
| worker | 31.1 MiB |
| db | 36.0 MiB |
| caddy | 15.6 MiB |

Host after 100 embeddings: 1,128 MB used, 2,777 MB available, swap 0 of 2,047 MB used.

**Conclusion:** the whole stack uses about 0.45 GB of container memory and about 1.1 GB on the host. That leaves about 2.7 GB of headroom on 4 GB for the Phase 2+ index (Postgres shared buffers and HNSW/btree indexes), a real scheduler in the worker, and the LLM client. A 2 GB VM would fit today but would leave too little room for Postgres caching once the index holds tens of thousands of jobs, so 4 GB stays.

### Cold start

API container recreated, timed from start until ready:

- `/api/healthz` responds after **3.44 s**
- `/api/readyz` (DB, pgvector version, model loaded from the baked cache, test embedding) passes after **3.50 s**

The model is baked into the image (`FASTEMBED_CACHE_PATH`, `HF_HUB_OFFLINE=1`), so startup never downloads anything.

### Images

| Image | Size |
|---|---|
| `ghcr.io/6slogan78/ranker-api:sha-919836a` (includes the ~67 MB ONNX model) | 729 MB |
| `ghcr.io/6slogan78/ranker-web:sha-919836a` | 417 MB |

### Verification

- `deploy/smoke.sh topfit.duckdns.org`: readyz ok, pgvector 0.8.7 (≥ 0.8.3), embedding dim 384, cookie round-trip, HTTP→HTTPS 308, `/` returns 200. All PASS.
- From the internet, ports 5432, 8000 and 3000 are closed; only Caddy publishes ports.

## Consequences

- **Credit runway:** B2ls_v2, plus the disk and the static IP, costs about $30–35/month, so the $100 credit lasts about 3 months (to about late February 2027). Azure for Students disables resources when the credit runs out instead of billing. Before then: renew the student offer (yearly), move to a smaller size if Phase 2–5 measurements allow, or migrate. Migration needs only `bootstrap.sh`, a `pg_dump`, and new deploy secrets.
- **The DuckDNS hostname is blocked on at least one Indian ISP.** From the owner's first network, every `*.duckdns.org` name got TLS resets and HTTP 503s from the ISP; the same site works from another network and from GitHub's runners. `sslip.io` names were not blocked. Some real users will be on blocking ISPs, so **switch to a registrable domain (GitHub Student Pack `.me`/`.tech`) before the Phase 5 MVP launch.**
- The `ranker_deploy` key is passphrase-less and lives in GitHub Actions secrets and on the owner's laptop. It can only log in as `deploy` on this VM. Revoke it by editing `/home/deploy/.ssh/authorized_keys`.
- Root SSH is `prohibit-password` (key only). Follow-up: set `PermitRootLogin no`. Login on Azure is through `azureuser` with sudo.
- Burstable B-series CPU: sustained load uses CPU credits. Measure again during the Phase 3 k6 load test (20 rps target).
- `/api/readyz` computes an embedding on every call. That's cheap (milliseconds), but it's public and unthrottled until rate limits arrive (SEC-04, Phase 3).
