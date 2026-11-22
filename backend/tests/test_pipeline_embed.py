import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings import FakeEmbedder
from ranker.ingest.pipeline import run_ingest
from ranker.settings import Settings, get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "remotive_jobs.json"
URL = "https://remotive.com/api/remote-jobs"

pytestmark = pytest.mark.usefixtures("clean_db")


class SpyEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts.extend(texts)
        return super().embed(texts)


class BoomEmbedder:
    dim = 384

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("secret detail that must not be logged")


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


def _serve(payload: dict[str, Any] | None = None) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload or _payload()))


def _settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


async def _vectors(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT source_job_id, embedding::text FROM jobs ORDER BY source_job_id")
        )
        return {r[0]: r[1] for r in rows}


async def _run_notes(engine: AsyncEngine, run_id: int) -> dict[str, Any]:
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT notes FROM ingest_runs WHERE id = :i"), {"i": run_id})
        ).scalar_one()


@respx.mock
async def test_embed_on_insert_skip_unchanged_and_change(engine: AsyncEngine) -> None:
    settings = _settings()
    _serve()
    spy = SpyEmbedder()
    first = await run_ingest(engine, settings, "remotive", embedder=spy)
    assert first.status == "succeeded" and first.embedded == 6
    assert (await _run_notes(engine, first.run_id))["embedded"] == 6
    async with engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE vector_dims(embedding) = 384 "
                    "AND embedding_model = :m"
                ),
                {"m": settings.embed_model},
            )
        ).scalar_one()
    assert n == 6
    before = await _vectors(engine)

    spy2 = SpyEmbedder()
    again = await run_ingest(engine, settings, "remotive", embedder=spy2)
    assert again.embedded == 0 and spy2.calls == 0
    assert (await _run_notes(engine, again.run_id))["embedded"] == 0
    assert await _vectors(engine) == before

    payload = _payload()
    for item in payload["jobs"]:
        if item["id"] == 1001:
            item["description"] = "<p>Completely new Rust and Kafka text.</p>"
    respx.clear()
    _serve(payload)
    spy3 = SpyEmbedder()
    changed = await run_ingest(engine, settings, "remotive", embedder=spy3)
    assert changed.embedded == 1 and len(spy3.texts) == 1
    after = await _vectors(engine)
    assert after["1001"] != before["1001"]
    assert {k: v for k, v in after.items() if k != "1001"} == {
        k: v for k, v in before.items() if k != "1001"
    }


@respx.mock
async def test_embedder_failure_downgrades_to_partial(engine: AsyncEngine) -> None:
    _serve()
    summary = await run_ingest(engine, _settings(), "remotive", embedder=BoomEmbedder())
    assert summary.status == "partial" and summary.inserted == 6
    async with engine.connect() as conn:
        errors = (
            await conn.execute(
                text("SELECT errors FROM ingest_runs WHERE id = :i"), {"i": summary.run_id}
            )
        ).scalar_one()
        nulls = (
            await conn.execute(text("SELECT count(*) FROM jobs WHERE embedding IS NULL"))
        ).scalar_one()
    assert nulls == 6
    assert {"source_job_id": None, "error": "EmbedError:RuntimeError"} in errors
    assert "secret" not in json.dumps(errors)

    # The next pass fills the leftovers.
    again = await run_ingest(engine, _settings(), "remotive", embedder=SpyEmbedder())
    assert again.embedded == 6 and again.status == "succeeded"
