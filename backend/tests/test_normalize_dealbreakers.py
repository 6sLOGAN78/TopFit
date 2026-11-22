import pytest

from ranker.ingest.normalize.company import normalize_company_name
from ranker.ingest.normalize.dealbreakers import (
    _company_map,  # pyright: ignore[reportPrivateUsage]
    company_profile,
    detect_relocation,
    detect_visa_sponsorship,
    infer_industry,
    normalize_company_size,
    normalize_company_stage,
    resolve_company_size,
    resolve_company_stage,
)

VISA_CASES = [
    ("Visa sponsorship available for the right candidate.", "offered"),
    ("We sponsor visas for engineers.", "offered"),
    ("H-1B sponsorship provided.", "offered"),
    ("We can sponsor work visas.", "offered"),
    ("Unable to sponsor candidates at this time.", "not_offered"),
    ("No visa sponsorship is offered.", "not_offered"),
    ("We cannot sponsor visas.", "not_offered"),
    ("Must have the right to work in the US without sponsorship.", "not_offered"),
    ("Visa sponsorship available, but we are unable to sponsor for this role.", "not_offered"),
    ("We build payments APIs. Great team.", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
]

RELOC_CASES = [
    ("Relocation assistance provided.", "offered"),
    ("We offer relocation support to Bengaluru.", "offered"),
    ("We will relocate you to our HQ.", "offered"),
    ("No relocation available for this role.", "not_offered"),
    ("Relocation not provided.", "not_offered"),
    ("Candidates must already be located in Germany.", "not_offered"),
    ("We do not offer relocation.", "not_offered"),
    ("Relocation support offered, but no relocation for contractors.", "not_offered"),
    ("Fully remote role in India.", "unknown"),
    (None, "unknown"),
]


@pytest.mark.parametrize(("text", "expected"), VISA_CASES)
def test_visa(text: str | None, expected: str) -> None:
    assert detect_visa_sponsorship(text) == expected


@pytest.mark.parametrize(("text", "expected"), RELOC_CASES)
def test_relocation(text: str | None, expected: str) -> None:
    assert detect_relocation(text) == expected


def test_case_count() -> None:
    assert len(VISA_CASES) + len(RELOC_CASES) >= 16


def test_scan_window_capped() -> None:
    text = "x " * 4000 + "visa sponsorship available"
    assert detect_visa_sponsorship(text) == "unknown"


# --- industry / stage / size -------------------------------------------------------------


def test_company_profile_hit_and_miss() -> None:
    p = company_profile("razorpay software")
    assert (p.industry, p.stage) == ("fintech", "late")
    miss = company_profile("no such co")
    assert (miss.industry, miss.stage, miss.size) == ("unknown", "unknown", "unknown")


def test_industry_self_description() -> None:
    desc = "Acme is a fintech startup building lending APIs"
    assert infer_industry(company_normalized="acme", description=desc, hint=None) == "fintech"
    desc2 = "We are an AI company building agents."
    assert infer_industry(company_normalized="acme", description=desc2, hint=None) == "ai_ml"


def test_industry_mention_without_self_description_is_unknown() -> None:
    desc = "You will integrate with payments providers and ship backend features."
    assert infer_industry(company_normalized="acme", description=desc, hint=None) == "unknown"


def test_industry_hint_wins_over_text() -> None:
    desc = "Acme is a fintech startup"
    assert infer_industry(company_normalized="acme", description=desc, hint="Edtech") == "edtech"
    assert infer_industry(company_normalized="acme", description=None, hint="Fintech") == "fintech"


def test_generic_hint_does_not_override_specific_map() -> None:
    got = infer_industry(
        company_normalized="razorpay software", description=None, hint="Software Development"
    )
    assert got == "fintech"
    other = infer_industry(company_normalized="acme", description=None, hint="Software Development")
    assert other == "other"


def test_unmapped_company_no_phrase() -> None:
    got = infer_industry(company_normalized="acme", description="We hire people.", hint=None)
    assert got == "unknown"
    assert infer_industry(company_normalized="acme", description=None, hint=None) == "unknown"


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("Series B", "growth"),
        ("Series C", "growth"),
        ("Seed", "early"),
        ("Series A", "early"),
        ("Pre-Seed", "early"),
        ("Series D", "late"),
        ("Late stage", "late"),
        ("Public", "public"),
        ("NASDAQ: X", "public"),
        (None, "unknown"),
        ("", "unknown"),
        ("confusing", "unknown"),
    ],
)
def test_stage(hint: str | None, expected: str) -> None:
    assert normalize_company_stage(hint) == expected


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("51-200 employees", "51-200"),
        ("1-10", "1-10"),
        ("11-50", "11-50"),
        ("201-500", "201-1000"),
        ("1000+", "201-1000"),
        ("1500+", "1001-5000"),
        ("10,000+", "5001+"),
        ("5001-10000", "5001+"),
        ("250 employees", "201-1000"),
        (None, "unknown"),
        ("lots", "unknown"),
        ("0", "unknown"),
    ],
)
def test_size(hint: str | None, expected: str) -> None:
    assert normalize_company_size(hint) == expected


def test_resolve_precedence() -> None:
    assert resolve_company_stage(hint="Seed", company_normalized="razorpay") == "early"
    assert resolve_company_stage(hint=None, company_normalized="razorpay") == "late"
    assert resolve_company_stage(hint=None, company_normalized="nobody") == "unknown"
    assert resolve_company_size(hint="11-50", company_normalized="infosys") == "11-50"
    assert resolve_company_size(hint=None, company_normalized="infosys") == "5001+"


def test_curated_map_keys_are_normalised_and_cover_seed_boards() -> None:
    keys = _company_map()
    assert len(keys) >= 40
    for key in keys:
        assert normalize_company_name(key) == key
    for slug in ("cred", "meesho", "paytm", "zeta", "groww", "gitlab", "stripe", "openai", "ramp"):
        assert slug in keys
