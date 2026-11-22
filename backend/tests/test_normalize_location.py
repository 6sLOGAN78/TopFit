import pytest

from ranker.ingest.normalize.location import detect_onsite_days, normalize_location

NCR = ["Delhi", "Gurugram", "Noida"]


@pytest.mark.parametrize(
    ("text", "city", "cities", "country"),
    [
        ("Bangalore", "Bengaluru", ["Bengaluru"], "IN"),
        ("BLR", "Bengaluru", ["Bengaluru"], "IN"),
        ("Bengaluru Urban", "Bengaluru", ["Bengaluru"], "IN"),
        ("Bengaluru-VTP, India", "Bengaluru", ["Bengaluru"], "IN"),
        ("Bangalore, Karnataka, India", "Bengaluru", ["Bengaluru"], "IN"),
        ("Gurgaon, Haryana", "Gurugram", ["Gurugram"], "IN"),
        ("New Delhi", "Delhi", ["Delhi"], "IN"),
        ("Delhi NCR", "Delhi", NCR, "IN"),
        ("NCR", "Delhi", NCR, "IN"),
        ("Delhi/NCR", "Delhi", NCR, "IN"),
        ("Greater Noida", "Noida", ["Noida"], "IN"),
        ("Secunderabad", "Hyderabad", ["Hyderabad"], "IN"),
        ("Madras", "Chennai", ["Chennai"], "IN"),
        ("Calcutta", "Kolkata", ["Kolkata"], "IN"),
        ("Cochin", "Kochi", ["Kochi"], "IN"),
        ("Trivandrum", "Thiruvananthapuram", ["Thiruvananthapuram"], "IN"),
        ("Mohali", "Chandigarh", ["Chandigarh"], "IN"),
        ("Bombay", "Mumbai", ["Mumbai"], "IN"),
        ("Navi Mumbai", "Navi Mumbai", ["Navi Mumbai"], "IN"),
        ("Thane", "Thane", ["Thane"], "IN"),
        ("Mumbai, Maharashtra", "Mumbai", ["Mumbai"], "IN"),
        ("Pune", "Pune", ["Pune"], "IN"),
        ("Vizag", "Visakhapatnam", ["Visakhapatnam"], "IN"),
        ("Bengaluru / Hyderabad / Pune", "Bengaluru", ["Bengaluru", "Hyderabad", "Pune"], "IN"),
        ("Hyderabad | Chennai", "Hyderabad", ["Hyderabad", "Chennai"], "IN"),
        ("Pan India", None, [], "IN"),
        ("Multiple locations", None, [], "IN"),
        ("Anywhere in India", None, [], "IN"),
        ("India", None, [], "IN"),
        ("London, UK", None, [], "GB"),
        ("Berlin, Germany", None, [], "DE"),
        ("Singapore", None, [], "SG"),
        ("United States", None, [], "US"),
        ("U.S.", None, [], "US"),
        ("Remote", None, [], None),
        ("", None, [], None),
        (None, None, [], None),
    ],
)
def test_normalize_city_and_country(
    text: str | None, city: str | None, cities: list[str], country: str | None
) -> None:
    loc = normalize_location(text)
    assert loc.city == city
    assert loc.cities == cities
    assert loc.country_code == country


def test_adzuna_parts_most_specific_last() -> None:
    loc = normalize_location(None, parts=["India", "Karnataka", "Bangalore"])
    assert loc.city == "Bengaluru"
    assert loc.country_code == "IN"


def test_adzuna_parts_country_only() -> None:
    loc = normalize_location(None, parts=["India"])
    assert loc.city is None
    assert loc.country_code == "IN"
    assert loc.remote_type == "onsite"


def test_country_hint_used_when_text_has_no_country() -> None:
    assert normalize_location("Remote", country_hint="US").country_code == "US"
    assert normalize_location("Somewhere", country_hint="Germany").country_code == "DE"


@pytest.mark.parametrize(
    ("text", "hint", "description", "expected"),
    [
        ("Bangalore", "remote", None, "remote"),
        ("Bangalore", "hybrid", None, "hybrid"),
        ("Bangalore", "onsite", None, "onsite"),
        ("Bangalore", "on-site", None, "onsite"),
        ("Bangalore", "unspecified", None, "onsite"),
        (None, "remote", None, "remote"),
        ("Anywhere", None, "Fully WFH role", "remote"),
        ("Pune", None, "This is a hybrid position", "hybrid"),
        ("Pune", None, "Work from home allowed", "remote"),
        ("Remote - India", None, None, "remote"),
        ("London, UK", None, None, "onsite"),
        (None, None, None, "unknown"),
        ("Somewhere", None, None, "unknown"),
        ("Pune", "unspecified", "Come to the office", "onsite"),
    ],
)
def test_remote_type(
    text: str | None, hint: str | None, description: str | None, expected: str
) -> None:
    assert (
        normalize_location(text, workplace_hint=hint, description=description).remote_type
        == expected
    )


def test_keywords_beyond_2000_chars_ignored() -> None:
    desc = "x" * 2100 + " remote"
    assert normalize_location("Pune", description=desc).remote_type == "onsite"


@pytest.mark.parametrize(
    ("description", "remote_type", "expected"),
    [
        ("Hybrid - 3 days a week in office", "hybrid", 3),
        ("2 days WFO", "hybrid", 2),
        ("work from office 2 days a week", "hybrid", 2),
        ("in-office 4x per week", "hybrid", 4),
        ("5 days work from office", "onsite", 5),
        ("6 days working", "onsite", 6),
        ("Hybrid role, details at interview", "hybrid", None),
        ("Standard onsite role", "onsite", None),
        ("3-4 days in office", "hybrid", 3),
        ("three days a week in the office", "hybrid", 3),
        ("3 years of experience, 2 weeks notice", "onsite", None),
        ("9 days in office", "onsite", None),
        ("anything", "remote", 0),
        (None, "hybrid", None),
        (None, "remote", 0),
        ("Hybrid. 2 to 3 days at the office", "hybrid", 2),
        ("   hybrid:\n 3  days\n a  week in  office", "hybrid", 3),
    ],
)
def test_detect_onsite_days(
    description: str | None, remote_type: str, expected: int | None
) -> None:
    assert detect_onsite_days(description, remote_type) == expected


def test_onsite_days_scan_bounded_to_4000_chars() -> None:
    assert detect_onsite_days("x" * 4000 + " 3 days a week in office", "hybrid") is None
