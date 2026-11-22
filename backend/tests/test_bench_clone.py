import asyncio
import json
from collections.abc import Iterator
from typing import Any

import numpy as np
import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import FakeEmbedder
from ranker.search.models import SearchFilters
from ranker.spike import bench_clone, bench_explain
from tests.fixtures.search.jobs_seed import add_job

BENCH_DB = "ranker_unit_bench"


def test_guard_rejects_non_bench_database() -> None:
    for dsn in (
        "postgresql+psycopg://u:p@db:5432/ranker",
        "postgresql://u:p@db:5432/ranker_bench_backup",
        "postgresql://u:p@db:5432/postgres",
    ):
        with pytest.raises(ValueError, match="_bench"):
            bench_clone.assert_bench_database(dsn)
        with pytest.raises(ValueError, match="_bench"):
            bench_clone.clone("postgresql://u:p@db:5432/src", dsn)
        with pytest.raises(ValueError, match="_bench"):
            bench_clone.drop_database(dsn)
    assert bench_clone.assert_bench_database("postgresql://u:p@h/ranker_bench") == "ranker_bench"


def test_clone_refuses_same_database() -> None:
    with pytest.raises(ValueError, match="same database"):
        bench_clone.clone("postgresql://u:p@h/x_bench", "postgresql://u:p@h/x_bench")


def test_retarget_keeps_credentials() -> None:
    out = bench_clone.retarget("postgresql+psycopg://u:p%40ss@db:5432/ranker", "ranker_bench")
    url = make_url(out)
    assert (url.username, url.password, url.host, url.database) == (
        "u",
        "p@ss",
        "db",
        "ranker_bench",
    )


def test_jittered_is_unit_length_and_close() -> None:
    rng = np.random.default_rng(1)
    base = np.asarray(FakeEmbedder().embed(["backend"])[0])
    base = base / np.linalg.norm(base)
    out = bench_clone.jittered(base, 0.02, rng)
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-9
    assert 0.95 < float(out @ base) < 1.0


@pytest.fixture
def bench_dsn(database_url: str) -> Iterator[str]:
    dsn = bench_clone.retarget(database_url, BENCH_DB)
    bench_clone.drop_database(dsn)
    yield dsn
    bench_clone.drop_database(dsn)


@pytest.mark.usefixtures("clean_db")
async def test_clone_multiplies_jobs_with_jittered_embeddings(
    engine: AsyncEngine, database_url: str, bench_dsn: str
) -> None:
    for n in range(4):
        await add_job(
            engine,
            title=f"Backend Engineer {n}",
            skills=["python"],
            description=f"build services {n}",
            company=f"Co{n}",
            city="Bengaluru",
            country_code="IN",
            remote_type="hybrid",
            seniority="senior",
            salary_min_inr=2_000_000,
            salary_max_inr=3_000_000,
        )

    summary = await asyncio.to_thread(
        bench_clone.clone, database_url, bench_dsn, factor=3, jitter=0.02, seed=7
    )
    assert summary["rows"] == 12
    assert summary["source_jobs"] == 4

    with (
        psycopg.connect(bench_clone.pg_dsn(database_url)) as src,
        psycopg.connect(bench_clone.pg_dsn(bench_dsn)) as tgt,
    ):
        orig: dict[str, tuple[str, str]] = {
            str(r[0]): (str(r[1]), str(r[2]))
            for r in src.execute(
                "SELECT source_job_id, description_clean, embedding::text FROM jobs"
            )
        }
        assert len(orig) == 4
        rows = tgt.execute(
            "SELECT source_job_id, description_clean, embedding::text FROM jobs"
        ).fetchall()
        assert len(rows) == 12
        assert len({r[0] for r in rows}) == 12
        for sid, desc, emb in rows:
            base_id = str(sid).split("~b")[0]
            assert base_id in orig and str(sid) not in orig
            assert desc == orig[base_id][0]
            a = bench_clone._parse_vector(emb)  # pyright: ignore[reportPrivateUsage]
            b = bench_clone._parse_vector(orig[base_id][1])  # pyright: ignore[reportPrivateUsage]
            assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-4
            cos = float(a @ b) / float(np.linalg.norm(b))
            assert 0.95 < cos < 1.0
        # generated tsvector columns and the HNSW index were rebuilt on the bench copy
        idx = tgt.execute(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'jobs_embedding_hnsw'"
        ).fetchone()
        assert idx is not None and idx[0] == 1
        assert tgt.execute("SELECT count(*) FROM jobs WHERE tsv IS NOT NULL").fetchone() == (12,)
        # source untouched
        assert src.execute("SELECT count(*) FROM jobs").fetchone() == (4,)

    with pytest.raises(RuntimeError, match="--reset"):
        await asyncio.to_thread(bench_clone.clone, database_url, bench_dsn, factor=1)
    again = await asyncio.to_thread(
        bench_clone.clone, database_url, bench_dsn, factor=2, reset=True
    )
    assert again["rows"] == 8

    # EXPLAIN matrix runs end to end against the small clone and never writes
    result: dict[str, Any] = await bench_explain.run_matrix(
        bench_dsn,
        runs=2,
        modes=("exact", "hnsw"),
        queries=bench_explain.DEFAULT_QUERIES[:2],
        filter_sets={
            "none": SearchFilters(),
            "city+salary+seniority": SearchFilters(
                cities=["Bengaluru"], min_salary_lpa=10, seniority=["senior"]
            ),
        },
    )
    print(json.dumps(result, indent=2, default=str))
    assert result["active_canonical_rows"] == 8
    assert [(r["mode"], r["filters"]) for r in result["results"]] == [
        ("exact", "none"),
        ("exact", "city+salary+seniority"),
        ("hnsw", "none"),
        ("hnsw", "city+salary+seniority"),
    ]
    for entry in result["results"]:
        assert set(entry["legs"]) == {"lex", "vec", "hybrid", "set_aside"}
        assert entry["legs"]["vec"]["rows"] > 0
        assert entry["legs"]["hybrid"]["plan"]["scan_nodes"]
        assert entry["full"]["p95_ms"] >= entry["full"]["p50_ms"]
    exact_vec = result["results"][0]["legs"]["vec"]["plan"]["scan_nodes"]
    assert not any("jobs_embedding_hnsw" in n for n in exact_vec)

    assert bench_clone.drop_database(bench_dsn) is True
    assert bench_clone.drop_database(bench_dsn) is False
