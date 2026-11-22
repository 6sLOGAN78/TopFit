"""Embed pass over the jobs index (SRCH-01) with change-only recomputation (ING-09)."""

import time
from collections.abc import Sequence

import anyio.to_thread
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import Embedder

log = structlog.get_logger("ranker.embeddings.service")

MAX_SKILLS = 15
MAX_DESCRIPTION_CHARS = 1500


class EmbeddingDimensionError(RuntimeError):
    pass


def job_embedding_text(
    title: str,
    company: str | None,
    skills: Sequence[str] | None,
    description_clean: str | None,
) -> str:
    """SPEC EMB-01: title, company, top 15 skills, first 1,500 chars of the description."""
    return "\n".join(
        [
            title,
            company or "",
            ", ".join((skills or [])[:MAX_SKILLS]),
            (description_clean or "")[:MAX_DESCRIPTION_CHARS],
        ]
    )


def vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


_vector_literal = vector_literal  # backwards-compatible alias


_SELECT = text(
    "SELECT j.id, j.title, c.name AS company, j.skills, j.description_clean "
    "FROM jobs j LEFT JOIN companies c ON c.id = j.company_id "
    "WHERE j.status = 'active' AND j.embedding IS NULL AND j.id > :after "
    "ORDER BY j.id LIMIT :n"
)
_UPDATE = text(
    "UPDATE jobs SET embedding = CAST(:v AS vector), embedding_model = :m "
    "WHERE id = :id AND embedding IS NULL"
)


async def embed_missing(
    engine: AsyncEngine,
    embedder: Embedder,
    model_name: str,
    *,
    batch_size: int = 32,
    limit: int | None = None,
) -> int:
    """Embed active jobs whose embedding is NULL, in threaded batches. Returns rows written."""
    total = 0
    after = 0
    while limit is None or total < limit:
        n = batch_size if limit is None else min(batch_size, limit - total)
        async with engine.connect() as conn:
            rows = (await conn.execute(_SELECT, {"after": after, "n": n})).mappings().all()
        if not rows:
            break
        after = rows[-1]["id"]
        started = time.monotonic()
        texts = [
            job_embedding_text(r["title"], r["company"], r["skills"], r["description_clean"])
            for r in rows
        ]
        vectors = await anyio.to_thread.run_sync(embedder.embed, texts)
        if len(vectors) != len(rows) or any(len(v) != embedder.dim for v in vectors):
            raise EmbeddingDimensionError(f"expected {embedder.dim}-d vectors for {len(rows)} jobs")
        written = 0
        async with engine.begin() as conn:
            for r, vec in zip(rows, vectors, strict=True):
                res = await conn.execute(
                    _UPDATE, {"v": vector_literal(vec), "m": model_name, "id": r["id"]}
                )
                written += res.rowcount
        total += written
        log.info(
            "embed_batch_done",
            batch=len(rows),
            written=written,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    return total


async def invalidate_other_models(engine: AsyncEngine, model_name: str) -> int:
    """NULL embeddings produced by a different model so embed_missing recomputes them."""
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE jobs SET embedding = NULL "
                "WHERE embedding IS NOT NULL AND embedding_model IS DISTINCT FROM :m"
            ),
            {"m": model_name},
        )
    return res.rowcount
