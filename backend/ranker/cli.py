"""`ranker` operator CLI.

Exit codes for ``ingest``: 0 succeeded or partial; 1 failed; 2 unknown source / usage;
3 nothing done (per-source lock held, runs-per-day cap reached, or source not configured).
"""

import asyncio
import json
from typing import Annotated, Literal

import typer

from ranker.db.engine import create_engine_from_settings
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
            }
        )
    )
    return EXIT_FAILED if summary.status == "failed" else 0


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


if __name__ == "__main__":
    app()
