import time

import pytest

from ranker.ingest.normalize.seniority import infer_employment_type, infer_seniority

TITLE_CASES = [
    ("Software Engineering Intern", "intern"),
    ("Graduate Trainee", "intern"),
    ("Summer Internship - Backend", "intern"),
    ("Junior Backend Developer", "junior"),
    ("SDE I", "junior"),
    ("SDE-1", "junior"),
    ("Software Engineer I", "junior"),
    ("Associate Software Engineer", "junior"),
    ("Fresher - Java", "junior"),
    ("Jr. Data Analyst", "junior"),
    ("SDE II", "mid"),
    ("SDE-2", "mid"),
    ("Software Engineer II", "mid"),
    ("Senior Data Engineer", "senior"),
    ("Sr. SRE", "senior"),
    ("Sr Backend Engineer", "senior"),
    ("SDE III", "senior"),
    ("Software Engineer III", "senior"),
    ("Staff Engineer", "lead"),
    ("Principal Engineer", "lead"),
    ("Tech Lead", "lead"),
    ("Engineering Manager", "lead"),
    ("Head of Data", "lead"),
    ("Senior Staff Engineer", "lead"),
    ("Software Engineer", "unknown"),
    ("Backend Engineer Integration", "unknown"),
    ("Engineer Intuition Platform", "unknown"),
    ("", "unknown"),
]


@pytest.mark.parametrize(("title", "expected"), TITLE_CASES)
def test_title_rules(title: str, expected: str) -> None:
    got, _ = infer_seniority(title, None)
    assert got == expected


def test_title_source_label() -> None:
    assert infer_seniority("Senior Engineer", None) == ("senior", "title")
    assert infer_seniority("Software Engineer", None) == ("unknown", "none")


YEARS_CASES = [
    ("We need 0-1 years of experience in Python", "junior"),
    ("Requires 3+ years of experience building APIs", "mid"),
    ("5-7 years of experience with distributed systems", "senior"),
    ("10+ years of experience leading teams", "lead"),
    ("Experience: 6 years in backend", "senior"),
    ("minimum 2 years experience", "mid"),
]


@pytest.mark.parametrize(("desc", "expected"), YEARS_CASES)
def test_years_rules(desc: str, expected: str) -> None:
    assert infer_seniority("Software Engineer", desc) == (expected, "years")


def test_years_beyond_scan_window_ignored() -> None:
    desc = "x" * 4100 + " 10+ years of experience"
    assert infer_seniority("Software Engineer", desc) == ("unknown", "none")


def test_title_beats_years() -> None:
    assert infer_seniority("Junior Developer", "10+ years of experience") == ("junior", "title")


def test_company_years_not_experience() -> None:
    desc = "We have been in business for 10 years and love pizza"
    assert infer_seniority("Software Engineer", desc) == ("unknown", "none")


EMP_CASES = [
    ("full_time", None, "Engineer", "full_time", "hint"),
    ("freelance", None, "Engineer", "contract", "hint"),
    ("contract", None, "Engineer", "contract", "hint"),
    ("part_time", None, "Engineer", "part_time", "hint"),
    ("internship", None, "Engineer", "internship", "hint"),
    ("full_time", "permanent", "Engineer", "full_time", "hint"),
    ("part_time", "permanent", "Engineer", "part_time", "hint"),
    ("full_time", "contract", "Engineer", "contract", "hint"),
    (None, "contract", "Engineer", "contract", "hint"),
    ("Full-time Employment", None, "Engineer", "full_time", "hint"),
    ("Intern", None, "Engineer", "internship", "hint"),
    ("Part-time", None, "Engineer", "part_time", "hint"),
    ("Contract", None, "Engineer", "contract", "hint"),
    (None, None, "Software Intern", "internship", "title"),
    (None, None, "Engineer", "unknown", "none"),
    ("weird", None, "Engineer", "unknown", "none"),
    ("", None, "Engineer", "unknown", "none"),
]


@pytest.mark.parametrize(("hint", "ctype", "title", "expected", "source"), EMP_CASES)
def test_employment(
    hint: str | None, ctype: str | None, title: str, expected: str, source: str
) -> None:
    assert infer_employment_type(hint=hint, contract_type=ctype, title=title) == (expected, source)


def test_case_count() -> None:
    assert len(TITLE_CASES) + len(YEARS_CASES) + len(EMP_CASES) >= 40


def test_values_cover_literals() -> None:
    seniors = {infer_seniority(t, None)[0] for t, _ in TITLE_CASES}
    assert seniors == {"intern", "junior", "mid", "senior", "lead", "unknown"}
    emps = {e[3] for e in EMP_CASES}
    assert emps == {"full_time", "part_time", "contract", "internship", "unknown"}


def test_adversarial_input_fast() -> None:
    nasty = "1 years " * 2000 + "experience " * 500
    start = time.perf_counter()
    infer_seniority("Engineer", nasty)
    infer_seniority("a " * 5000, nasty)
    assert time.perf_counter() - start < 1.0
