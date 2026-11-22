import time
from typing import get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ranker.ingest.normalize.region import RemoteEligibility, detect_remote_eligibility


def _hint(hint: str) -> str:
    return detect_remote_eligibility(
        region_hint=hint, description=None, country_code=None, remote_type="remote"
    ).remote_eligibility


def _desc(description: str) -> str:
    return detect_remote_eligibility(
        region_hint=None, description=description, country_code=None, remote_type="remote"
    ).remote_eligibility


def test_literal_has_all_seven_values() -> None:
    assert set(get_args(RemoteEligibility)) == {
        "worldwide",
        "india",
        "apac",
        "us_only",
        "eu_only",
        "other_region",
        "unknown",
    }


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("LATAM, Europe, USA, Canada, APAC", "apac"),
        ("USA, Canada, USA timezones", "us_only"),
        ("Europe, USA, Canada, Australia, Singapore, Mexico, Saudi Arabia", "apac"),
        ("Worldwide", "worldwide"),
        ("USA", "us_only"),
        ("US", "us_only"),
        ("Anywhere", "worldwide"),
        ("Europe", "eu_only"),
        ("EU", "eu_only"),
        ("UK", "eu_only"),
        ("EMEA", "eu_only"),
        ("India", "india"),
        ("Remote - India", "india"),
        ("Bangalore", "india"),
        ("Asia", "apac"),
        ("Singapore", "apac"),
        ("LATAM", "other_region"),
        ("Australia", "other_region"),
        ("Europe, USA", "other_region"),
        ("North America", "us_only"),
        ("Germany, France", "eu_only"),
        ("Worldwide, USA", "worldwide"),
        ("India, USA", "india"),
        ("Canada", "us_only"),
        ("Brazil, Mexico", "other_region"),
        ("Remote", "unknown"),
        ("", "unknown"),
    ],
)
def test_region_hints(hint: str, expected: str) -> None:
    assert _hint(hint) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("You must reside in the US to apply.", "us_only"),
        ("Candidates must be in the EU timezone.", "eu_only"),
        ("We need some IST overlap with the team.", "india"),
        ("This is a Remote - India role.", "india"),
        ("Work from anywhere you like.", "worldwide"),
        ("Working hours UTC+5:30.", "india"),
        ("Working hours GMT+8.", "apac"),
        ("US work authorization required.", "us_only"),
        ("No region mentioned at all.", "unknown"),
        ("Join us in building neural nets, useful tools. Seoul eureka.", "unknown"),
        ("We love Bengaluru and Pune.", "india"),
        ("Overlap with CET hours.", "eu_only"),
    ],
)
def test_description_phrases(text: str, expected: str) -> None:
    assert _desc(text) == expected


def test_word_boundaries_never_produce_tokens() -> None:
    result = detect_remote_eligibility(
        region_hint=None,
        description="neural useful Seoul eureka bus Indiana",
        country_code=None,
        remote_type="remote",
    )
    assert result.remote_regions == []
    assert result.source == "none"


def test_gmt_plus_offset_is_not_eu() -> None:
    result = detect_remote_eligibility(
        region_hint=None, description="GMT+8", country_code=None, remote_type="remote"
    )
    assert result.remote_regions == ["apac"]


def test_description_scan_window() -> None:
    filler = "x " * 2000  # 4000 chars
    middle_hit = "x " * 1600 + "worldwide " + "x " * 1700  # worldwide in the dead zone
    assert _desc(middle_hit) == "unknown"
    assert _desc("worldwide " + filler) == "worldwide"
    assert _desc(filler + "worldwide") == "worldwide"


def test_hint_wins_over_description() -> None:
    result = detect_remote_eligibility(
        region_hint="USA",
        description="Work from anywhere",
        country_code=None,
        remote_type="remote",
    )
    assert (result.remote_eligibility, result.source) == ("us_only", "hint")


def test_unrecognised_hint_falls_through_to_description() -> None:
    result = detect_remote_eligibility(
        region_hint="Remote",
        description="IST overlap required",
        country_code=None,
        remote_type="remote",
    )
    assert (result.remote_eligibility, result.source) == ("india", "description")


@pytest.mark.parametrize(
    ("country", "remote_type", "expected", "source"),
    [
        ("IN", "onsite", "india", "location"),
        ("IN", "hybrid", "india", "location"),
        ("GB", "onsite", "other_region", "location"),
        ("US", "hybrid", "other_region", "location"),
        (None, "remote", "unknown", "none"),
        ("IN", "remote", "unknown", "none"),
        (None, "onsite", "unknown", "none"),
    ],
)
def test_location_fallback(
    country: str | None, remote_type: str, expected: str, source: str
) -> None:
    result = detect_remote_eligibility(
        region_hint=None, description=None, country_code=country, remote_type=remote_type
    )
    assert (result.remote_eligibility, result.source) == (expected, source)


def test_remote_regions_sorted_tokens() -> None:
    result = detect_remote_eligibility(
        region_hint="USA, Europe, LATAM, Canada",
        description=None,
        country_code=None,
        remote_type="remote",
    )
    assert result.remote_regions == ["eu", "other", "us"]
    assert result.remote_eligibility == "other_region"


@settings(max_examples=25, deadline=None)
@given(st.text(max_size=50_000))
def test_random_long_strings_fast(text: str) -> None:
    start = time.perf_counter()
    detect_remote_eligibility(
        region_hint=text[:200], description=text, country_code=None, remote_type="remote"
    )
    assert time.perf_counter() - start < 1.0


def test_pathological_repeats_fast() -> None:
    start = time.perf_counter()
    _desc("us " * 20_000 + "in india " * 5_000)
    assert time.perf_counter() - start < 1.0
