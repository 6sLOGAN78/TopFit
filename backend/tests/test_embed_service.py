from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings import (
    EmbeddingDimensionError,
    FakeEmbedder,
    embed_missing,
    invalidate_other_models,
    job_embedding_text,
)

pytestmark = pytest.mark.usefixtures("clean_db")
MODEL = "BAAI/bge-small-en-v1.5"


class SpyEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[int] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(len(texts))
        return super().embed(texts)


class BadEmbedder:
    dim = 384

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * 10 for _ in texts]


async def _seed(engine: AsyncEngine, n: int, *, status: str = "active", start: int = 0) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (source, source_job_id, url, title, description_clean, "
                "description_raw, content_hash, normalizer_version, dedupe_key, status) "
                "SELECT 'test', 'j' || g, 'https://example.com/' || g, 'Title ' || g, "
                "'desc ' || g, 'desc ' || g, 'h' || g, '1', 'd' || g, CAST(:st AS text) "
                "FROM generate_series(CAST(:a AS int), CAST(:b AS int)) g"
            ),
            {"st": status, "a": start, "b": start + n - 1},
        )


async def _count(engine: AsyncEngine, where: str, params: dict[str, str] | None = None) -> int:
    async with engine.connect() as conn:
        query = text(f"SELECT count(*) FROM jobs WHERE {where}")  # noqa: S608
        return (await conn.execute(query, params or {})).scalar_one()


async def test_batches_dims_and_status_filter(engine: AsyncEngine) -> None:
    await _seed(engine, 70)
    await _seed(engine, 3, status="expired", start=1000)
    spy = SpyEmbedder()
    assert await embed_missing(engine, spy, MODEL, batch_size=32) == 70
    assert spy.batches == [32, 32, 6]
    done = await _count(
        engine, "vector_dims(embedding) = 384 AND embedding_model = :m", {"m": MODEL}
    )
    assert done == 70
    assert await _count(engine, "embedding IS NOT NULL AND status = 'expired'") == 0
    # Already embedded rows are not touched.
    assert await embed_missing(engine, spy, MODEL) == 0
    assert spy.batches == [32, 32, 6]


async def test_limit(engine: AsyncEngine) -> None:
    await _seed(engine, 10)
    assert await embed_missing(engine, SpyEmbedder(), MODEL, batch_size=4, limit=6) == 6


async def test_invalidate_other_models(engine: AsyncEngine) -> None:
    await _seed(engine, 5)
    await embed_missing(engine, SpyEmbedder(), "model-old")
    assert await invalidate_other_models(engine, "model-old") == 0
    assert await invalidate_other_models(engine, "model-x") == 5
    assert await embed_missing(engine, SpyEmbedder(), "model-x") == 5
    assert await _count(engine, "embedding_model = 'model-x'") == 5


async def test_wrong_dimension_writes_nothing(engine: AsyncEngine) -> None:
    await _seed(engine, 3)
    with pytest.raises(EmbeddingDimensionError):
        await embed_missing(engine, BadEmbedder(), MODEL)
    assert await _count(engine, "embedding IS NOT NULL") == 0


def test_job_embedding_text_caps() -> None:
    skills = [f"s{i}" for i in range(30)]
    out = job_embedding_text("T", "Co", skills, "x" * 5000)
    title, company, sk, desc = out.split("\n")
    assert (title, company) == ("T", "Co")
    assert sk.split(", ") == skills[:15]
    assert len(desc) == 1500
