"""Small shared helpers for the free-API adapters (strings, epoch/ISO dates, safe numbers)."""

import asyncio
import math
from datetime import UTC, datetime
from typing import cast

MAX_NUMBER = 1e12  # untrusted salary figures above this are dropped, never stored
_MIN_EPOCH = 946684800  # 2000-01-01
_MAX_EPOCH = 4102444800  # 2100-01-01


async def sleep(seconds: float) -> None:
    """Indirection so tests can replace waiting (looked up at call time)."""
    await asyncio.sleep(seconds)


def s(value: object, limit: int = 2000) -> str | None:
    if isinstance(value, str):
        value = value.strip()[:limit]
        return value or None
    return None


def first_str(value: object) -> str | None:
    if isinstance(value, list):
        for entry in cast(list[object], value):
            text = s(entry)
            if text:
                return text
        return None
    return s(value)


def str_list(value: object, *, max_items: int = 50) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for entry in cast(list[object], value)[:max_items]:
            text = s(entry, 100)
            if text:
                out.append(text)
    return out


def parse_iso(value: object) -> datetime | None:
    raw = s(value)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not (_MIN_EPOCH <= value <= _MAX_EPOCH):
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def safe_amount(value: object) -> float | None:
    """A positive finite number below MAX_NUMBER, else None (0 and junk mean 'no salary')."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f) or f <= 0 or f > MAX_NUMBER:
        return None
    return f
