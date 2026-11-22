"""`ranker` operator CLI.

Exit codes for ``ingest``: 0 succeeded or partial; 1 failed; 2 unknown source / usage;
3 nothing done (per-source lock held, runs-per-day cap reached, or source not configured).
"""

import asyncio
import json
from functools import partial
from typing import Annotated, Literal

import anyio.to_thread
import typer

from ranker.db.engine import create_engine_from_settings
from ranker.embeddings.embedder import build_embedder
from ranker.embeddings.service import embed_missing, invalidate_other_models
from ranker.ingest.pipeline import (
    RunQuotaReached,
    SourceLocked,
    SourceNotConfigured,
    run_ingest,
)
from ranker.ingest.sources import UnknownSource
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


if __name__ == "__main__":
    app()
