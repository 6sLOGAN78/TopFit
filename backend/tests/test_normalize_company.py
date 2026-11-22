# ruff: noqa: RUF001
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ranker.ingest.normalize.company import is_placeholder_company, normalize_company_name

NORM_CASES = [
    ("Razorpay Software Pvt. Ltd.", "razorpay software"),
    ("Infosys Limited", "infosys"),
    ("Freshworks Inc.", "freshworks"),
    ("Zeta Suite (Directi) LLP", "zeta suite directi"),
    ("ThoughtWorks Technologies India Pvt Ltd", "thoughtworks"),
    ("Sigma Labs", "sigma"),
    ("AB Labs", "ab labs"),
    ("Co", "co"),
    ("Ｚｏｍａｔｏ", "zomato"),
    ("ZOMATO LTD", "zomato"),
    ("Tata Consultancy Services Limited", "tata consultancy services"),
    ("Acme Corp.", "acme"),
    ("Acme Corporation", "acme"),
    ("Acme GmbH", "acme"),
    ("Acme Private Limited", "acme"),
    ("Acme LLC", "acme"),
    ("  Acme   Inc  ", "acme"),
    ("Inc", "inc"),
    ("Acme-Works, Inc.", "acme works"),
    ("Pine Labs", "pine"),
    ("Google India Pvt Ltd", "google"),
    ("Stripe", "stripe"),
    ("", "unknown"),
    ("!!!", "unknown"),
    ("   ", "unknown"),
]


@pytest.mark.parametrize(("raw", "expected"), NORM_CASES)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


def test_canonical_hint_used_as_base() -> None:
    assert normalize_company_name("RAZORPAY SOFTWARE PRIVATE LIMITED", "Razorpay") == "razorpay"
    assert normalize_company_name("Foo", "") == "foo"


@pytest.mark.parametrize(
    "name",
    [
        "Confidential",
        "Hiring Company",
        "Stealth Startup",
        "A client of XYZ Consulting",
        "Top MNC",
        "Company Confidential",
        "Randstad India Pvt Ltd",
        "",
        "...",
    ],
)
def test_placeholders(name: str) -> None:
    assert is_placeholder_company(name) is True


@pytest.mark.parametrize(
    "name",
    ["Razorpay", "TeamLease Services", "Stripe Inc.", "Confidentialis Labs", "Stealthy Systems"],
)
def test_real_companies_not_placeholders(name: str) -> None:
    assert is_placeholder_company(name) is False


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300))
def test_idempotent(text: str) -> None:
    once = normalize_company_name(text)
    assert normalize_company_name(once) == once


def test_long_input_fast() -> None:
    start = time.perf_counter()
    normalize_company_name("ab " * 50_000)
    is_placeholder_company("a client of " * 5000)
    assert time.perf_counter() - start < 1.0
