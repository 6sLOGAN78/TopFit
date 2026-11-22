"""Registry and shared adapter base for public ATS boards (ING-13, D-03).

Boards (Greenhouse, Lever, Ashby) live in ``source_boards``. Seeds come from sources.yaml and are
synced at the start of every board fetch. A dropped board is never resurrected by a sync.

Security (T-02-49): a slug must match ``SLUG_RE`` and a URL is built only from a fixed host in
``ALLOWED_HOSTS`` plus the validated slug; nothing else ever chooses where a request goes.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import ClassVar, Literal

import httpx
import structlog
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.normalize.location import normalize_location
from ranker.ingest.normalize.region import detect_remote_eligibility
from ranker.ingest.normalize.scope import classify_scope, classify_tech
from ranker.ingest.sources.base import (
    AdapterContext,
    BaseSourceConfig,
    FetchResult,
    RawJob,
)

log = structlog.get_logger("ranker.ingest.boards")

Ats = Literal["greenhouse", "lever", "ashby"]
Host = Literal["global", "eu"]
BoardStatus = Literal["seed", "active", "rejected", "dropped"]
ALL_ATS: tuple[Ats, ...] = ("greenhouse", "lever", "ashby")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ALLOWED_HOSTS = frozenset(
    {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}
)
ELIGIBLE_REGIONS = frozenset({"india", "apac", "worldwide"})
MAX_SALARY = 1e10  # anything larger is garbage, not a salary


class InvalidBoardSlug(ValueError):
    pass


def valid_slug(slug: object) -> bool:
    return isinstance(slug, str) and SLUG_RE.fullmatch(slug) is not None


def checked_url(host: str, path: str, slug: str) -> str:
    """Build ``https://{host}{path.format(slug)}`` for an allow-listed host and a valid slug."""
    if host not in ALLOWED_HOSTS:
        raise ValueError("host not allow-listed")
    if not valid_slug(slug):
        raise InvalidBoardSlug("invalid board slug")
    return f"https://{host}{path.format(slug=slug)}"


class BoardsConfig(BaseSourceConfig):
    seeds: list[str] = Field(default_factory=lambda: list[str]())
    min_in_scope_jobs: int = 5
    max_discovery_probes: int = 40
    request_interval_seconds: float = 1.0
    india_candidates: list[str] = Field(default_factory=lambda: list[str]())


class BoardSeed(BaseModel):
    ats: Ats
    slug: str
    host: Host = "global"

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not valid_slug(v):
            raise ValueError("invalid board slug")
        return v


class Board(BaseModel):
    ats: Ats
    slug: str
    host: Host
    status: BoardStatus
    origin: Literal["seed", "discovered"]
    in_scope_count: int
    zero_checks: int
    last_checked_at: datetime | None
    reason: str | None


BoardRow = Board


def seeds_for(ats: Ats, cfg: BoardsConfig) -> list[BoardSeed]:
    out: list[BoardSeed] = []
    for slug in cfg.seeds:
        if valid_slug(slug):
            out.append(BoardSeed(ats=ats, slug=slug))
        else:
            log.warning("board_seed_invalid", ats=ats)
    return out


_BOARD_COLS = (  # module constant, never user input
    "ats, slug, host, status, origin, in_scope_count, zero_checks, last_checked_at, reason"
)


async def sync_seeds(engine: AsyncEngine, seeds: Iterable[BoardSeed]) -> int:
    """Insert missing seeds with status ``seed``; existing rows (even dropped) are untouched."""
    inserted = 0
    async with engine.begin() as conn:
        for seed in seeds:
            res = await conn.execute(
                text(
                    "INSERT INTO source_boards (ats, slug, host, status, origin) "
                    "VALUES (:ats, :slug, :host, 'seed', 'seed') ON CONFLICT (ats, slug) DO NOTHING"
                ),
                {"ats": seed.ats, "slug": seed.slug, "host": seed.host},
            )
            inserted += int(res.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
    return inserted


async def active_boards(engine: AsyncEngine, ats: Ats) -> list[Board]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT " + _BOARD_COLS + " FROM source_boards "  # noqa: S608
                "WHERE ats = :ats AND status IN ('seed','active') ORDER BY slug"
            ),
            {"ats": ats},
        )
        return [Board.model_validate(dict(r._mapping)) for r in rows]  # pyright: ignore[reportPrivateUsage]


async def boards_report(engine: AsyncEngine) -> list[Board]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT " + _BOARD_COLS + " FROM source_boards ORDER BY ats, slug"  # noqa: S608
            )
        )
        return [Board.model_validate(dict(r._mapping)) for r in rows]  # pyright: ignore[reportPrivateUsage]


ZERO_CHECKS_TO_DROP = 2


async def record_board_check(
    engine: AsyncEngine,
    ats: Ats,
    slug: str,
    *,
    status_code: int,
    in_scope_count: int,
    host: Host | None = None,
    count_zero: bool = True,
) -> str:
    """Apply one board check. 404 drops at once; two consecutive zero-in-scope checks drop.

    ``count_zero=False`` (used by the daily adapter fetches) never advances the zero counter, so
    only the weekly re-check (D-03) can drop a board for being empty. Returns the new status, or
    ``"unknown"`` when the board is not in the registry.
    """
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, zero_checks FROM source_boards "
                        "WHERE ats = :ats AND slug = :slug FOR UPDATE"
                    ),
                    {"ats": ats, "slug": slug},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return "unknown"
        status: str = row["status"]
        zero: int = row["zero_checks"]
        reason: str | None = None
        if status == "dropped":
            return status
        if status_code == 404:
            status, reason = "dropped", "not_found"
        elif status_code == 200:
            if in_scope_count <= 0:
                if count_zero:
                    zero += 1
                if zero >= ZERO_CHECKS_TO_DROP:
                    status, reason = "dropped", "zero_in_scope"
                elif status == "seed":
                    status = "active"
            else:
                zero = 0
                if status != "rejected":
                    status = "active"
        else:
            return status
        await conn.execute(
            text(
                "UPDATE source_boards SET status = :status, zero_checks = :zero, "
                "in_scope_count = :n, last_checked_at = :now, updated_at = :now, "
                "reason = :reason, host = COALESCE(:host, host) "
                "WHERE ats = :ats AND slug = :slug"
            ),
            {
                "status": status,
                "zero": zero,
                "n": max(in_scope_count, 0) if status_code == 200 else 0,
                "now": now,
                "reason": reason,
                "host": host,
                "ats": ats,
                "slug": slug,
            },
        )
        return status


def is_in_scope_for_india(raw: RawJob) -> bool:
    """A tech job located in India, or remote and open to India/APAC/worldwide candidates."""
    if not classify_tech(raw.title, raw.category_hint).in_scope:
        return False
    description = clean_description(
        raw.description_raw,
        is_html=raw.description_is_html,
        entity_escaped=raw.description_entity_escaped,
    )
    location = normalize_location(
        raw.location_text,
        parts=raw.location_parts,
        workplace_hint=raw.workplace_hint,
        country_hint=raw.country_hint,
        description=description,
    )
    if not classify_scope(raw.title, raw.category_hint, location).in_scope:
        return False
    if location.country_code == "IN":
        return True
    region = detect_remote_eligibility(
        region_hint=raw.region_hint,
        description=description,
        country_code=location.country_code,
        remote_type=location.remote_type,
    )
    return region.remote_eligibility in ELIGIBLE_REGIONS


def count_in_scope(raw_jobs: Iterable[RawJob]) -> int:
    return sum(1 for job in raw_jobs if is_in_scope_for_india(job))


# --- shared helpers for the three board adapters ------------------------------------------


def s(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def number(value: object, *, maximum: float = MAX_SALARY) -> float | None:
    """A finite, sane, non-negative number or None (bool and overflow-sized values rejected)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        v = float(value)
    except OverflowError:
        return None
    if v != v or v < 0 or v > maximum:  # NaN, negative, absurd
        return None
    return v


def company_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


Sleep = Callable[[float], Awaitable[None]]


class BoardAdapter:
    """Fetch every active board of one ATS; each board is a scope (T-02-53)."""

    name: ClassVar[str]
    ats: ClassVar[Ats]
    expiry_policy: Literal["absence", "ttl"] = "absence"

    def __init__(self, ctx: AdapterContext, *, sleep: Sleep = asyncio.sleep) -> None:
        self._cfg = BoardsConfig.model_validate(ctx.config.model_dump())
        self._engine = ctx.engine
        self._sleep = sleep
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _count_attempt(self) -> None:
        self._result.calls_used += 1

    # Subclass hooks ------------------------------------------------------------------------
    def request(self, board: Board) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def parse(self, payload: object, slug: str) -> tuple[list[RawJob], int]:
        raise NotImplementedError

    def payload_ok(self, payload: object) -> bool:
        """True when ``payload`` has the shape of a board (an empty board still counts)."""
        if not isinstance(payload, dict):
            return False
        return isinstance(payload.get("jobs"), list)  # pyright: ignore[reportUnknownMemberType]

    async def get_payload(
        self, client: httpx.AsyncClient, board: Board
    ) -> tuple[object, Host | None]:
        url, params = self.request(board)
        payload = await get_json(
            client, url, params=params, sleep=self._sleep, on_attempt=self._count_attempt
        )
        return payload, None

    # ---------------------------------------------------------------------------------------
    async def fetch_boards(self) -> AsyncIterator[RawJob]:
        """Fetch all active boards; callers must ``sync_seeds`` first (each adapter's fetch)."""
        self._result = FetchResult()
        boards = await active_boards(self._engine, self.ats)
        async with build_client() as client:
            for n, board in enumerate(boards):
                if n:
                    await self._sleep(self._cfg.request_interval_seconds)
                try:
                    payload, host = await self.get_payload(client, board)
                except InvalidBoardSlug:
                    self._result.failed_scopes[board.slug] = "InvalidSlug:None"
                    continue
                except SourceHTTPError as exc:
                    self._result.failed_scopes[board.slug] = f"{exc.error_type}:{exc.status}"
                    if exc.status == 404:
                        await record_board_check(
                            self._engine,
                            self.ats,
                            board.slug,
                            status_code=404,
                            in_scope_count=0,
                            count_zero=False,
                        )
                    log.warning("board_fetch_failed", source=self.name, board=board.slug)
                    continue
                if not self.payload_ok(payload):
                    self._result.failed_scopes[board.slug] = "InvalidPayload:None"
                    continue
                jobs, invalid = self.parse(payload, board.slug)
                self._result.invalid_items += invalid
                unique: dict[str, RawJob] = {}
                for job in jobs:
                    unique[job.source_job_id] = job  # last wins
                self._result.complete_scopes.add(board.slug)
                self._result.scope_counts[board.slug] = len(unique)
                await record_board_check(
                    self._engine,
                    self.ats,
                    board.slug,
                    status_code=200,
                    in_scope_count=count_in_scope(unique.values()),
                    host=host,
                    count_zero=False,
                )
                for job in unique.values():
                    yield job
        log.info(
            "source_fetch_done",
            source=self.name,
            boards=len(boards),
            failed=len(self._result.failed_scopes),
            calls=self._result.calls_used,
        )

    def result(self) -> FetchResult:
        return self._result
