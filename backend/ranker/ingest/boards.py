"""Board discovery, weekly re-check and the ``boards`` registry facade (ING-13, D-03).

Registry functions live in ``board_registry`` and the adapter base in ``sources.boardbase``;
they are re-exported here. Discovery never builds a URL from anything but an allow-listed host
and a slug that passed ``SLUG_RE`` (T-02-49), probes at most ``max_discovery_probes`` candidates
per run, one request per ``request_interval_seconds``.
"""

import asyncio
import importlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.config_loader import load_config
from ranker.ingest.board_registry import (
    ALL_ATS,
    ALLOWED_HOSTS,
    SLUG_RE,
    Ats,
    Board,
    BoardRow,
    BoardSeed,
    BoardStatus,
    Host,
    InvalidBoardSlug,
    active_boards,
    boards_report,
    checked_url,
    record_board_check,
    sync_seeds,
    valid_slug,
)
from ranker.ingest.expiry import expire_dropped_board
from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.sources.base import BaseSourceConfig, RawJob, SourcesConfig
from ranker.ingest.sources.boardbase import (
    BoardAdapter,
    BoardsConfig,
    Sleep,
    count_in_scope,
    is_in_scope_for_india,
    number,
    s,
    seeds_for,
)
from ranker.settings import Settings

log = structlog.get_logger("ranker.ingest.boards")

__all__ = [
    "ALLOWED_HOSTS",
    "SLUG_RE",
    "Board",
    "BoardAdapter",
    "BoardRow",
    "BoardSeed",
    "BoardsConfig",
    "InvalidBoardSlug",
    "active_boards",
    "boards_report",
    "checked_url",
    "count_in_scope",
    "is_in_scope_for_india",
    "number",
    "record_board_check",
    "s",
    "seeds_for",
    "sync_seeds",
    "valid_slug",
]

# --- discovery and weekly re-check (D-03) -------------------------------------------------

REPROBE_REJECTED_DAYS = 30
MAX_SCAN_CHARS = 5000  # per job description scanned for board links
MIN_COMPANY_JOBS = 3
_SLUG_PATTERN = r"([a-z0-9][a-z0-9-]{0,62})"
# Board links seen in job text: boards.greenhouse.io/<slug>, job-boards(.eu).greenhouse.io/<slug>,
# jobs.lever.co/<slug>, jobs.eu.lever.co/<slug>, jobs.ashbyhq.com/<slug>.
_URL_PATTERNS: list[tuple[Ats, Host, re.Pattern[str]]] = [
    ("greenhouse", "global", re.compile(r"boards\.greenhouse\.io/" + _SLUG_PATTERN)),
    ("greenhouse", "global", re.compile(r"job-boards(?:\.eu)?\.greenhouse\.io/" + _SLUG_PATTERN)),
    ("lever", "global", re.compile(r"jobs\.lever\.co/" + _SLUG_PATTERN)),
    ("lever", "eu", re.compile(r"jobs\.eu\.lever\.co/" + _SLUG_PATTERN)),
    ("ashby", "global", re.compile(r"jobs\.ashbyhq\.com/" + _SLUG_PATTERN)),
]
_NOT_SLUGS = frozenset({"embed", "v1", "api", "www", "jobs", "careers"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _new_entries() -> list[dict[str, Any]]:
    return []


class DiscoveryResult(BaseModel):
    added: list[dict[str, Any]] = Field(default_factory=_new_entries)
    rejected: list[dict[str, Any]] = Field(default_factory=_new_entries)
    dropped: list[dict[str, Any]] = Field(default_factory=_new_entries)
    errors: list[dict[str, Any]] = Field(default_factory=_new_entries)
    probes: int = 0
    rechecked: int = 0
    calls_used: int = 0

    def merge(self, other: DiscoveryResult) -> DiscoveryResult:
        return DiscoveryResult(
            added=self.added + other.added,
            rejected=self.rejected + other.rejected,
            dropped=self.dropped + other.dropped,
            errors=self.errors + other.errors,
            probes=self.probes + other.probes,
            rechecked=self.rechecked + other.rechecked,
            calls_used=self.calls_used + other.calls_used,
        )


class _Candidate(BaseModel):
    ats: Ats
    slug: str
    host: Host = "global"
    priority: int
    last_checked_at: datetime | None = None


def extract_board_links(text_: str) -> list[tuple[Ats, str, Host]]:
    """Board (ats, slug, host) triples found in ``text_`` (lowercased; linear-time regexes)."""
    low = text_.lower()
    found: list[tuple[Ats, str, Host]] = []
    for ats, host, pattern in _URL_PATTERNS:
        for m in pattern.finditer(low):
            slug = m.group(1).rstrip("-")
            if valid_slug(slug) and slug not in _NOT_SLUGS:
                found.append((ats, slug, host))
    return found


def company_slug_variants(normalized_name: str) -> list[str]:
    base = _NON_ALNUM.sub("-", normalized_name.lower()).strip("-")
    squashed = base.replace("-", "")
    if not squashed:
        return []
    variants = [squashed, base, squashed + "hq"]
    out: list[str] = []
    for v in variants:
        if v and valid_slug(v) and v not in out:
            out.append(v)
    return out


async def _known_pairs(engine: AsyncEngine) -> dict[tuple[str, str], tuple[str, datetime | None]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT ats, slug, status, last_checked_at FROM source_boards")
        )
        return {(r.ats, r.slug): (r.status, r.last_checked_at) for r in rows}


async def _extracted_links(engine: AsyncEngine) -> set[tuple[Ats, str, Host]]:
    out: set[tuple[Ats, str, Host]] = set()
    async with engine.connect() as conn:
        stream = await conn.stream(
            text("SELECT url, left(description_raw, :n) AS d FROM jobs WHERE status = 'active'"),
            {"n": MAX_SCAN_CHARS},
        )
        async for row in stream:
            out.update(extract_board_links(f"{row.url} {row.d or ''}"))
    return out


async def _company_names(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.normalized_name FROM companies c JOIN jobs j ON j.company_id = c.id "
                "WHERE j.status = 'active' AND NOT c.is_placeholder "
                "GROUP BY c.id, c.normalized_name HAVING count(*) >= :n ORDER BY c.normalized_name"
            ),
            {"n": MIN_COMPANY_JOBS},
        )
        return [r[0] for r in rows]


def _needs_probe(
    known: dict[tuple[str, str], tuple[str, datetime | None]], ats: str, slug: str, now: datetime
) -> bool:
    entry = known.get((ats, slug))
    if entry is None:
        return True
    status, last = entry
    if status != "rejected":
        return False
    return last is None or now - last >= timedelta(days=REPROBE_REJECTED_DAYS)


async def _candidates(engine: AsyncEngine, cfg: BoardsConfig, now: datetime) -> list[_Candidate]:
    known = await _known_pairs(engine)
    pool: dict[tuple[str, str], _Candidate] = {}

    def offer(ats: Ats, slug: str, host: Host, priority: int) -> None:
        if not valid_slug(slug) or not _needs_probe(known, ats, slug, now):
            return
        key = (ats, slug)
        prev = pool.get(key)
        last = known.get(key, ("", None))[1]
        if prev is None or priority < prev.priority:
            pool[key] = _Candidate(
                ats=ats, slug=slug, host=host, priority=priority, last_checked_at=last
            )

    for ats, slug, host in sorted(await _extracted_links(engine)):
        offer(ats, slug, host, 0)
    for slug in cfg.india_candidates:
        for ats in ALL_ATS:
            offer(ats, slug, "global", 1)
    for name in await _company_names(engine):
        for slug in company_slug_variants(name):
            for ats in ALL_ATS:
                offer(ats, slug, "global", 2)
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(
        pool.values(),
        key=lambda c: (c.priority, c.last_checked_at or epoch, c.ats, c.slug),
    )


def _ats_module(ats: Ats) -> Any:
    if ats not in ALL_ATS:
        raise ValueError("unknown ats")
    return importlib.import_module(f"ranker.ingest.sources.{ats}")


class _Probe(BaseModel):
    status_code: int  # 0 = transport error
    jobs: list[RawJob] = Field(default_factory=lambda: list[RawJob]())
    host: Host | None = None


async def _probe(
    client: httpx.AsyncClient,
    ats: Ats,
    slug: str,
    host: Host,
    *,
    sleep: Sleep,
    on_attempt: Callable[[], None],
) -> _Probe:
    module = _ats_module(ats)
    hosts: list[Host] = [host]
    if ats == "lever" and host == "global":
        hosts.append("eu")  # an EU site 404s on the global host
    last_status = 404
    for h in hosts:
        url, params = module.board_request(slug, h)
        try:
            payload = await get_json(
                client, url, params=params, max_attempts=3, sleep=sleep, on_attempt=on_attempt
            )
        except SourceHTTPError as exc:
            last_status = exc.status or 0
            if exc.status == 404:
                continue
            return _Probe(status_code=last_status)
        jobs, _ = module.parse_board(payload, slug)
        return _Probe(status_code=200, jobs=jobs, host=h if h != host else None)
    return _Probe(status_code=last_status)


async def _store_probed(
    engine: AsyncEngine,
    cand: _Candidate,
    *,
    status: BoardStatus,
    in_scope: int,
    reason: str | None,
    host: Host | None,
) -> None:
    """Insert a discovered board, or refresh a previously rejected one (never a live board)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO source_boards "
                "(ats, slug, host, status, origin, in_scope_count, last_checked_at, reason) "
                "VALUES (:ats, :slug, :host, :status, 'discovered', :n, :now, :reason) "
                "ON CONFLICT (ats, slug) DO UPDATE SET host = EXCLUDED.host, "
                "status = EXCLUDED.status, in_scope_count = EXCLUDED.in_scope_count, "
                "last_checked_at = EXCLUDED.last_checked_at, reason = EXCLUDED.reason, "
                "zero_checks = 0, updated_at = EXCLUDED.last_checked_at "
                "WHERE source_boards.status = 'rejected'"
            ),
            {
                "ats": cand.ats,
                "slug": cand.slug,
                "host": host or cand.host,
                "status": status,
                "n": in_scope,
                "now": now,
                "reason": reason,
            },
        )


async def discover_boards(
    engine: AsyncEngine,
    settings: Settings,
    cfg: BoardsConfig,
    client: httpx.AsyncClient,
    *,
    sleep: Sleep = asyncio.sleep,
) -> DiscoveryResult:
    """Probe new candidate boards; accept only those with >= N in-scope India/remote jobs."""
    result = DiscoveryResult()

    def attempt() -> None:
        result.calls_used += 1

    candidates = (await _candidates(engine, cfg, datetime.now(UTC)))[: cfg.max_discovery_probes]
    for n, cand in enumerate(candidates):
        if n:
            await sleep(cfg.request_interval_seconds)
        probe = await _probe(
            client, cand.ats, cand.slug, cand.host, sleep=sleep, on_attempt=attempt
        )
        result.probes += 1
        ref = {"ats": cand.ats, "slug": cand.slug}
        if probe.status_code == 404:
            await _store_probed(
                engine, cand, status="rejected", in_scope=0, reason="not_found", host=None
            )
            result.rejected.append({**ref, "reason": "not_found"})
        elif probe.status_code == 200:
            in_scope = count_in_scope(probe.jobs)
            if in_scope >= cfg.min_in_scope_jobs:
                await _store_probed(
                    engine, cand, status="active", in_scope=in_scope, reason=None, host=probe.host
                )
                result.added.append({**ref, "in_scope": in_scope})
            else:
                reason = f"below_threshold:{in_scope}"
                await _store_probed(
                    engine,
                    cand,
                    status="rejected",
                    in_scope=in_scope,
                    reason=reason,
                    host=probe.host,
                )
                result.rejected.append({**ref, "reason": reason})
        else:
            result.errors.append({**ref, "error": f"status:{probe.status_code}"})
    log.info(
        "board_discovery_done",
        probes=result.probes,
        added=len(result.added),
        rejected=len(result.rejected),
    )
    return result


async def recheck_boards(
    engine: AsyncEngine,
    settings: Settings,
    cfg: BoardsConfig,
    client: httpx.AsyncClient,
    *,
    sleep: Sleep = asyncio.sleep,
) -> DiscoveryResult:
    """Probe every seed/active board once; 404 or two zero-in-scope checks drop a board."""
    result = DiscoveryResult()

    def attempt() -> None:
        result.calls_used += 1

    first = True
    for ats in ALL_ATS:
        for board in await active_boards(engine, ats):
            if not first:
                await sleep(cfg.request_interval_seconds)
            first = False
            probe = await _probe(
                client, ats, board.slug, board.host, sleep=sleep, on_attempt=attempt
            )
            result.rechecked += 1
            ref = {"ats": ats, "slug": board.slug}
            if probe.status_code not in (200, 404):
                result.errors.append({**ref, "error": f"status:{probe.status_code}"})
                continue
            status = await record_board_check(
                engine,
                ats,
                board.slug,
                status_code=probe.status_code,
                in_scope_count=count_in_scope(probe.jobs),
                host=probe.host,
            )
            if status == "dropped":
                reason = "not_found" if probe.status_code == 404 else "zero_in_scope"
                result.dropped.append({**ref, "reason": reason})
    return result


def _boards_config(section: BaseSourceConfig | None) -> BoardsConfig:
    return BoardsConfig.model_validate(section.model_dump() if section else {})


async def run_board_maintenance(
    engine: AsyncEngine,
    settings: Settings,
    *,
    sleep: Sleep = asyncio.sleep,
    client: httpx.AsyncClient | None = None,
) -> DiscoveryResult:
    """Sync seeds, then discovery + weekly re-check; one ``ingest_runs`` row records it all."""
    from ranker.ingest import store  # lazy: store imports normalize.job, which imports sources

    sections = load_config("sources.yaml", SourcesConfig).root
    for ats in ALL_ATS:
        await sync_seeds(engine, seeds_for(ats, _boards_config(sections.get(ats))))
    cfg = _boards_config(sections.get("greenhouse"))  # discovery settings + india_candidates
    started = datetime.now(UTC)
    async with engine.begin() as conn:
        run_id = await store.create_run(conn, "board_discovery", "discovery", started)
    owns_client = client is None
    http = client or build_client()
    result = DiscoveryResult()
    status = "succeeded"
    expired_on_drop = 0
    try:
        result = await discover_boards(engine, settings, cfg, http, sleep=sleep)
        result = result.merge(await recheck_boards(engine, settings, cfg, http, sleep=sleep))
        if result.dropped:
            # A dropped board is no longer a complete scope: expire its jobs now, reversibly.
            async with engine.begin() as conn:
                for entry in result.dropped:
                    expired_on_drop += await expire_dropped_board(
                        conn, source=entry["ats"], slug=entry["slug"], run_id=run_id
                    )
        if result.errors:
            status = "partial"
    except Exception as exc:
        status = "failed"
        log.warning("board_maintenance_failed", error_type=type(exc).__name__)
        raise
    finally:
        if owns_client:
            await http.aclose()
        notes: dict[str, Any] = {
            "added": result.added,
            "rejected": result.rejected,
            "dropped": result.dropped,
            "rechecked": result.rechecked,
            "expired_on_drop": expired_on_drop,
        }
        async with engine.begin() as conn:
            await store.finish_run(
                conn,
                run_id,
                status=status,
                fetched=result.probes + result.rechecked,
                inserted=len(result.added),
                updated=0,
                unchanged=0,
                skipped=len(result.rejected),
                failed=len(result.errors),
                calls_used=result.calls_used,
                in_scope=0,
                errors=[
                    {"source_job_id": None, "error": f"{e['ats']}/{e['slug']}:{e['error']}"}
                    for e in result.errors
                ],
                notes=notes,
                now=datetime.now(UTC),
            )
    return result
