"""Shared base for the Greenhouse, Lever and Ashby adapters (ING-13).

Each board is a scope, so one failing board never expires another board's jobs (T-02-53).
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import ClassVar, Literal

import httpx
import structlog
from pydantic import Field

from ranker.ingest.board_registry import (
    Ats,
    Board,
    BoardSeed,
    Host,
    InvalidBoardSlug,
    active_boards,
    record_board_check,
    valid_slug,
)
from ranker.ingest.http import SourceHTTPError, build_client, get_json
from ranker.ingest.normalize.html import clean_description
from ranker.ingest.normalize.location import normalize_location
from ranker.ingest.normalize.region import detect_remote_eligibility
from ranker.ingest.normalize.scope import classify_scope, classify_tech
from ranker.ingest.sources.base import AdapterContext, BaseSourceConfig, FetchResult, RawJob

log = structlog.get_logger("ranker.ingest.boards")

ELIGIBLE_REGIONS = frozenset({"india", "apac", "worldwide"})
MAX_SALARY = 1e10  # anything larger is garbage, not a salary


class BoardsConfig(BaseSourceConfig):
    seeds: list[str] = Field(default_factory=lambda: list[str]())
    min_in_scope_jobs: int = 5
    max_discovery_probes: int = 40
    request_interval_seconds: float = 1.0
    india_candidates: list[str] = Field(default_factory=lambda: list[str]())


def seeds_for(ats: Ats, cfg: BoardsConfig) -> list[BoardSeed]:
    out: list[BoardSeed] = []
    for slug in cfg.seeds:
        if valid_slug(slug):
            out.append(BoardSeed(ats=ats, slug=slug))
        else:
            log.warning("board_seed_invalid", ats=ats)
    return out


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
ScopeFlush = Callable[[], Awaitable[None]]


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
        self._scope_flush: ScopeFlush | None = None

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

    def set_scope_flush(self, hook: ScopeFlush | None) -> None:
        """Register a callback awaited after each board's jobs were yielded (pipeline flush)."""
        self._scope_flush = hook

    # ---------------------------------------------------------------------------------------
    async def fetch_boards(self) -> AsyncIterator[RawJob]:
        """Fetch all active boards; callers must ``sync_seeds`` first (each adapter's fetch).

        Memory contract (T-02-75): at most one board's postings are referenced at a time. The
        2026-11-22 production Greenhouse run (4,337 postings from 13 boards with full HTML)
        peaked at 1023 of 1024 MiB because a board's payload, parsed list and dict stayed alive
        while the next board was fetched. Now each board is fetched, parsed, its payload
        released, its jobs yielded, its dict cleared, and the scope-flush hook (the pipeline's
        normalise + upsert of the buffered postings) awaited, all before the next request. The
        peak is one board per source instead of the whole source.
        """
        self._result = FetchResult()
        boards = await active_boards(self._engine, self.ats)
        async with build_client() as client:
            for n, board in enumerate(boards):
                if n:
                    await self._sleep(self._cfg.request_interval_seconds)
                payload: object = None
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
                    payload = None  # never hold a bad payload across the next fetch
                    continue
                jobs, invalid = self.parse(payload, board.slug)
                del payload  # the raw board JSON is the largest object; drop it first
                self._result.invalid_items += invalid
                unique: dict[str, RawJob] = {}
                for job in jobs:
                    unique[job.source_job_id] = job  # last wins
                del jobs
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
                    del job  # the loop variable would keep the board's last posting alive
                unique.clear()
                del unique
                if self._scope_flush is not None:
                    await self._scope_flush()
        log.info(
            "source_fetch_done",
            source=self.name,
            boards=len(boards),
            failed=len(self._result.failed_scopes),
            calls=self._result.calls_used,
        )

    def result(self) -> FetchResult:
        return self._result
