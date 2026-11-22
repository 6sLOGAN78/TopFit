"""Spike S1/S3 tooling: clone real jobs xN into a separate ``*_bench`` database.

Pitfall 7 (03-RESEARCH): benchmarking on random vectors is unrealistic, so the real active
canonical jobs are copied ``--factor`` times with new ``source_job_id`` values, real text, and
embeddings jittered with Gaussian noise and re-normalised.

Safety (T-03-36):
- The target database name must end with ``_bench``; anything else raises before a connection
  is opened.
- The source is opened in a READ ONLY transaction, so this tool cannot write to production.
- ``--drop`` removes the bench database again (clean teardown).

Run inside the api image on the VM (see bench/README.md)::

    python -m ranker.spike.bench_clone --source-dsn "$DATABASE_URL" --target-db ranker_bench
"""

import argparse
import json
import math
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, LiteralString, cast

import numpy as np
import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from ranker.settings import get_settings

BENCH_SUFFIX = "_bench"
HNSW_INDEX = "jobs_embedding_hnsw"
BATCH_ROWS = 2000
# Resolves to backend/ locally and /app in the image (both hold alembic.ini + migrations/).
ROOT = Path(__file__).resolve().parents[2]


def database_name(dsn: str) -> str:
    name = make_url(dsn).database
    if not name:
        raise ValueError("DSN has no database name")
    return name


def assert_bench_database(dsn: str) -> str:
    """Return the database name, or raise unless it ends with ``_bench``."""
    name = database_name(dsn)
    if not name.endswith(BENCH_SUFFIX):
        raise ValueError(
            f"refusing to touch database {name!r}: bench tooling only runs on *{BENCH_SUFFIX}"
        )
    return name


def retarget(dsn: str, database: str) -> str:
    """The same server and credentials with a different database name."""
    return make_url(dsn).set(database=database).render_as_string(hide_password=False)


def sa_dsn(dsn: str) -> str:
    """SQLAlchemy URL form (postgresql+psycopg), used by alembic and the async engine."""
    url: URL = make_url(dsn).set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def pg_dsn(dsn: str) -> str:
    """libpq URI form for psycopg."""
    return make_url(dsn).set(drivername="postgresql").render_as_string(hide_password=False)


def _parse_vector(text_value: str) -> np.ndarray:
    return np.array([float(x) for x in text_value.strip()[1:-1].split(",")], dtype=np.float64)


def _format_vector(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def jittered(vec: np.ndarray, jitter: float, rng: np.random.Generator) -> np.ndarray:
    """Add isotropic Gaussian noise of expected L2 norm ``jitter``, then renormalise.

    ``jitter`` is relative to a unit vector: 0.02 keeps the cosine to the original near 0.9998,
    0.3 gives about 0.96. Per-dimension sigma is ``jitter / sqrt(dim)`` so the setting does not
    depend on the embedding dimension.
    """
    dim = int(vec.shape[0])
    noise: np.ndarray = rng.normal(0.0, jitter / math.sqrt(dim), dim)
    noisy: np.ndarray = vec + noise
    norm = float(np.linalg.norm(noisy))
    result: np.ndarray = noisy / norm if norm > 0 else noisy
    return result


def clone_rows(
    rows: list[dict[str, Any]], factor: int, jitter: float, seed: int
) -> Iterator[dict[str, Any]]:
    """Yield ``factor`` jittered copies of every row, each with a fresh source_job_id."""
    rng = np.random.default_rng(seed)
    for k in range(factor):
        for row in rows:
            out = dict(row)
            out["source_job_id"] = f"{row['source_job_id']}~b{k}"
            out["canonical_job_id"] = None
            out["embedding"] = _format_vector(
                jittered(_parse_vector(str(row["embedding"])), jitter, rng)
            )
            yield out


def _create_database(target_dsn: str) -> None:
    name = assert_bench_database(target_dsn)
    admin = pg_dsn(retarget(target_dsn, "postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def drop_database(target_dsn: str) -> bool:
    """Drop the bench database (connections are terminated). Returns False if it was absent."""
    name = assert_bench_database(target_dsn)
    admin = pg_dsn(retarget(target_dsn, "postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            return False
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    return True


def migrate(target_dsn: str) -> None:
    """alembic upgrade head on the bench database (env.py reads DATABASE_URL via settings)."""
    assert_bench_database(target_dsn)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = sa_dsn(target_dsn)
    get_settings.cache_clear()
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def _insertable_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND is_generated = 'NEVER' "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _select_list(columns: list[str]) -> sql.Composed:
    parts: list[sql.Composable] = []
    for col in columns:
        ident = sql.Identifier(col)
        if col == "embedding":
            parts.append(sql.SQL("{}::text AS {}").format(ident, ident))
        else:
            parts.append(ident)
    return sql.SQL(", ").join(parts)


def _read_source(source_dsn: str, columns: list[str]) -> tuple[list[dict[str, Any]], list[Any]]:
    """Active canonical embedded jobs plus all companies, in a READ ONLY transaction."""
    with psycopg.connect(pg_dsn(source_dsn)) as conn:
        conn.read_only = True
        company_cols = _insertable_columns(conn, "companies")
        companies = conn.execute(
            sql.SQL("SELECT {} FROM companies ORDER BY id").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in company_cols)
            )
        ).fetchall()
        cur = conn.execute(
            sql.SQL(
                "SELECT {} FROM jobs WHERE status = 'active' AND canonical_job_id IS NULL "
                "AND embedding IS NOT NULL ORDER BY id"
            ).format(_select_list(columns))
        )
        names = [d.name for d in cur.description or []]
        jobs = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    return jobs, companies


def clone(
    source_dsn: str,
    target_dsn: str,
    *,
    factor: int = 25,
    jitter: float = 0.02,
    seed: int = 7,
    reset: bool = False,
) -> dict[str, Any]:
    """Create/migrate the bench database and load ``factor`` jittered copies of the source jobs."""
    target_name = assert_bench_database(target_dsn)
    if database_name(source_dsn) == target_name:
        raise ValueError("source and target are the same database")
    if factor < 1:
        raise ValueError("factor must be >= 1")
    started = time.perf_counter()

    _create_database(target_dsn)
    migrate(target_dsn)

    with psycopg.connect(pg_dsn(target_dsn)) as tgt:
        job_cols = [c for c in _insertable_columns(tgt, "jobs") if c != "id"]
        company_cols = _insertable_columns(tgt, "companies")
        existing = tgt.execute("SELECT count(*) FROM jobs").fetchone()
        if existing and int(existing[0]) > 0:
            if not reset:
                raise RuntimeError(
                    f"{target_name} already holds {existing[0]} jobs; pass --reset to reload"
                )
            tgt.execute("TRUNCATE jobs, companies RESTART IDENTITY CASCADE")
        tgt.commit()

        jobs, companies = _read_source(source_dsn, job_cols)
        if not jobs:
            raise RuntimeError("source has no active canonical jobs with embeddings")

        indexdef_row = tgt.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s", (HNSW_INDEX,)
        ).fetchone()
        tgt.execute("SET maintenance_work_mem = '128MB'")
        if indexdef_row:
            tgt.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(HNSW_INDEX)))

        with tgt.cursor() as cur:
            with cur.copy(
                sql.SQL("COPY companies ({}) FROM STDIN").format(
                    sql.SQL(", ").join(sql.Identifier(c) for c in company_cols)
                )
            ) as cp:
                for row in companies:
                    cp.write_row(row)
            copy_sql = sql.SQL("COPY jobs ({}) FROM STDIN").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in job_cols)
            )
            with cur.copy(copy_sql) as cp:
                for row in clone_rows(jobs, factor, jitter, seed):
                    cp.write_row([row[c] for c in job_cols])
        tgt.execute(
            "SELECT setval(pg_get_serial_sequence('companies', 'id'), "
            "greatest((SELECT coalesce(max(id), 1) FROM companies), 1))"
        )
        tgt.commit()
        loaded_s = time.perf_counter() - started

        if indexdef_row:
            tgt.execute(
                cast(LiteralString, str(indexdef_row[0]))
            )  # catalog text, recreated verbatim
        tgt.commit()
        tgt.autocommit = True
        tgt.execute("ANALYZE jobs")
        tgt.execute("ANALYZE companies")
        total = tgt.execute("SELECT count(*) FROM jobs").fetchone()
        size = tgt.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()

    return {
        "target_db": target_name,
        "source_jobs": len(jobs),
        "factor": factor,
        "jitter": jitter,
        "seed": seed,
        "rows": int(total[0]) if total else 0,
        "db_size": str(size[0]) if size else "",
        "load_seconds": round(loaded_s, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--source-dsn", default=os.environ.get("DATABASE_URL"), help="read-only source")
    tgt = p.add_mutually_exclusive_group(required=True)
    tgt.add_argument("--target-dsn", help="full DSN of the bench database (must end _bench)")
    tgt.add_argument("--target-db", help="bench database name on the source server (_bench)")
    p.add_argument("--factor", type=int, default=25)
    p.add_argument("--jitter", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--reset", action="store_true", help="truncate an existing bench load first")
    p.add_argument("--drop", action="store_true", help="drop the bench database and exit")
    args = p.parse_args(argv)

    if args.target_dsn:
        target_dsn = args.target_dsn
    else:
        if not args.source_dsn:
            p.error("--target-db needs --source-dsn or DATABASE_URL for the server credentials")
        target_dsn = retarget(args.source_dsn, args.target_db)
    assert_bench_database(target_dsn)

    if args.drop:
        print(json.dumps({"dropped": drop_database(target_dsn), "db": database_name(target_dsn)}))
        return
    if not args.source_dsn:
        p.error("--source-dsn (or DATABASE_URL) is required")
    summary = clone(
        args.source_dsn,
        target_dsn,
        factor=args.factor,
        jitter=args.jitter,
        seed=args.seed,
        reset=args.reset,
    )
    print(json.dumps(summary))  # one line: alembic logs share stdout, so `tail -n1` is the summary


if __name__ == "__main__":
    main()
