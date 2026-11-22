"""`ranker` operator CLI.

Exit codes for ``ingest``: 0 succeeded or partial; 1 failed; 2 unknown source / usage;
3 nothing done (per-source lock held, runs-per-day cap reached, or source not configured).
"""

import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Literal

import anyio.to_thread
import typer

from ranker.config_loader import load_config
from ranker.db.engine import create_engine_from_settings
from ranker.embeddings.embedder import build_embedder
from ranker.embeddings.service import embed_missing, invalidate_other_models
from ranker.ingest.expiry import ExpiryConfig, expire_ttl, purge_source
from ranker.ingest.pipeline import (
    RunQuotaReached,
    SourceLocked,
    SourceNotConfigured,
    run_ingest,
)
from ranker.ingest.sources import SourcesConfig, UnknownSource, get_adapter_factory
from ranker.observability import configure_logging
from ranker.settings import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOTHING_DONE = 3


async def _ingest(source: str, mode: Literal["incremental", "backfill"]) -> int:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        summary = await run_ingest(engine, settings, source, mode=mode)
    except UnknownSource:
        typer.echo(json.dumps({"source": source, "error": "UnknownSource"}), err=True)
        return EXIT_USAGE
    except (SourceLocked, RunQuotaReached, SourceNotConfigured) as exc:
        typer.echo(json.dumps({"source": source, "error": type(exc).__name__}), err=True)
        return EXIT_NOTHING_DONE
    finally:
        await engine.dispose()
    typer.echo(
        json.dumps(
            {
                "source": summary.source,
                "run_id": summary.run_id,
                "status": summary.status,
                "fetched": summary.fetched,
                "inserted": summary.inserted,
                "updated": summary.updated,
                "unchanged": summary.unchanged,
                "skipped": summary.skipped,
                "failed": summary.failed,
                "calls_used": summary.calls_used,
                "embedded": summary.embedded,
                "expired": summary.expired,
                "duplicates_grouped": summary.duplicates_grouped,
                "ghost_flagged": summary.ghost_flagged,
                "reposts": summary.reposts,
            }
        )
    )
    return EXIT_FAILED if summary.status == "failed" else 0


async def _embed(model: str | None, invalidate: bool, batch_size: int) -> int:
    settings = get_settings()
    model_name = model or settings.embed_model
    engine = create_engine_from_settings(settings)
    try:
        if invalidate:
            await invalidate_other_models(engine, model_name)
        embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
        embedded = await embed_missing(engine, embedder, model_name, batch_size=batch_size)
    finally:
        await engine.dispose()
    typer.echo(json.dumps({"embedded": embedded, "model": model_name}))
    return 0


async def _purge(source: str) -> int:
    engine = create_engine_from_settings(get_settings())
    try:
        purged = await purge_source(engine, source)
    finally:
        await engine.dispose()
    typer.echo(json.dumps({"purged": purged}))
    return 0


async def _expire_ttl(source: str) -> int:
    section = load_config("sources.yaml", SourcesConfig).root.get(source)
    if section is None or section.expiry_policy != "ttl":
        typer.echo(json.dumps({"source": source, "error": "NotATtlSource"}), err=True)
        return EXIT_USAGE
    engine = create_engine_from_settings(get_settings())
    try:
        expired = await expire_ttl(
            engine,
            source=source,
            cfg=ExpiryConfig.model_validate(section.model_dump()),
            now=datetime.now(UTC),
        )
    finally:
        await engine.dispose()
    typer.echo(json.dumps({"expired": expired}))
    return 0


@app.callback()
def main() -> None:
    """Ranker operator commands."""


@app.command()
def ingest(
    source: Annotated[str, typer.Argument(help="Source name, e.g. remotive")],
    mode: Annotated[str, typer.Option(help="incremental or backfill")] = "incremental",
) -> None:
    """Run one ingest pass for SOURCE."""
    if mode not in ("incremental", "backfill"):
        typer.echo(json.dumps({"error": "InvalidMode"}), err=True)
        raise typer.Exit(EXIT_USAGE)
    configure_logging(get_settings().log_level)
    code = asyncio.run(_ingest(source, mode))
    raise typer.Exit(code)


@app.command()
def embed(
    missing: Annotated[
        bool, typer.Option("--missing", help="Embed rows with NULL embedding")
    ] = False,
    all_: Annotated[
        bool, typer.Option("--all", help="Re-embed rows embedded by a different model")
    ] = False,
    model: Annotated[str | None, typer.Option(help="Model name to tag and migrate to")] = None,
    batch_size: Annotated[int, typer.Option(min=1, max=256)] = 32,
) -> None:
    """Backfill embeddings (--missing) or migrate them to another model (--all --model X)."""
    if missing == all_:
        typer.echo(json.dumps({"error": "UseExactlyOneOf--missing/--all"}), err=True)
        raise typer.Exit(EXIT_USAGE)
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_embed(model, all_, batch_size)))


@app.command("purge-source")
def purge_source_cmd(
    source: Annotated[str, typer.Argument(help="Source name, e.g. adzuna")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the irreversible purge")] = False,
) -> None:
    """Irreversibly remove all content of SOURCE (provider termination / ToS)."""
    try:
        get_adapter_factory(source)
    except UnknownSource:
        typer.echo(json.dumps({"source": source, "error": "UnknownSource"}), err=True)
        raise typer.Exit(EXIT_USAGE) from None
    if not yes:
        typer.echo(json.dumps({"source": source, "error": "RequiresYes"}), err=True)
        raise typer.Exit(EXIT_USAGE)
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_purge(source)))


@app.command("expire-ttl")
def expire_ttl_cmd(
    source: Annotated[str, typer.Argument(help="TTL-policy source, e.g. adzuna")],
) -> None:
    """Run the age-based expiry for a TTL-policy SOURCE."""
    try:
        get_adapter_factory(source)
    except UnknownSource:
        typer.echo(json.dumps({"source": source, "error": "UnknownSource"}), err=True)
        raise typer.Exit(EXIT_USAGE) from None
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_expire_ttl(source)))


if __name__ == "__main__":
    app()
