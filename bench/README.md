# Search benchmarks (spikes S1 and S3)

Measures retrieval at about 50k rows on the production VM (Azure Standard_B2ls_v2: 2 vCPU,
4 GB, burstable) and feeds ADR 0004.

- **S1** clones the real jobs x25 into a separate database `ranker_bench` and compares exact vs
  HNSW vector search with 0, 1 and 3 filters (`EXPLAIN (ANALYZE, BUFFERS)`).
- **S3** runs k6 at 20 rps for 5 minutes against one uvicorn worker on `ranker_bench`.

## Safety model

| Rule | How it is enforced |
|---|---|
| Never touch the live `jobs` table | Everything runs in the database `ranker_bench` on the same Postgres server. `bench_clone` and `bench_explain` refuse any database whose name does not end in `_bench`. |
| Source is read-only | `bench_clone` opens the production database in a `READ ONLY` transaction. `bench_explain` runs every transaction `READ ONLY`. |
| Live traffic never reaches the bench api | Separate compose project `ranker-bench`; the service is named `bench-api` (not `api`) so Caddy's `api:8000` cannot resolve to it; published on `127.0.0.1:8100` only. |
| No secrets leak into the bench container | No `env_file`. It gets only the DB credentials and session secret via interpolation, an empty Anthropic/OpenRouter key and `LLM_PROVIDER=fake`. |
| Clean teardown | `down` the project, `bench_clone --drop` the database, delete `bench/config`. Nothing else is created. |
| No host installs | Python tools run in the api image already on the box; k6 runs from the pinned `grafana/k6` image. |

Run off-peak. The clone and the HNSW build compete with the live database for 2 vCPU, and the
5-minute k6 run drains burst credits (RESEARCH Pitfall 5). Prerequisites: at least 3 GB free
disk (`df -h /var/lib/docker`), at least 1.5 GB free RAM (`free -m`), and the live stack up
(`docker compose -f compose.prod.yaml ps`).

Cloning detail: every real active canonical job is copied `--factor` times with a new
`source_job_id` (`<id>~b<k>`), the real description text, and its embedding plus isotropic
Gaussian noise of L2 norm `--jitter` (0.02 keeps cosine near 0.9998; try 0.3 for about 0.96),
re-normalised. The HNSW index is dropped before the load and rebuilt after it (much faster than
incremental inserts), then `ANALYZE` runs.

## Commands for the VM, in order

All commands run as the deploy user. Replace `HOST`.

### 0. Copy the bench directory (from your workstation)

```bash
scp -r bench deploy@HOST:/srv/ranker/
```

### 1. Record the starting CPU credit balance

Azure portal, VM, Metrics, "CPU Credits Remaining" (aggregation Avg, 1 minute). CLI equivalent:

```bash
az monitor metrics list --resource <vm-resource-id> --metric "CPU Credits Remaining" \
  --interval PT1M --aggregation Average --offset 15m -o table
```

Write the number down (credits before). Also run `free -m` and `df -h /var/lib/docker`.

### 2. S1: clone real jobs x25 into `ranker_bench`

```bash
cd /srv/ranker
docker compose -f compose.prod.yaml run --rm --no-deps -T api sh -c \
  'python -m ranker.spike.bench_clone --target-db ranker_bench --factor 25 --jitter 0.02 --seed 7' \
  | tail -n1
```

The last line is a JSON summary (rows, db_size, load_seconds). `rows` should be about 25 times the
active canonical job count (about 46k for 1,838 jobs). Add `--reset` to reload an existing bench
database. The source database is the container's `DATABASE_URL` (production), read-only.

### 3. S1: exact vs HNSW EXPLAIN matrix

```bash
cd /srv/ranker
docker compose -f compose.prod.yaml run --rm --no-deps -T api sh -c \
  'python -m ranker.spike.bench_explain --dsn "${DATABASE_URL%/*}/ranker_bench" --runs 30' \
  > /tmp/bench_explain.json
cat /tmp/bench_explain.json
```

Modes: `exact`, `hnsw` (ef_search 200, iterative relaxed_order, planner's choice) and
`hnsw+index` (same, with `enable_seqscan` off so the index is used). Filter sets: `none` (only the
default India geo clause), `city` (Bengaluru + remote ok), `city+salary+seniority`. For each, the
JSON has `lex`, `vec`, `hybrid` and `set_aside` legs (p50/p95/max ms, rows returned, top plan node,
scan nodes, buffers) and `full` (hybrid plus set-aside, as the API runs it). Paste the whole file
into the plan summary. Pass `--queries-file` to use a different query list (default: 5 built-in
queries cycled).

### 4. S3: prepare the bench api

```bash
cd /srv/ranker
# Ranking config copy, so vector_mode can change without touching production.
IMG=$(docker compose -f compose.prod.yaml config --images | grep ranker-api | head -n1)
mkdir -p bench/config
cid=$(docker create "$IMG")
docker cp "$cid:/app/config/." bench/config/
docker rm "$cid"
chmod -R a+rX bench/config
grep vector_mode bench/config/ranking.yaml        # exact (current default)

cd bench
docker compose -p ranker-bench --env-file ../.env -f compose.bench.yaml up -d --wait
curl -fsS "http://127.0.0.1:8100/api/jobs/search?q=backend%20python%20bengaluru" | head -c 300; echo
```

If `up` fails with a network error, run `docker network ls` and retry with
`PROD_NETWORK=<name of the live stack default network>` prefixed to the command.

### 5. S3: k6, 20 rps for 5 minutes, 1 worker

```bash
cd /srv/ranker
K6=grafana/k6:1.4.2@sha256:3656673de3f30424e8ebcfa46acd9558d83b6a43612d0f668ffeac953950c6c7
docker run --rm --network host -v "$PWD/bench:/bench:ro" \
  -e BASE_URL=http://127.0.0.1:8100 -e CACHE=cold \
  "$K6" run /bench/search.js | tee /tmp/k6_exact_cold.txt
```

Thresholds are in the script (`p(95)<300`, `http_req_failed rate<0.01`); k6 exits non-zero if
one fails. `CACHE=cold` (default) appends a random word to every query so the query-embedding
cache never hits (worst case, each request runs fastembed). Optionally repeat with `-e CACHE=warm`
for the repeat-query case. Record p50/p95/p99, failures, `search_degraded_responses` and
`search_empty_results`. During the run, in another shell:

```bash
docker stats --no-stream
```

### 6. Record the ending CPU credit balance

Same metric as step 1, immediately after the run (credits after). If the balance reached 0 the
VM throttled to its 30% baseline; say so, and report the sustainable rate at base CPU honestly.

### 7. Optional: repeat S3 with HNSW

Only if step 3 says HNSW wins somewhere. Wait for credits to recover first.

```bash
cd /srv/ranker/bench
sed -i 's/^\(  vector_mode:\) *[a-z]*/\1 hnsw/' config/ranking.yaml
grep vector_mode config/ranking.yaml
docker compose -p ranker-bench --env-file ../.env -f compose.bench.yaml up -d --force-recreate --wait
# then repeat step 5 with the output file named k6_hnsw_cold.txt
```

### 8. Teardown (always)

```bash
cd /srv/ranker/bench
docker compose -p ranker-bench -f compose.bench.yaml down
cd /srv/ranker
docker compose -f compose.prod.yaml run --rm --no-deps -T api sh -c \
  'python -m ranker.spike.bench_clone --target-db ranker_bench --drop'
rm -rf bench/config
docker compose -f compose.prod.yaml ps          # live stack still healthy
curl -fsS https://<domain>/api/readyz
```

`--drop` runs `DROP DATABASE ranker_bench WITH (FORCE)` and prints `{"dropped": true, ...}`. The
production `ranker` database is not touched by any step.

## Local check (no VM)

```bash
cd backend
uv run pytest tests/test_bench_clone.py -s     # clones a seeded database, runs the matrix, drops it
```
