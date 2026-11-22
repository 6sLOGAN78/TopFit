"""Ingest run lifecycle: lock, runs-per-day cap, stream, normalise, upsert, record (ING-05/06)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal

import anyio.to_thread
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ranker.config_loader import load_config
from ranker.embeddings.embedder import Embedder, build_embedder
from ranker.embeddings.service import embed_missing
from ranker.ingest import store
from ranker.ingest.hashing import content_hash
from ranker.ingest.normalize.job import NORMALIZER_VERSION, NormalizedJob, ScopeSkip, normalize_job
from ranker.ingest.sources import (
    AdapterContext,
    BaseSourceConfig,
    SourceAdapter,
    SourcesConfig,
    get_adapter_factory,
)
from ranker.ingest.sources.base import FetchResult, RawJob
from ranker.settings import Settings

log = structlog.get_logger("ranker.ingest.pipeline")

MAX_ERRORS = 50
MAX_SKIPPED_SAMPLES = 20
Mode = Literal["incremental", "backfill"]


class SourceLocked(RuntimeError):
    pass


class RunQuotaReached(RuntimeError):
    pass


class SourceNotConfigured(RuntimeError):
    pass


@dataclass
class RunSummary:
    source: str
    run_id: int
    status: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    calls_used: int = 0
    embedded: int = 0


def _error(source_job_id: str | None, exc: BaseException) -> dict[str, Any]:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    return {"source_job_id": source_job_id, "error": f"{type(exc).__name__}:{status}"}


def _source_config(source: str) -> BaseSourceConfig:
    try:
        return load_config("sources.yaml", SourcesConfig).root.get(source) or BaseSourceConfig()
    except Exception:  # missing/invalid config must not block a manual run
        log.warning("sources_config_unavailable", source=source)
        return BaseSourceConfig()


async def _process_buffer(
    engine: AsyncEngine,
    run_id: int,
    source: str,
    buffer: list[RawJob],
    now: datetime,
    summary: RunSummary,
    errors: list[dict[str, Any]],
    skipped_samples: list[str],
) -> None:
    unique: dict[str, RawJob] = {}
    for raw in buffer:
        unique[raw.source_job_id] = raw  # last wins
    hashes = {sid: content_hash(raw) for sid, raw in unique.items()}
    unchanged = skipped = failed = inserted = updated = 0
    local_errors: list[dict[str, Any]] = []
    local_samples: list[str] = []
    try:
        async with engine.begin() as conn:
            existing = await store.prefetch_versions(conn, source, list(unique))
            same = [
                sid for sid, h in hashes.items() if existing.get(sid) == (h, NORMALIZER_VERSION)
            ]
            unchanged = await store.touch_unchanged(conn, run_id, source, same, now)
            same_set = set(same)
            normalized: list[NormalizedJob] = []
            for sid, raw in unique.items():
                if sid in same_set:
                    continue
                try:
                    out = normalize_job(raw)
                except Exception as exc:
                    failed += 1
                    local_errors.append(_error(sid, exc))
                    continue
                if isinstance(out, ScopeSkip):
                    skipped += 1
                    local_samples.append(out.title[:200])
                else:
                    normalized.append(out)
            company_ids = await store.upsert_companies(
                conn,
                [
                    (j.company_name, j.company_normalized, j.company_is_placeholder)
                    for j in normalized
                ],
            )
            inserted, updated = await store.upsert_jobs(conn, run_id, normalized, company_ids, now)
            # Rows that raced to an identical hash count as unchanged.
            unchanged += len(normalized) - inserted - updated
    except Exception as exc:
        # The whole buffer rolled back: report it as failed, keep going with the next one.
        summary.failed += len(unique)
        if len(errors) < MAX_ERRORS:
            errors.append(_error(None, exc))
        log.warning("ingest_buffer_failed", source=source, error_type=type(exc).__name__)
        return
    summary.unchanged += unchanged
    summary.skipped += skipped
    summary.failed += failed
    summary.inserted += inserted
    summary.updated += updated
    errors.extend(local_errors[: max(0, MAX_ERRORS - len(errors))])
    skipped_samples.extend(local_samples[: max(0, MAX_SKIPPED_SAMPLES - len(skipped_samples))])


async def _acquire_lock(engine: AsyncEngine, source: str) -> AsyncConnection:
    conn = await engine.connect()
    try:
        got = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext('ingest:' || :source))"),
                {"source": source},
            )
        ).scalar_one()
        await conn.commit()
    except BaseException:
        await conn.close()
        raise
    if not got:
        await conn.close()
        raise SourceLocked(source)
    return conn


async def _release_lock(conn: AsyncConnection, source: str) -> None:
    try:
        await conn.execute(
            text("SELECT pg_advisory_unlock(hashtext('ingest:' || :source))"), {"source": source}
        )
        await conn.commit()
    except Exception:
        await conn.invalidate()  # dropping the session releases the advisory lock
    finally:
        await conn.close()


async def run_ingest(
    engine: AsyncEngine,
    settings: Settings,
    source: str,
    *,
    mode: Mode = "incremental",
    adapter: SourceAdapter | None = None,
    embedder: Embedder | None = None,
) -> RunSummary:
    factory = get_adapter_factory(source)  # raises UnknownSource
    config = _source_config(source)
    now = datetime.now(UTC)
    if adapter is None:
        adapter = factory(
            AdapterContext(settings=settings, engine=engine, config=config, mode=mode, now=now)
        )
    if not adapter.configured():
        log.warning("source_not_configured", source=source)
        raise SourceNotConfigured(source)

    lock_conn = await _acquire_lock(engine, source)
    try:
        if config.max_runs_per_day is not None:
            async with engine.connect() as conn:
                started = await store.runs_started_since(conn, source, now - timedelta(hours=24))
            if started >= config.max_runs_per_day:
                raise RunQuotaReached(source)
        async with engine.begin() as conn:
            run_id = await store.create_run(conn, source, mode, now)
        return await _run(engine, adapter, source, mode, run_id, now, settings, embedder)
    finally:
        await _release_lock(lock_conn, source)


async def _run(
    engine: AsyncEngine,
    adapter: SourceAdapter,
    source: str,
    mode: Mode,
    run_id: int,
    now: datetime,
    settings: Settings,
    embedder: Embedder | None,
) -> RunSummary:
    summary = RunSummary(source=source, run_id=run_id, status="running")
    errors: list[dict[str, Any]] = []
    skipped_samples: list[str] = []
    buffer: list[RawJob] = []
    yielded = 0
    adapter_failed = False
    try:
        async for raw in adapter.fetch():
            yielded += 1
            summary.fetched += 1
            buffer.append(raw)
            if len(buffer) >= store.BATCH_SIZE:
                await _process_buffer(
                    engine, run_id, source, buffer, now, summary, errors, skipped_samples
                )
                buffer = []
        if buffer:
            await _process_buffer(
                engine, run_id, source, buffer, now, summary, errors, skipped_samples
            )
    except Exception as exc:
        adapter_failed = True
        if len(errors) < MAX_ERRORS:
            errors.append(_error(None, exc))
        log.warning("ingest_adapter_failed", source=source, error_type=type(exc).__name__)

    # Embedding never fails ingestion: rows keep embedding NULL and the next pass retries (ING-09).
    embedded = 0
    embed_failed = False
    try:
        if embedder is None:
            embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
        embedded = await embed_missing(engine, embedder, settings.embed_model)
    except Exception as exc:
        embed_failed = True
        if len(errors) < MAX_ERRORS:
            errors.append({"source_job_id": None, "error": f"EmbedError:{type(exc).__name__}"})
        log.warning("ingest_embed_failed", source=source, error_type=type(exc).__name__)

    result: FetchResult = adapter.result()
    summary.calls_used = result.calls_used
    summary.failed += result.invalid_items
    in_scope = summary.inserted + summary.updated + summary.unchanged
    notes: dict[str, Any] = dict(result.extra_notes)
    notes["scope_counts"] = result.scope_counts
    notes["failed_scopes"] = result.failed_scopes
    notes["skipped_samples"] = skipped_samples
    notes["embedded"] = embedded
    summary.embedded = embedded
    if adapter_failed and yielded == 0:
        summary.status = "failed"
    elif adapter_failed or result.failed_scopes:
        summary.status = "partial"
    else:
        summary.status = "succeeded"
    # A source that failed every scope and produced nothing did not succeed either.
    if summary.status == "partial" and yielded == 0 and not result.complete_scopes:
        summary.status = "failed"

    if embed_failed and summary.status == "succeeded":
        summary.status = "partial"

    async with engine.begin() as conn:
        await store.finish_run(
            conn,
            run_id,
            status=summary.status,
            fetched=summary.fetched,
            inserted=summary.inserted,
            updated=summary.updated,
            unchanged=summary.unchanged,
            skipped=summary.skipped,
            failed=summary.failed,
            calls_used=summary.calls_used,
            in_scope=in_scope,
            errors=errors,
            notes=notes,
            now=datetime.now(UTC),
        )
    log.info(
        "ingest_run_finished",
        source=source,
        run_id=run_id,
        mode=mode,
        status=summary.status,
        fetched=summary.fetched,
        inserted=summary.inserted,
        updated=summary.updated,
        unchanged=summary.unchanged,
        skipped=summary.skipped,
        failed=summary.failed,
        calls_used=summary.calls_used,
    )
    return summary


__all__: Sequence[str] = [
    "RunQuotaReached",
    "RunSummary",
    "SourceLocked",
    "SourceNotConfigured",
    "run_ingest",
]
