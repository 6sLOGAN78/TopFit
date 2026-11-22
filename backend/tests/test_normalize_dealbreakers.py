import pytest

from ranker.ingest.normalize.dealbreakers import detect_relocation, detect_visa_sponsorship

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
