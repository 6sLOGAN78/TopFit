"""Spike S1: exact vs HNSW retrieval timings and plans on the ``*_bench`` database.

For each vector mode (exact, hnsw) and filter set (none, city, city+salary+seniority) this times
the lexical leg, the vector leg, the fused hybrid statement and the set-aside statement, then the
full path (hybrid + set-aside in one transaction, as the API runs it). Every statement is run
``--runs`` times after warm-up; ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` is captured once per
statement. Output is one JSON document on stdout.

Safety: only ``*_bench`` databases are accepted and every transaction is READ ONLY.
"""

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from ranker.embeddings.embedder import Embedder, build_embedder
from ranker.embeddings.service import vector_literal
from ranker.search.config import ranking_config
from ranker.search.models import SearchFilters
from ranker.search.parse import parse_rules
from ranker.search.sql import (
    Legs,
    apply_vector_mode,
    build_hybrid_sql,
    build_set_aside_sql,
    compile_filters,
    skill_tokens,
)
from ranker.settings import get_settings
from ranker.spike.bench_clone import assert_bench_database, sa_dsn

# "hnsw+index" is hnsw with enable_seqscan off: what the HNSW index costs when the planner is
# made to use it (the planner may otherwise pick a seq scan at small sizes).
BenchMode = Literal["exact", "hnsw", "hnsw+index"]
WARMUP = 3

DEFAULT_QUERIES = [
    "backend python bengaluru 10 LPA remote ok",
    "senior data engineer spark airflow hyderabad",
    "frontend react typescript remote",
    "devops kubernetes terraform pune",
    "machine learning engineer pytorch mumbai 25 LPA",
]

FILTER_SETS: dict[str, SearchFilters] = {
    "none": SearchFilters(),
    "city": SearchFilters(cities=["Bengaluru"], remote_ok=True),
    "city+salary+seniority": SearchFilters(
        cities=["Bengaluru"], remote_ok=True, min_salary_lpa=10, seniority=["senior"]
    ),
}


def percentile_ms(samples: Sequence[float], q: float) -> float:
    return round(float(np.percentile(np.asarray(samples), q)), 2)


def _scan_nodes(plan: dict[str, Any]) -> list[str]:
    """Scan/index nodes in the plan tree, e.g. 'Index Scan using jobs_embedding_hnsw'."""
    found: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        kind = str(node.get("Node Type", ""))
        if "Scan" in kind and kind != "Subquery Scan":
            idx = node.get("Index Name")
            rel = node.get("Relation Name") or node.get("CTE Name")
            found.append(f"{kind} using {idx}" if idx else f"{kind} on {rel}")
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return found


def summarise_plan(explain: Any) -> dict[str, Any]:
    root = cast(dict[str, Any], explain[0] if isinstance(explain, list) else explain)
    plan = cast(dict[str, Any], root["Plan"])
    return {
        "top_node": plan.get("Node Type"),
        "scan_nodes": _scan_nodes(plan),
        "execution_ms": round(float(root.get("Execution Time", 0.0)), 2),
        "planning_ms": round(float(root.get("Planning Time", 0.0)), 2),
        "shared_hit_blocks": plan.get("Shared Hit Blocks"),
        "shared_read_blocks": plan.get("Shared Read Blocks"),
        "plan_rows": plan.get("Actual Rows"),
    }


async def _begin(conn: AsyncConnection, mode: BenchMode, legs: Legs) -> None:
    await conn.execute(text("SET TRANSACTION READ ONLY"))
    if legs != "lex":
        await apply_vector_mode(conn, "exact" if mode == "exact" else "hnsw")
        if mode == "hnsw+index":
            await conn.execute(text("SELECT set_config('enable_seqscan', 'off', true)"))


async def _time_statement(
    engine: Any,
    mode: BenchMode,
    legs: Legs,
    stmts: list[TextClause],
    param_sets: list[dict[str, Any]],
    runs: int,
) -> dict[str, Any]:
    """Time ``stmts`` executed back to back in one transaction (one stmt = a single leg)."""
    samples: list[float] = []
    rows = 0
    for i in range(WARMUP + runs):
        fetched: list[Any] = []
        params = param_sets[i % len(param_sets)]
        async with engine.begin() as conn:
            await _begin(conn, mode, legs)
            t0 = time.perf_counter()
            for stmt in stmts:
                result = await conn.execute(stmt, params)
                fetched = result.all()
            elapsed = (time.perf_counter() - t0) * 1000
        if i >= WARMUP:
            samples.append(elapsed)
            rows = len(fetched)
    return {
        "p50_ms": percentile_ms(samples, 50),
        "p95_ms": percentile_ms(samples, 95),
        "max_ms": round(max(samples), 2),
        "rows": rows,
        "runs": len(samples),
    }


async def _explain(
    engine: Any, mode: BenchMode, legs: Legs, stmt: TextClause, params: dict[str, Any]
) -> dict[str, Any]:
    async with engine.begin() as conn:
        await _begin(conn, mode, legs)
        plan = (
            await conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + stmt.text), params)
        ).scalar_one()
    return summarise_plan(plan)


def load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES
    lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")] or DEFAULT_QUERIES


def _embed_all(embedder: Embedder, queries: list[str]) -> list[list[float]]:
    return embedder.embed(queries)


async def run_matrix(
    dsn: str,
    *,
    runs: int = 30,
    queries: list[str] | None = None,
    embedder: Embedder | None = None,
    modes: Sequence[BenchMode] = ("exact", "hnsw", "hnsw+index"),
    filter_sets: dict[str, SearchFilters] | None = None,
) -> dict[str, Any]:
    assert_bench_database(dsn)
    cfg = ranking_config()
    ret = cfg.retrieval
    qs = queries or DEFAULT_QUERIES
    emb = embedder or build_embedder(get_settings())
    vectors = _embed_all(emb, qs)
    parses = [parse_rules(q) for q in qs]
    fsets = filter_sets or FILTER_SETS

    engine = create_async_engine(sa_dsn(dsn), pool_size=2)
    out: dict[str, Any] = {"runs": runs, "queries": len(qs), "results": []}
    try:
        async with engine.connect() as conn:
            counts = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM jobs WHERE status = 'active' "
                        "AND canonical_job_id IS NULL"
                    )
                )
            ).scalar_one()
        out["active_canonical_rows"] = int(counts)
        out["retrieval"] = ret.model_dump()
        for mode in modes:
            for fname, filters in fsets.items():
                compiled = compile_filters(filters)
                param_sets: list[dict[str, Any]] = []
                for parse, vec in zip(parses, vectors, strict=True):
                    param_sets.append(
                        {
                            "lex_text": parse.lex_text,
                            "skill_tokens": skill_tokens(parse.skills),
                            "w_skill": ret.skill_rank_weight,
                            "lex_k": ret.lexical_k,
                            "vec_k": ret.vector_k,
                            "rrf_k": ret.rrf_k,
                            "fused_k": ret.fused_k,
                            "qvec": vector_literal(vec),
                            **compiled.params,
                        }
                    )
                sa_sets = [
                    {
                        **p,
                        "lex_k": ret.set_aside_universe_k,
                        "vec_k": ret.set_aside_universe_k,
                    }
                    for p in param_sets
                ]
                hybrid = build_hybrid_sql(compiled.sql, "both")
                lex = build_hybrid_sql(compiled.sql, "lex")
                vec_leg = build_hybrid_sql(compiled.sql, "vec")
                set_aside = build_set_aside_sql(compiled.clauses, "both")
                entry: dict[str, Any] = {"mode": mode, "filters": fname, "legs": {}}
                legs_cfg: list[tuple[str, Legs, TextClause, list[dict[str, Any]]]] = [
                    ("lex", "lex", lex, param_sets),
                    ("vec", "vec", vec_leg, param_sets),
                    ("hybrid", "both", hybrid, param_sets),
                    ("set_aside", "both", set_aside, sa_sets),
                ]
                for name, legs, stmt, sets in legs_cfg:
                    timing = await _time_statement(engine, mode, legs, [stmt], sets, runs)
                    timing["plan"] = await _explain(engine, mode, legs, stmt, sets[0])
                    entry["legs"][name] = timing
                # Full path: hybrid then set-aside in one transaction, differing parameters.
                full_samples: list[float] = []
                for i in range(WARMUP + runs):
                    j = i % len(param_sets)
                    async with engine.begin() as conn:
                        await _begin(conn, mode, "both")
                        t0 = time.perf_counter()
                        (await conn.execute(hybrid, param_sets[j])).all()
                        (await conn.execute(set_aside, sa_sets[j])).all()
                        elapsed = (time.perf_counter() - t0) * 1000
                    if i >= WARMUP:
                        full_samples.append(elapsed)
                entry["full"] = {
                    "p50_ms": percentile_ms(full_samples, 50),
                    "p95_ms": percentile_ms(full_samples, 95),
                    "max_ms": round(max(full_samples), 2),
                    "runs": len(full_samples),
                }
                out["results"].append(entry)
    finally:
        await engine.dispose()
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--dsn", required=True, help="bench database DSN (must end _bench)")
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--queries-file", help="one query per line (default: 5 built-in queries)")
    args = p.parse_args(argv)
    assert_bench_database(args.dsn)
    result = asyncio.run(
        run_matrix(args.dsn, runs=args.runs, queries=load_queries(args.queries_file))
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
