# ruff: noqa: RUF001
import time
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ranker.config_loader import load_config
from ranker.ingest.normalize.salary import (
    FxConfig,
    ParsedSalary,
    parse_salary_text,
    salary_from_numbers,
    to_inr_annual,
)

D = Decimal
L = 100_000  # one lakh
CR = 10_000_000  # one crore

# (text, default_currency, min, max, currency, period)
CASES: list[tuple[str, str | None, str, str, str, str]] = [
    ("₹12–18 LPA", None, str(12 * L), str(18 * L), "INR", "year"),
    ("12-18 LPA", None, str(12 * L), str(18 * L), "INR", "year"),
    ("12L - 18L", None, str(12 * L), str(18 * L), "INR", "year"),
    ("12 Lac", None, str(12 * L), str(12 * L), "INR", "year"),
    ("12 lakhs p.a.", None, str(12 * L), str(12 * L), "INR", "year"),
    ("1.2 Cr", None, "12000000", "12000000", "INR", "year"),
    ("₹8,00,000", None, "800000", "800000", "INR", "year"),
    ("INR 8,00,000 - 12,00,000", None, "800000", "1200000", "INR", "year"),
    ("₹50k/month", None, "50000", "50000", "INR", "month"),
    ("30,000/month", "INR", "30000", "30000", "INR", "month"),
    ("$120k–$150k", None, "120000", "150000", "USD", "year"),
    ("$14/hour", None, "14", "14", "USD", "hour"),
    ("$50-$75 /hour", None, "50", "75", "USD", "hour"),
    ("$35,3k- $52k", None, "35300", "52000", "USD", "year"),
    ("OTE $25k - $35k", None, "25000", "35000", "USD", "year"),
    ("$170k - $200k", None, "170000", "200000", "USD", "year"),
    ("€60k", None, "60000", "60000", "EUR", "year"),
    ("£50k", None, "50000", "50000", "GBP", "year"),
    ("₹10 LPA + ESOPs", None, str(10 * L), str(10 * L), "INR", "year"),
    ("CTC 15 LPA (fixed 12)", None, str(15 * L), str(15 * L), "INR", "year"),
    ("Rs. 25,000 per month", None, "25000", "25000", "INR", "month"),
    ("Rs 6 lakh per annum", None, str(6 * L), str(6 * L), "INR", "year"),
    ("INR 18 LPA", None, str(18 * L), str(18 * L), "INR", "year"),
    ("18 to 25 LPA", None, str(18 * L), str(25 * L), "INR", "year"),
    ("₹20–30 lakhs", None, str(20 * L), str(30 * L), "INR", "year"),
    ("2 Cr - 3 Cr", None, str(2 * CR), str(3 * CR), "INR", "year"),
    ("1 crore", None, str(CR), str(CR), "INR", "year"),
    ("₹80k", None, "80000", "80000", "INR", "month"),
    ("₹300k", None, "300000", "300000", "INR", "year"),
    ("₹40,000 pm", None, "40000", "40000", "INR", "month"),
    ("₹40000 monthly", None, "40000", "40000", "INR", "month"),
    ("$30 per hour", None, "30", "30", "USD", "hour"),
    ("$25/hr", None, "25", "25", "USD", "hour"),
    ("$100,000 - $130,000 per year", None, "100000", "130000", "USD", "year"),
    ("$100,000 a year", None, "100000", "100000", "USD", "year"),
    ("USD 90k", None, "90000", "90000", "USD", "year"),
    ("EUR 70k-80k", None, "70000", "80000", "EUR", "year"),
    ("GBP 55,000 per annum", None, "55000", "55000", "GBP", "year"),
    ("SGD 8,000/month", None, "8000", "8000", "SGD", "month"),
    ("€4,5k/month", None, "4500", "4500", "EUR", "month"),
    ("$60k+", None, "60000", "60000", "USD", "year"),
    ("$60k + bonus", None, "60000", "60000", "USD", "year"),
    ("up to ₹15 LPA", None, str(15 * L), str(15 * L), "INR", "year"),
    ("15.5 LPA", None, "1550000", "1550000", "INR", "year"),
    ("₹7.5-9.5 LPA", None, "750000", "950000", "INR", "year"),
    ("12 — 18 LPA", None, str(12 * L), str(18 * L), "INR", "year"),
    ("500000", "INR", "500000", "500000", "INR", "year"),
    ("45000", "INR", "45000", "45000", "INR", "month"),
    ("$95000", None, "95000", "95000", "USD", "year"),
    ("$15", None, "15", "15", "USD", "hour"),
    ("AED 20,000 monthly", None, "20000", "20000", "AED", "month"),
    ("CAD 100k - 120k", None, "100000", "120000", "CAD", "year"),
    ("AUD 110,000 p.a.", None, "110000", "110000", "AUD", "year"),
    ("$150k–$120k", None, "120000", "150000", "USD", "year"),
]

NONE_CASES = ["Competitive", "As per industry", "DOE", "", "Negotiable", "Not disclosed", None]


@pytest.mark.parametrize(("text", "dc", "lo", "hi", "cur", "per"), CASES)
def test_parse_table(text: str, dc: str | None, lo: str, hi: str, cur: str, per: str) -> None:
    got = parse_salary_text(text, default_currency=dc)
    assert got is not None
    assert got.min == D(lo)
    assert got.max == D(hi)
    assert got.currency == cur
    assert got.period == per
    assert got.is_estimate is False


def test_case_table_size() -> None:
    assert len(CASES) >= 50


@pytest.mark.parametrize("text", NONE_CASES)
def test_unparseable_is_none(text: str | None) -> None:
    assert parse_salary_text(text) is None


def test_inr_annual_conversion_lpa() -> None:
    got = parse_salary_text("₹12–18 LPA")
    assert got is not None
    assert got.min_inr_annual == D(1_200_000)
    assert got.max_inr_annual == D(1_800_000)
    assert got.fx_as_of is None


def test_monthly_x12() -> None:
    got = parse_salary_text("₹50k/month")
    assert got is not None
    assert got.min_inr_annual == D(600_000)


def test_hourly_x2080_usd() -> None:
    got = parse_salary_text("$50-$75 /hour")
    assert got is not None
    fx = load_config("fx.yaml", FxConfig)
    assert got.min_inr_annual == D(50) * fx.rates_to_inr["USD"] * 2080
    assert got.fx_as_of == fx.as_of


def test_ctc_over_fixed() -> None:
    got = parse_salary_text("CTC 15 LPA (fixed 12)")
    assert got is not None
    assert got.min_inr_annual == D(1_500_000)


def test_numbers_estimate_flag() -> None:
    got = salary_from_numbers(
        min=600000, max=900000, currency="INR", period="year", is_estimate=True
    )
    assert got.is_estimate is True
    assert got.min_inr_annual == D(600000)
    assert got.max_inr_annual == D(900000)
    assert got.fx_as_of is None


@pytest.mark.parametrize(("lo", "hi"), [(1000, 2000), (10**9, 2 * 10**9), (10, 20)])
def test_out_of_bounds(lo: int, hi: int) -> None:
    got = salary_from_numbers(min=lo, max=hi, currency="INR", period="year")
    assert got.out_of_bounds is True
    assert got.min_inr_annual is None
    assert got.max_inr_annual is None
    assert got.min == D(lo)


def test_unknown_currency_no_guess() -> None:
    got = salary_from_numbers(min=100000, max=200000, currency="XYZ", period="year")
    assert got.min_inr_annual is None
    assert got.max_inr_annual is None
    assert got.fx_as_of is None
    assert got.out_of_bounds is False


def test_missing_period_no_inr() -> None:
    got = salary_from_numbers(min=100000, max=200000, currency="INR", period=None)
    assert got.min_inr_annual is None


def test_to_inr_annual_helper() -> None:
    val, as_of = to_inr_annual(D(10), "USD", "year")
    fx = load_config("fx.yaml", FxConfig)
    assert val == D(10) * fx.rates_to_inr["USD"]
    assert as_of == fx.as_of
    assert to_inr_annual(None, "USD", "year") == (None, None)
    assert isinstance(fx.as_of, date)


def test_source_text_capped() -> None:
    got = parse_salary_text("$100k " + "x" * 1000)
    assert got is not None
    assert got.source_text is not None
    assert len(got.source_text) <= 300


@settings(max_examples=150, deadline=None)
@given(
    lo=st.integers(min_value=1, max_value=10**7),
    hi=st.integers(min_value=1, max_value=10**7),
    cur=st.sampled_from(["INR", "USD", "EUR", "GBP"]),
    per=st.sampled_from(["year", "month", "hour"]),
)
def test_property_conversion(lo: int, hi: int, cur: str, per: str) -> None:
    period: ParsedSalary = salary_from_numbers(
        min=lo,
        max=hi,
        currency=cur,
        period=per,  # pyright: ignore[reportArgumentType]
    )
    assert period.min is not None
    assert period.max is not None
    assert period.min <= period.max
    fx = load_config("fx.yaml", FxConfig)
    rate = D(1) if cur == "INR" else fx.rates_to_inr[cur]
    mult = fx.period_multiplier[per]
    if not period.out_of_bounds:
        assert period.min_inr_annual == period.min * rate * mult
        assert period.max_inr_annual == period.max * rate * mult
    else:
        assert period.min_inr_annual is None


@settings(max_examples=200, deadline=None)
@given(text=st.text(max_size=400))
def test_property_parse_total(text: str) -> None:
    got = parse_salary_text(text)
    if got is not None and got.min is not None and got.max is not None:
        assert got.min <= got.max


def test_long_adversarial_input_is_fast() -> None:
    nasty = "1," * 5000 + "k" + "₹ " * 2000
    start = time.perf_counter()
    parse_salary_text(nasty)
    parse_salary_text("9" * 100_000)
    assert time.perf_counter() - start < 1.0
