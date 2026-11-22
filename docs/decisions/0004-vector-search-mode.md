# ADR 0004: Vector search mode and the search latency SLO at ~46k jobs

Status: accepted (Phase 3, plan 03-12). Date: 2026-11-23. The SLO miss recorded here is an open gap, not a closed item.

## Context

SRCH-03 asks for a recorded exact-versus-HNSW decision and SRCH-06 asks for search p95 < 300 ms at 20 rps without rerank. The background (STACK.md verdict #1):

- pgvector's default `hnsw.ef_search` is 40, so an HNSW scan returns at most 40 rows whatever the `LIMIT`. `apply_vector_mode("hnsw")` therefore sets `hnsw.ef_search = 200` and iterative scan (`relaxed_order`).
- A CTE referenced twice is materialised by Postgres, so the vector leg must not read from a shared `base` CTE if it is to use the index.
- Evaluation always runs `exact` (D-16), so the choice only affects production latency, not the eval numbers.

Today's index is small (1,838 active jobs), so the question is what happens at the 10k-50k rows the project is sized for. We measured it on the production VM.

## Method

- **Host:** the production VM, Azure B2ls_v2 (2 vCPU burstable, 4 GB). The benchmark ran next to the live stack (prod api, worker, web, caddy and db were up) on 2026-11-22 between about 18:50 and 19:30 UTC.
- **Data:** database `ranker_bench`, 1,838 real jobs cloned x25 = 45,950 rows with jittered embeddings (jitter 0.02, seed 7), 919 MB, loaded in 108 s (`ranker.spike.bench_clone`). The first load failed with `DiskFull: could not resize shared memory segment ... No space left on device` during the parallel HNSW build, because Docker's default `/dev/shm` for the db container is 64 MB. The rerun used `PGOPTIONS='-c max_parallel_maintenance_workers=0 -c maintenance_work_mem=256MB'`.
- **S1, EXPLAIN matrix** (`ranker.spike.bench_explain`): 30 runs x 5 queries per cell. Modes: `exact`, `hnsw` (ef_search 200 + iterative scan), `hnsw+index` (the same with `enable_seqscan` off, to see what the planner does when forced). Filter sets: none, city, city + salary + seniority. Each leg (lex, vec, hybrid, set_aside) and the full search is timed.
- **S3, k6:** 20 rps for 5 minutes, cold cache, 1 uvicorn worker, rate limit off, LLM fallback off, against a bench api container pointed at `ranker_bench`.
- **CPU credits:** the B2ls_v2 credit balance before and after was **not captured** (the owner did not provide readings). The VM is burstable, so part of the slowness may be credit throttling; we cannot separate that from real query cost. The k6 run saturated both cores (db container 193% CPU), so a credit-limited baseline would only make it worse.
- Raw outputs are in `.planning/phases/03-hybrid-search-eval-baseline/bench/` (`explain.json`, `clone.json`, `k6_exact_cold.txt`, `stats_pre.txt`, `stats_post.txt`).

## Results

### S1: full search latency at 45,950 rows (ms, p50 / p95)

| mode | no filter | city | city + salary + seniority |
|---|---|---|---|
| exact | 1395 / 2799 | 1064 / 2058 | 987 / 1753 |
| hnsw | 1422 / 2751 | 1127 / 2101 | 1025 / 1912 |
| hnsw+index (seqscan off) | 1417 / 2748 | 1050 / 2059 | 988 / 1855 |

No mode is meaningfully faster than another. The difference between modes is inside the run-to-run noise.

### S1: where the time goes (exact mode, ms, p50 / p95)

| filters | lex | vec | hybrid (lex+vec+fuse) | set_aside | full search |
|---|---|---|---|---|---|
| none | 416 / 996 | 214 / 360 | 863 / 1980 | 800 / 2054 | 1395 / 2799 |
| city | 132 / 262 | 62 / 121 | 216 / 346 | 797 / 1582 | 1064 / 2058 |
| city + salary + seniority | 62 / 121 | 42 / 43 | 121 / 195 | 896 / 1535 | 987 / 1753 |

For any filtered search, the set-aside/relax count query takes roughly 800-900 ms at p50, which is 75-90% of the full search. Retrieval plus fusion alone is 121-216 ms p50 (195-346 ms p95) with filters. Without filters retrieval is 863 ms p50. The guest default geo filter is always active, so the set-aside query ran on every search.

### S1: query plans

- The vector leg is a **Seq Scan on jobs in every mode**. HNSW (`jobs_embedding_hnsw`) is never chosen, even in `hnsw` mode. With `enable_seqscan` off the planner switches to a bitmap scan on `jobs_source_scope_active`, still not HNSW. This confirms that `ORDER BY dist, j.id` (the `j.id` tie-breaker) stops Postgres from using the index for ordering: HNSW can only satisfy `ORDER BY embedding <=> q` with nothing after it.
- The lexical leg is a **Seq Scan on jobs** in every mode, including `hnsw+index` where seqscan is disabled. The GIN indexes (`jobs_tsv_gin`, `jobs_skills_tsv_gin`) are used only inside the hybrid and set-aside statements when seqscan is forced off, and then it does not help (hybrid is still 559 ms p50 unfiltered).
- The set-aside query seq-scans `jobs` twice per run plus a bitmap heap scan; its cost is independent of filter selectivity.

### S3: k6, 20 rps for 5 minutes, exact mode, 1 worker, cold cache

| metric | value |
|---|---|
| http_req_duration p95 | 51.68 s |
| http_req_duration p99 | 56.12 s |
| median | 30.04 s |
| http_req_failed | 71.02% (635 of 894) |
| requests completed | 894 (2.7 req/s achieved) |
| dropped iterations | 5,094 |
| db container CPU | 193% (both cores saturated) |
| db memory | 472 MiB to 550 MiB |
| bench api memory | 323 MiB |
| host memory | fine, no swap |
| CPU credit balance before/after | not captured |

Every k6 threshold failed. The server ran about 2.7 requests per second against an offered 20, so the queue grew until requests timed out. One search costs on the order of 1 second of CPU at this size, so two cores cap throughput at roughly 2 rps, which matches the observation.

## Decision

1. **`vector_mode` stays `exact`.** HNSW gives no benefit while the tie-breaker blocks index ordering, and there is no latency to win back from it in any measured cell. The config is unchanged.
2. **The SLO is not met at ~46k rows on this VM.** Measured p95 for one unloaded search is 1.7-2.8 s and the k6 run collapsed at 20 rps (p95 51.7 s, 71% failed). SRCH-06 at production-size data is **not achieved**; it remains an open gap.
3. **At today's 1,838 rows production is fine.** The prod search for "backend python bengaluru 10 LPA remote ok" returned 20 items, not degraded. As a rough extrapolation (not a measurement) of the roughly linear sequential-scan cost, a filtered search at 1.8k rows would take tens of milliseconds, and at about 5k rows would land near 200-250 ms p95. Latency should be measured in production (Prometheus histogram) rather than trusted from this extrapolation.
4. **Set-aside is now conditional (done in this plan).** `set_aside_when_results_below: 20` in `ranking.yaml`. The set-aside/relax query runs only when fewer than 20 candidates were fused, because the counts exist to explain a short list. A full pool (20 or more) returns `set_aside: []` and `relax: []`. Setting the key to 0 restores the old always-on behaviour. This removes about 800 ms of the p50 for typical full-page searches. It has **not been re-benchmarked on the VM**: by arithmetic from the table above, the filtered p50 would fall to roughly 120-220 ms and p95 to roughly 200-350 ms at 46k rows, which is still borderline against 300 ms and still far from 20 rps on two burstable cores (about 5-8 req/s at best). Treat these as expectations to verify.
5. **Product consequence of 4.** When a page is full, the UI no longer shows "N jobs set aside by city" and relax suggestions. The geo set-aside note in particular disappears for broad queries. Short or empty result lists, which is where those hints matter, are unaffected.
6. **`shm_size: 256mb` on the prod db service** (`deploy/compose.prod.yaml`) so parallel index builds and parallel query workers do not hit the 64 MB `/dev/shm` default. It takes effect on the next db container recreate; this plan did not touch the server.

## Follow-ups for a gap-closure plan (ranked by expected gain)

1. **Done here:** conditional set-aside (about 800 ms p50 saved on full pages). Verify with a re-bench.
2. **Drop `j.id` from the inner vector `ORDER BY`** so HNSW can order, with the tie-break in the outer query (rank ties on distance are rare with float embeddings but must stay deterministic for eval, so order by `dist, id` outside the limit). Then re-run S1: HNSW should win the vec leg clearly (214 ms exact unfiltered). Filtered HNSW needs the iterative scan, and the choice between modes should be made on nDCG/Recall@50 as well as latency.
3. **Make the lexical leg use the GIN index:** a `tsv @@ query` filter before ranking, with a `LIMIT`, so the planner uses `jobs_tsv_gin` instead of ranking all rows (lex is 416 ms p50 unfiltered, the largest leg after set-aside).
4. **Apply `shm_size`** by recreating the db container at the next deploy window (config change made here).
5. **Trigger to act:** start the SQL rewrites (2 and 3) before the active index passes **5,000 jobs**, or sooner if the production search p95 exceeds 300 ms for an hour, whichever comes first. The 20 rps target needs the rewrites even at that size; on 2 burstable vCPUs the exact-scan design is the bottleneck, not the VM.
6. Re-run S3 with CPU credit readings before and after, and with prod services idle, so credit throttling can be separated from query cost.

## Consequences

- SRCH-06 (p95 < 300 ms at production index size) and the 20 rps target are open phase gaps for verify-work. SRCH-03 is satisfied in that the benchmark is recorded on production-size data with the decision stated.
- Search stays on exact scan. Eval numbers are unaffected by this change because the set-aside result is not read by the eval harness.
- Because `ranking.yaml` changed, `pipeline_version` changes for new `search_logs` rows.
