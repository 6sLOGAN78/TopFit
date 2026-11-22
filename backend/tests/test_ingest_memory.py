"""Board ingests hold one board's postings at a time (plan 02-18 Task 2, T-02-75).

The 2026-11-22 production Greenhouse run peaked at 1023/1024 MiB. These tests use synthetic
boards served by respx and prove that, when the next board's request starts, nothing from the
earlier boards is still alive, that each board is upserted before the next fetch, that the
memory peak does not grow with the board count, and that embedding stays in 32-text batches.
"""

import gc
import json
import tracemalloc
import weakref
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.embeddings.embedder import EMBED_DIM, FakeEmbedder
from ranker.ingest import pipeline
from ranker.ingest.board_registry import Board, Host
from ranker.ingest.pipeline import RunSummary, run_ingest
from ranker.ingest.sources import boardbase
from ranker.ingest.sources.base import AdapterContext, RawJob
from ranker.ingest.sources.boardbase import BoardsConfig
from ranker.ingest.sources.greenhouse import GreenhouseAdapter
from ranker.settings import get_settings

pytestmark = pytest.mark.usefixtures("clean_db")

GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
PER_BOARD = 300
FILLER = "Python backend services, distributed systems and PostgreSQL. " * 130  # ~8 KB


def _board_bytes(slug: str, n: int, base: int) -> bytes:
    jobs: list[dict[str, Any]] = []
    for i in range(n):
        jid = base + i
        jobs.append(
            {
                "id": jid,
                "title": f"Backend Engineer {jid}",
                "absolute_url": f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}",
                "location": {"name": "Bengaluru, India"},
                "content": f"&lt;p&gt;Role {jid}. {FILLER}&lt;/p&gt;",
                "first_published": "2026-11-20T10:00:00Z",
                "departments": [{"name": "Engineering"}],
                "offices": [{"name": "Bengaluru", "location": "Bengaluru, India"}],
                "company_name": "Synthetic Co",
            }
        )
    return json.dumps({"jobs": jobs}).encode()


class TrackedDict(dict[str, Any]):
    """A weakref-able payload so tests can see when a board's JSON is released."""


class CountingEmbedder:
    def __init__(self) -> None:
        self.dim = EMBED_DIM
        self._inner = FakeEmbedder()
        self.sizes: list[int] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.sizes.append(len(texts))
        return self._inner.embed(texts)


class ProbeAdapter(GreenhouseAdapter):
    """Runs a check when each board's request starts, and logs fetch order."""

    def __init__(self, ctx: AdapterContext, log: list[str], check: Any = None) -> None:
        async def sleep(_: float) -> None:
            return None

        super().__init__(ctx, sleep=sleep)
        self.log = log
        self.check = check

    async def get_payload(
        self, client: httpx.AsyncClient, board: Board
    ) -> tuple[object, Host | None]:
        if self.check is not None:
            self.check(board.slug)
        self.log.append(f"fetch:{board.slug}")
        return await super().get_payload(client, board)


def _adapter(engine: AsyncEngine, seeds: list[str], log: list[str], check: Any = None) -> Any:
    get_settings.cache_clear()
    ctx = AdapterContext(
        settings=get_settings(),
        engine=engine,
        config=BoardsConfig(seeds=seeds, request_interval_seconds=0),
        mode="incremental",
        now=datetime.now(UTC),
    )
    return ProbeAdapter(ctx, log, check)


def _serve(router: respx.MockRouter, boards: dict[str, bytes]) -> None:
    for slug, body in boards.items():
        router.get(GH.format(slug)).mock(
            side_effect=lambda _req, body=body: httpx.Response(
                200, content=body, headers={"content-type": "application/json"}
            )
        )


async def _run(engine: AsyncEngine, adapter: Any, embedder: Any = None) -> RunSummary:
    return await run_ingest(
        engine,
        get_settings(),
        "greenhouse",
        adapter=adapter,
        embedder=embedder or FakeEmbedder(),
    )


async def test_previous_board_is_released_before_next_fetch(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    slugs = ["b1", "b2", "b3"]
    bodies = {s: _board_bytes(s, PER_BOARD, 10_000 * (k + 1)) for k, s in enumerate(slugs)}
    refs: list[weakref.ref[TrackedDict]] = []
    real_get_json = boardbase.get_json

    async def tracked_get_json(*args: Any, **kwargs: Any) -> object:
        data = await real_get_json(*args, **kwargs)
        tracked = TrackedDict(data) if isinstance(data, dict) else data
        if isinstance(tracked, TrackedDict):
            refs.append(weakref.ref(tracked))
        return tracked

    monkeypatch.setattr(boardbase, "get_json", tracked_get_json)
    seen: list[str] = []
    findings: list[tuple[str, int, int]] = []  # (slug, live earlier RawJobs, live payloads)

    def check(slug: str) -> None:
        earlier = set(seen)
        seen.append(slug)
        if not earlier:
            return
        gc.collect()
        live_jobs = sum(
            1 for o in gc.get_objects() if isinstance(o, RawJob) and o.source_scope in earlier
        )
        live_payloads = sum(1 for r in refs if r() is not None)
        findings.append((slug, live_jobs, live_payloads))

    embedder = CountingEmbedder()
    with respx.mock(assert_all_called=True) as router:
        _serve(router, bodies)
        summary = await _run(engine, _adapter(engine, slugs, [], check), embedder)
    assert summary.status == "succeeded" and summary.fetched == 3 * PER_BOARD
    assert summary.inserted == 3 * PER_BOARD
    assert findings == [("b2", 0, 0), ("b3", 0, 0)]
    assert len(refs) == 3
    # Embedding stays bounded: never more than 32 texts per model call.
    assert embedder.sizes and max(embedder.sizes) <= 32
    assert sum(embedder.sizes) == summary.embedded == 3 * PER_BOARD


async def test_each_board_upserted_before_next_fetch(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 120 per board (below BATCH_SIZE), so only the scope-flush hook can cause these flushes.
    slugs = ["b1", "b2", "b3"]
    bodies = {s: _board_bytes(s, 120, 10_000 * (k + 1)) for k, s in enumerate(slugs)}
    log: list[str] = []
    real_process = pipeline._process_buffer  # pyright: ignore[reportPrivateUsage]

    async def logged(
        engine_: AsyncEngine, run_id: int, source: str, buffer: list[RawJob], *a: Any
    ) -> None:
        log.append("upsert:" + ",".join(sorted({r.source_scope or "" for r in buffer})))
        await real_process(engine_, run_id, source, buffer, *a)

    monkeypatch.setattr(pipeline, "_process_buffer", logged)
    with respx.mock(assert_all_called=True) as router:
        _serve(router, bodies)
        summary = await _run(engine, _adapter(engine, slugs, log))
    assert log == ["fetch:b1", "upsert:b1", "fetch:b2", "upsert:b2", "fetch:b3", "upsert:b3"]
    assert summary.inserted == 360 and summary.status == "succeeded"
    assert summary.calls_used == 3


async def test_large_board_still_split_at_batch_size(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sizes: list[int] = []
    real_process = pipeline._process_buffer  # pyright: ignore[reportPrivateUsage]

    async def logged(
        engine_: AsyncEngine, run_id: int, source: str, buffer: list[RawJob], *a: Any
    ) -> None:
        sizes.append(len(buffer))
        await real_process(engine_, run_id, source, buffer, *a)

    monkeypatch.setattr(pipeline, "_process_buffer", logged)
    with respx.mock(assert_all_called=True) as router:
        _serve(router, {"big": _board_bytes("big", 350, 50_000)})
        summary = await _run(engine, _adapter(engine, ["big"], []))
    assert sizes == [300, 50] and summary.inserted == 350


async def _drop_boards(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM source_boards"))


async def _peak(engine: AsyncEngine, bodies: dict[str, bytes]) -> int:
    await _drop_boards(engine)
    with respx.mock(assert_all_called=True) as router:
        _serve(router, bodies)
        adapter = _adapter(engine, list(bodies), [])
        gc.collect()
        start, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        summary = await _run(engine, adapter)
        _, peak = tracemalloc.get_traced_memory()
    assert summary.status == "succeeded" and summary.fetched == PER_BOARD * len(bodies)
    return peak - start


async def test_peak_memory_does_not_scale_with_board_count(engine: AsyncEngine) -> None:
    """Tolerant ratio check: three boards peak at <= 1.5x the one-board peak, not ~3x."""
    warm = {"w": _board_bytes("w", PER_BOARD, 1_000_000)}
    one = {"s": _board_bytes("s", PER_BOARD, 2_000_000)}
    three = {f"m{k}": _board_bytes(f"m{k}", PER_BOARD, 3_000_000 + k * 10_000) for k in (1, 2, 3)}
    await _drop_boards(engine)
    with respx.mock(assert_all_called=True) as router:  # warm caches outside the measurement
        _serve(router, warm)
        await _run(engine, _adapter(engine, list(warm), []))
    tracemalloc.start()
    try:
        single = await _peak(engine, one)
        multi = await _peak(engine, three)
    finally:
        tracemalloc.stop()
    assert single > 0
    assert multi <= 1.5 * single, (single, multi)
