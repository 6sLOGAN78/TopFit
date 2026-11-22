"""Registry of public ATS boards (``source_boards``) shared by adapters and discovery (ING-13).

Imports nothing from the sources package, so adapters and ``ranker.ingest.boards`` can both use
it without an import cycle. A dropped board is never resurrected by a seed sync.

Security (T-02-49): a slug must match ``SLUG_RE`` and a URL is built only from a fixed host in
``ALLOWED_HOSTS`` plus the validated slug; nothing else ever chooses where a request goes.
"""

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Ats = Literal["greenhouse", "lever", "ashby"]
Host = Literal["global", "eu"]
BoardStatus = Literal["seed", "active", "rejected", "dropped"]
ALL_ATS: tuple[Ats, ...] = ("greenhouse", "lever", "ashby")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ALLOWED_HOSTS = frozenset(
    {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}
)


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
