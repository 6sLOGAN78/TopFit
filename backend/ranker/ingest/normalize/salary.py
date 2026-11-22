# ruff: noqa: RUF001
"""Salary text parsing and INR-annual conversion (NORM-04, D-12, D-13).

Pure functions. The only I/O is ``load_config("fx.yaml")``. All regexes are linear and
inputs are length-capped (T-02-18).
"""

import re
from datetime import date
from decimal import Decimal, DecimalException
from typing import Literal

from pydantic import BaseModel

from ranker.config_loader import load_config

Period = Literal["year", "month", "hour"]

MAX_SALARY_TEXT = 300
INR_MIN_ANNUAL = Decimal(50_000)
INR_MAX_ANNUAL = Decimal(50_000_000)
# Bare-k INR values below this are read as monthly (research: bare-k rule).
BARE_K_MONTHLY_BELOW = Decimal(200_000)
# Poison-pill guard: source figures beyond this are treated as unknown, never converted.
MAX_RAW_VALUE = Decimal(10**12)


class FxConfig(BaseModel):
    as_of: date
    rates_to_inr: dict[str, Decimal]
    period_multiplier: dict[str, int]


class ParsedSalary(BaseModel):
    min: Decimal | None = None
    max: Decimal | None = None
    currency: str | None = None
    period: Period | None = None
    is_estimate: bool = False
    min_inr_annual: Decimal | None = None
    max_inr_annual: Decimal | None = None
    fx_as_of: date | None = None
    out_of_bounds: bool = False
    source_text: str | None = None


_UNIT_MULT: dict[str, Decimal] = {
    "k": Decimal(1000),
    "lpa": Decimal(100_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "l": Decimal(100_000),
    "cr": Decimal(10_000_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
}
_INR_UNITS = frozenset(_UNIT_MULT) - {"k"}

_UNIT = r"(?:lpa|lakhs?|lacs?|crores?|cr|l|k)"
_TOKEN_RE = re.compile(
    rf"(?P<num>\d+(?:,\d+)*(?:\.\d+)?)\s*(?P<unit>{_UNIT})?(?![a-z])", re.IGNORECASE
)
# (en/em dashes are intentional range separators)
_RANGE_SEP_RE = re.compile(
    r"^\s*(?:[-–—]|to)\s*(?:(?:₹|rs\.?|inr|usd|us\$|\$|€|eur|£|gbp)\s*)?$",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\([^)]*\)")
_ISO_RE = re.compile(r"\b(usd|eur|gbp|inr|sgd|aud|cad|aed)\b", re.IGNORECASE)
_RS_RE = re.compile(r"\brs\b\.?", re.IGNORECASE)
_MONTH_RE = re.compile(r"/\s*(?:month|mo)\b|per\s+month|\bpm\b|p\.m\.|monthly", re.IGNORECASE)
_HOUR_RE = re.compile(r"/\s*h(?:ou)?r\b|per\s+hour|hourly", re.IGNORECASE)
_YEAR_RE = re.compile(
    r"\bp\.a\.?|per\s+annum|annual|yearly|/\s*(?:year|yr)\b|per\s+year|\ba\s+year\b|\blpa\b",
    re.IGNORECASE,
)
_PLUS_RE = re.compile(r"\+\s*[a-z]")


def _fx() -> FxConfig:
    return load_config("fx.yaml", FxConfig)


def to_inr_annual(
    value: Decimal | None, currency: str | None, period: Period | None
) -> tuple[Decimal | None, date | None]:
    """Convert a figure to INR per year. Returns (value, fx_as_of); INR needs no rate."""
    if value is None or currency is None or period is None:
        return None, None
    fx = _fx()
    mult = fx.period_multiplier.get(period)
    if mult is None:
        return None, None
    cur = currency.upper()
    try:
        if cur == "INR":
            return value * mult, None
        rate = fx.rates_to_inr.get(cur)
        if rate is None:
            return None, None
        return value * rate * mult, fx.as_of
    except DecimalException:
        return None, None


def _to_decimal(v: Decimal | int | float | str | None) -> Decimal | None:
    if v is None:
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
        if not d.is_finite() or abs(d) > MAX_RAW_VALUE:
            return None
    except DecimalException:
        return None
    return d


def salary_from_numbers(
    *,
    min: Decimal | int | float | str | None,  # noqa: A002
    max: Decimal | int | float | str | None,  # noqa: A002
    currency: str | None,
    period: Period | None,
    is_estimate: bool = False,
    source_text: str | None = None,
) -> ParsedSalary:
    lo = _to_decimal(min)
    hi = _to_decimal(max)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    cur = currency.upper() if currency else None
    lo_inr, as_of = to_inr_annual(lo, cur, period)
    hi_inr, as_of_hi = to_inr_annual(hi, cur, period)
    as_of = as_of or as_of_hi
    out_of_bounds = False
    for v in (lo_inr, hi_inr):
        if v is not None and not (INR_MIN_ANNUAL <= v <= INR_MAX_ANNUAL):
            out_of_bounds = True
    if out_of_bounds:
        lo_inr = hi_inr = None
        as_of = None
    return ParsedSalary(
        min=lo,
        max=hi,
        currency=cur,
        period=period,
        is_estimate=is_estimate,
        min_inr_annual=lo_inr,
        max_inr_annual=hi_inr,
        fx_as_of=as_of,
        out_of_bounds=out_of_bounds,
        source_text=source_text,
    )


def _detect_currency(text: str, units: list[str]) -> str | None:
    iso = _ISO_RE.search(text)
    if iso:
        return iso.group(1).upper()
    if "₹" in text or _RS_RE.search(text):
        return "INR"
    if any(u in _INR_UNITS for u in units):
        return "INR"
    if "$" in text:
        return "USD"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    return None


def _detect_period(text: str, units: list[str]) -> Period | None:
    if _HOUR_RE.search(text):
        return "hour"
    if _MONTH_RE.search(text):
        return "month"
    if _YEAR_RE.search(text) or any(u in _INR_UNITS for u in units):
        return "year"
    return None


def _number(raw: str, unit: str | None) -> Decimal | None:
    s = raw
    if unit == "k" and re.fullmatch(r"\d{1,3},\d{1,2}", s):
        s = s.replace(",", ".")  # decimal comma: "35,3k" = 35.3k
    else:
        s = s.replace(",", "")
    try:
        d = Decimal(s)
        d = d * _UNIT_MULT[unit] if unit else d
    except DecimalException:
        return None
    return d if abs(d) <= MAX_RAW_VALUE else None


def parse_salary_text(
    text: str | None, *, default_currency: str | None = None
) -> ParsedSalary | None:
    if not text:
        return None
    source = text[:MAX_SALARY_TEXT]
    clean = _PAREN_RE.sub(" ", source)
    plus = _PLUS_RE.search(clean)
    if plus:
        clean = clean[: plus.start()]
    lower = clean.lower()
    tokens = list(_TOKEN_RE.finditer(lower))
    if not tokens:
        return None
    first = tokens[0]
    group = [first]
    if len(tokens) > 1 and _RANGE_SEP_RE.match(lower[first.end() : tokens[1].start()]):
        group.append(tokens[1])
    units: list[str | None] = [(m.group("unit") or "").lower() or None for m in group]
    # "12-18 LPA": the first figure inherits the unit of the second.
    if len(group) == 2 and units[0] is None and units[1] is not None:
        units[0] = units[1]
    present = [u for u in units if u]
    currency = _detect_currency(lower, present) or default_currency
    period = _detect_period(lower, present)
    values = [_number(m.group("num"), u) for m, u in zip(group, units, strict=True)]
    nums = [v for v in values if v is not None]
    if len(nums) != len(values):
        return None
    if period is None:
        bare_k = "k" in present
        top = nums[0] if len(nums) == 1 else (nums[0] if nums[0] > nums[1] else nums[1])
        if currency == "INR":
            if any(u in _INR_UNITS for u in present):
                period = "year"
            elif top < BARE_K_MONTHLY_BELOW:
                period = "month"
            else:
                period = "year"
        elif currency is not None:
            period = "hour" if (top < 1000 and not bare_k) else "year"
    lo = nums[0]
    hi = nums[1] if len(nums) > 1 else lo
    return salary_from_numbers(
        min=lo,
        max=hi,
        currency=currency,
        period=period,
        is_estimate=False,
        source_text=source,
    )
