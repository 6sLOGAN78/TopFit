import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ranker.ingest.normalize.location import Location, normalize_location
from ranker.ingest.normalize.scope import classify_geo, classify_scope, classify_tech

IN_SCOPE = [
    "Backend Engineer",
    "SDE II",
    "SDE-3",
    "Senior Software Developer",
    "Data Analyst",
    "Senior Data Analyst",
    "Analytics Engineer",
    "ML Engineer",
    "AI Researcher",
    "SRE",
    "Site Reliability Engineer",
    "Platform Engineer",
    "DevOps Engineer",
    "QA Automation Engineer",
    "SDET",
    "Security Engineer",
    "Android Developer",
    "iOS Engineer",
    "Full Stack Developer",
    "Fullstack Engineer",
    "Data Scientist",
    "Data Engineer",
    "Frontend Engineer (React)",
    "Front-end Developer",
    "Engineering Manager",
    "Staff Software Engineer",
    "Machine Learning Scientist",
    "Mobile Developer (Flutter)",
    "Java Developer",
    "Python Programmer",
    "Software Engineer Intern",
    "Cloud DevOps Lead",
    "NLP Engineer",
    "LLM Engineer",
    "Principal Engineer, Infrastructure",
    "Backend Developer - Node.js",
    "Test Automation Lead",
    "Salesforce Developer",
    "Data Platform Engineer",
    "Senior QA Engineer",
]
OUT_OF_SCOPE = [
    "Product Manager",
    "Senior Product Manager",
    "Product Designer",
    "UI/UX Designer",
    "Graphic Designer",
    "Customer Support Engineer",
    "Technical Support Specialist",
    "IT Helpdesk",
    "IT Help Desk Analyst",
    "System Administrator",
    "Systems Administrator",
    "IT Admin",
    "Sysadmin",
    "Sales Engineer",
    "Sales Executive",
    "Technical Recruiter",
    "Talent Acquisition Partner",
    "Marketing Manager",
    "Collections Officer",
    "Content Writer",
    "Technical Writer",
    "Business Development Manager",
    "Account Executive",
    "Customer Success Manager",
    "HR Executive",
    "Accountant",
    "Program Manager",
    "Delivery Driver",
    "Data Entry Operator",
    "Receptionist",
]


def test_labelled_title_count() -> None:
    assert len(IN_SCOPE) + len(OUT_OF_SCOPE) >= 60


@pytest.mark.parametrize("title", IN_SCOPE)
def test_in_scope_titles(title: str) -> None:
    decision = classify_tech(title, None)
    assert decision.in_scope, title
    assert decision.reason == "tech"
    assert decision.family is not None


@pytest.mark.parametrize("title", OUT_OF_SCOPE)
def test_out_of_scope_titles(title: str) -> None:
    assert not classify_tech(title, None).in_scope, title


@pytest.mark.parametrize(
    ("title", "category", "in_scope", "reason"),
    [
        ("Associate", "Software Development", True, "category"),
        ("Associate", "Sales", False, "not_tech"),
        ("Associate", None, False, "not_tech"),
        ("Sales Engineer", "Software Development", False, "excluded"),
        ("Product Manager", "it-jobs", False, "excluded"),
        ("Associate", "it-jobs", True, "category"),
        ("Specialist", "Engineering", True, "category"),
        ("Specialist", "Data Platform Team", True, "category"),
        ("Specialist", "Finance", False, "not_tech"),
        ("Associate", "Artificial Intelligence", True, "category"),
    ],
)
def test_category_rules(title: str, category: str | None, in_scope: bool, reason: str) -> None:
    decision = classify_tech(title, category)
    assert (decision.in_scope, decision.reason) == (in_scope, reason)


@pytest.mark.parametrize(
    ("title", "category", "family"),
    [
        ("Backend Engineer", None, "engineering"),
        ("Data Analyst", None, "data"),
        ("ML Engineer", None, "ml_ai"),
        ("SRE", None, "devops"),
        ("QA Automation Engineer", None, "qa"),
        ("Security Engineer", None, "security"),
        ("Android Developer", None, "mobile"),
        ("Associate", "Data and Analytics", "data"),
        ("Associate", "Artificial Intelligence", "ml_ai"),
        ("Associate", "Software Development", "engineering"),
    ],
)
def test_family(title: str, category: str | None, family: str) -> None:
    assert classify_tech(title, category).family == family


def _loc(country: str | None, remote_type: str, city: str | None = None) -> Location:
    return Location(
        city=city,
        cities=[city] if city else [],
        country_code=country,
        remote_type=remote_type,  # pyright: ignore[reportArgumentType]
    )


@pytest.mark.parametrize(
    ("country", "remote_type", "in_scope", "reason"),
    [
        ("US", "remote", True, "tech"),
        (None, "remote", True, "tech"),
        ("IN", "onsite", True, "tech"),
        ("IN", "hybrid", True, "tech"),
        ("GB", "onsite", False, "geo_out_of_scope"),
        ("US", "hybrid", False, "geo_out_of_scope"),
        (None, "onsite", True, "geo_unknown"),
        (None, "hybrid", True, "geo_unknown"),
    ],
)
def test_geo_rules(country: str | None, remote_type: str, in_scope: bool, reason: str) -> None:
    location = _loc(country, remote_type)
    decision = classify_scope("Backend Engineer", None, location)
    assert (decision.in_scope, decision.reason) == (in_scope, reason)
    assert decision.family == "engineering"
    assert classify_geo(location, remote_type).in_scope == in_scope


def test_non_tech_wins_over_geo() -> None:
    decision = classify_scope("Product Manager", None, _loc("IN", "onsite", "Pune"))
    assert (decision.in_scope, decision.reason) == (False, "excluded")


def test_end_to_end_with_normalised_location() -> None:
    assert classify_scope("SDE II", None, normalize_location("Bangalore")).in_scope
    assert not classify_scope("SDE II", None, normalize_location("London, UK")).in_scope
    remote_us = normalize_location("USA", workplace_hint="remote")
    assert classify_scope("SDE II", None, remote_us).in_scope


@settings(max_examples=25, deadline=None)
@given(st.text(max_size=50_000))
def test_random_titles_fast(text: str) -> None:
    start = time.perf_counter()
    classify_tech(text, text[:100])
    assert time.perf_counter() - start < 1.0
