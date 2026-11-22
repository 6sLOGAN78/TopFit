"""`ranker` operator CLI.

Exit codes for ``ingest``: 0 succeeded or partial; 1 failed; 2 unknown source / usage;
3 nothing done (per-source lock held, runs-per-day cap reached, or source not configured).
"""

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Annotated, Literal, cast

import anyio.to_thread
import typer
from sqlalchemy import text

from ranker.config_loader import load_config
from ranker.db.engine import create_engine_from_settings
from ranker.embeddings.embedder import build_embedder
from ranker.embeddings.service import embed_missing, invalidate_other_models
from ranker.evals.ingest_eval import (
    EvalSetError,
    capture_samples,
    load_eval_set,
    report_markdown,
    score_normalisation,
)
from ranker.evals.ranking_eval import (
    VARIANTS,
    RankingEvalError,
    Variant,
    check_gate,
    load_judgments,
    load_personas,
    run_ranking_eval,
)
from ranker.evals.ranking_eval import report_markdown as ranking_markdown
from ranker.ingest.boards import boards_report, run_board_maintenance
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
boards_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Public ATS boards.")
app.add_typer(boards_app, name="boards")
eval_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Offline evaluations.")
app.add_typer(eval_app, name="eval")
runs_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Ingest run quality.")
app.add_typer(runs_app, name="runs")

DEFAULT_EVAL_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ingest_eval" / "postings.jsonl"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DATA_DIR = REPO_ROOT / "evals" / "data"
EVAL_RESULTS_DIR = REPO_ROOT / "evals" / "results"

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


async def _boards_report() -> int:
    engine = create_engine_from_settings(get_settings())
    try:
        rows = await boards_report(engine)
    finally:
        await engine.dispose()
    for row in rows:
        typer.echo(
            json.dumps(
                {
                    "ats": row.ats,
                    "slug": row.slug,
                    "host": row.host,
                    "status": row.status,
                    "origin": row.origin,
                    "in_scope_count": row.in_scope_count,
                    "last_checked_at": row.last_checked_at.isoformat()
                    if row.last_checked_at
                    else None,
                    "reason": row.reason,
                }
            )
        )
    return 0


async def _boards_discover() -> int:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        result = await run_board_maintenance(engine, settings)
    finally:
        await engine.dispose()
    typer.echo(
        json.dumps(
            {
                "added": result.added,
                "rejected": result.rejected,
                "dropped": result.dropped,
                "errors": len(result.errors),
                "probes": result.probes,
                "rechecked": result.rechecked,
                "calls_used": result.calls_used,
            }
        )
    )
    return 0


async def _runs_report(since_hours: int) -> int:
    engine = create_engine_from_settings(get_settings())
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT source, status, started_at, finished_at, fetched, in_scope, inserted, "
                    "updated, expired AS closed, duplicates_grouped, ghost_flagged, reposts "
                    "FROM ingest_runs WHERE started_at >= :since ORDER BY started_at, id"
                ),
                {"since": since},
            )
            result = [dict(r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]
    finally:
        await engine.dispose()
    for row in result:
        typer.echo(json.dumps(row, default=str))
    return 0


async def _eval_capture(source: str, n: int, out: Path) -> int:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        written = await capture_samples(engine, settings, source, n, out)
    finally:
        await engine.dispose()
    typer.echo(json.dumps({"source": source, "written": written, "out": str(out)}))
    return 0


def _git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
    except OSError, subprocess.SubprocessError:
        return "nogit"
    return out.stdout.strip() or "nogit"


def _write_result(out_dir: Path, stamp: str, payload: dict[str, object]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}_{_git_short_sha()}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


async def _eval_ranking(
    personas_path: Path,
    judgments_path: Path,
    as_of: datetime,
    variant: str,
    baseline: Path | None,
    max_drop: float,
    ranking_config_path: Path | None,
    out_dir: Path,
) -> int:
    personas = load_personas(personas_path)
    judgments = load_judgments(judgments_path)
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        embedder = await anyio.to_thread.run_sync(partial(build_embedder, settings))
        report = await run_ranking_eval(
            engine,
            embedder,
            settings,
            personas,
            judgments,
            as_of=as_of,
            variant=cast(Variant, variant),
            ranking_config_path=ranking_config_path,
        )
    finally:
        await engine.dispose()
    gate = check_gate(report, baseline, max_drop)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {"timestamp": stamp, "report": report.model_dump(mode="json"), **gate.as_json()}
    await anyio.to_thread.run_sync(_write_result, out_dir, stamp, payload)
    typer.echo(ranking_markdown(report))
    if gate.status == "no_baseline":
        typer.echo("notice: no baseline found, gate skipped")
    typer.echo(json.dumps(gate.as_json()))
    return EXIT_FAILED if gate.status == "fail" else 0


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


@boards_app.command("report")
def boards_report_cmd() -> None:
    """Print one JSON line per known board (status, origin, in-scope count, reason)."""
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_boards_report()))


@boards_app.command("discover")
def boards_discover_cmd() -> None:
    """Run board discovery plus the weekly re-check and print the summary."""
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_boards_discover()))


@eval_app.command("ingest")
def eval_ingest_cmd(
    fixture: Annotated[Path, typer.Option(help="Labelled postings JSONL")] = DEFAULT_EVAL_FIXTURE,
    markdown: Annotated[bool, typer.Option("--markdown", help="Print a markdown table")] = False,
) -> None:
    """Score normalize_job against the gold labels (no network, no LLM)."""
    if not fixture.is_file():
        typer.echo(json.dumps({"error": "FixtureNotFound", "path": str(fixture)}), err=True)
        raise typer.Exit(EXIT_USAGE)
    try:
        report = score_normalisation(load_eval_set(fixture))
    except EvalSetError as exc:
        typer.echo(json.dumps({"error": "InvalidFixture", "detail": str(exc)}), err=True)
        raise typer.Exit(EXIT_USAGE) from None
    typer.echo(report_markdown(report) if markdown else report.model_dump_json())


@eval_app.command("capture")
def eval_capture_cmd(
    source: Annotated[str, typer.Argument(help="Source name, e.g. greenhouse")],
    n: Annotated[int, typer.Option(min=1, max=200)] = 10,
    out: Annotated[Path, typer.Option(help="JSONL to append unlabelled records to")] = (
        DEFAULT_EVAL_FIXTURE
    ),
) -> None:
    """Fetch SOURCE once through its adapter and append N scrubbed, unlabelled postings."""
    try:
        get_adapter_factory(source)
    except UnknownSource:
        typer.echo(json.dumps({"source": source, "error": "UnknownSource"}), err=True)
        raise typer.Exit(EXIT_USAGE) from None
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_eval_capture(source, n, out)))


@eval_app.command("ranking")
def eval_ranking_cmd(
    personas: Annotated[Path, typer.Option(help="Personas JSONL")] = EVAL_DATA_DIR
    / "personas.jsonl",
    judgments: Annotated[Path, typer.Option(help="Graded judgments JSONL")] = EVAL_DATA_DIR
    / "judgments.jsonl",
    as_of: Annotated[
        str | None, typer.Option(help="ISO timestamp; default from snapshot.meta.json")
    ] = None,
    baseline: Annotated[Path | None, typer.Option(help="baseline.json to gate against")] = None,
    max_drop: Annotated[float, typer.Option(min=0.0, help="Max allowed nDCG@10 drop")] = 0.02,
    ranking_config: Annotated[
        Path | None, typer.Option(help="Alternative ranking.yaml to evaluate (D-12)")
    ] = None,
    out: Annotated[Path, typer.Option(help="Directory for the result JSON")] = EVAL_RESULTS_DIR,
    variant: Annotated[
        str, typer.Option(help="lex | vec | hybrid | hybrid_signals")
    ] = "hybrid_signals",
    mode: Annotated[str, typer.Option(help="Only no-llm is supported")] = "no-llm",
) -> None:
    """Score the production search path against graded judgments (no LLM, exact vectors)."""

    def usage(error: str, **extra: str) -> typer.Exit:
        typer.echo(json.dumps({"error": error, **extra}), err=True)
        return typer.Exit(EXIT_USAGE)

    if mode != "no-llm":
        raise usage("UnsupportedMode", mode=mode)
    if variant not in VARIANTS:
        raise usage("UnknownVariant", variant=variant)
    for path in (personas, judgments):
        if not path.is_file():
            raise usage("FileNotFound", path=str(path))
    if ranking_config is not None and not ranking_config.is_file():
        raise usage("FileNotFound", path=str(ranking_config))
    as_of_text = as_of
    if as_of_text is None:
        meta = EVAL_DATA_DIR / "snapshot.meta.json"
        if not meta.is_file():
            raise usage("AsOfRequired")
        as_of_text = str(json.loads(meta.read_text(encoding="utf-8"))["as_of"])
    try:
        as_of_dt = datetime.fromisoformat(as_of_text)
    except ValueError:
        raise usage("InvalidAsOf", value=as_of_text) from None
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=UTC)
    configure_logging(get_settings().log_level)
    try:
        code = asyncio.run(
            _eval_ranking(
                personas, judgments, as_of_dt, variant, baseline, max_drop, ranking_config, out
            )
        )
    except RankingEvalError as exc:
        raise usage("InvalidEvalFile", detail=str(exc)) from None
    raise typer.Exit(code)


@runs_app.command("report")
def runs_report_cmd(
    since_hours: Annotated[int, typer.Option(min=1)] = 24,
) -> None:
    """Print one JSON line per recent ingest run with the D-20 quality counters."""
    configure_logging(get_settings().log_level)
    raise typer.Exit(asyncio.run(_runs_report(since_hours)))


if __name__ == "__main__":
    app()
