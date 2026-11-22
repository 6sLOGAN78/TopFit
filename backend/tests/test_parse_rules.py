import pytest

from ranker.ingest.skills import SkillEntry, SkillsConfig, default_matcher
from ranker.search.models import SearchFilters
from ranker.search.parse import SALARY_BUCKETS_LPA, parse_query, parse_rules
from ranker.settings import Settings

# The rules path never reads settings, so no database is needed here.
SETTINGS = Settings.model_construct()


def test_demo_query_chips_and_texts() -> None:
    p = parse_rules("backend python bengaluru 10 LPA remote ok")
    assert p.filters.cities == ["Bengaluru"]
    assert p.filters.remote_ok is True
    assert p.filters.min_salary_lpa == 10
    assert p.filters.work_modes == []
    assert "python" in p.skills
    words = p.lex_text.split()
    assert "backend" in words and "python" in words
    for gone in ("bengaluru", "10", "lpa", "remote", "ok"):
        assert gone not in [w.lower() for w in words]
    assert [(c.field, c.label, c.source) for c in p.chips] == [
        ("city", "City: Bengaluru + remote", "rules"),
        ("min_salary", "₹10 LPA+", "rules"),
    ]
    assert p.parse_source == "rules"


def test_react_pune_hybrid() -> None:
    p = parse_rules("react frontend developer pune hybrid")
    assert p.filters.cities == ["Pune"]
    assert p.filters.work_modes == ["hybrid"]
    assert p.filters.remote_ok is False
    assert [c.label for c in p.chips] == ["City: Pune", "Hybrid"]
    assert "frontend" in p.lex_text and "pune" not in p.lex_text.lower()


def test_fresher_maps_through_seniority_normaliser() -> None:
    p = parse_rules("data analyst fresher sql")
    assert p.filters.seniority == ["junior"]
    assert [c.label for c in p.chips] == ["Junior"]
    assert "fresher" not in p.lex_text and "fresher" in p.embed_text


def test_remote_without_city_is_work_mode() -> None:
    p = parse_rules("devops kubernetes remote india")
    assert p.filters.work_modes == ["remote"]
    assert p.filters.cities == []
    assert [c.field for c in p.chips] == ["work_mode"]
    assert "india" not in p.lex_text


@pytest.mark.parametrize(
    ("q", "lpa"),
    [("blr 8 lakh", 8), ("12.5 LPA", 12), ("java 15-20 lpa", 15), ("python 2.5 lpa", None)],
)
def test_salary_snaps_down_to_bucket(q: str, lpa: int | None) -> None:
    p = parse_rules(q)
    assert p.filters.min_salary_lpa == lpa
    if lpa is not None:
        assert lpa in SALARY_BUCKETS_LPA


def test_blr_alias_is_bengaluru() -> None:
    assert parse_rules("blr 8 lakh").filters.cities == ["Bengaluru"]


@pytest.mark.parametrize("q", ["sde 2 bangalore", "swe 2 bangalore"])
def test_sde_swe_map_to_software_engineer(q: str) -> None:
    p = parse_rules(q)
    assert "software engineer" in p.lex_text
    assert "software engineer" in p.embed_text


def test_other_synonyms() -> None:
    assert "frontend" in parse_rules("front-end dev").lex_text
    assert "machine learning" in parse_rules("ml engineer").lex_text


def test_short_query_go_skill_via_query_ok() -> None:
    assert "go" in parse_rules("remote go jobs").skills


def test_long_sentence_go_is_not_a_skill() -> None:
    q = "I would like to go to the office a few days and meet colleagues every single week"
    assert "go" not in parse_rules(q).skills


def test_query_ok_requires_ambiguous() -> None:
    with pytest.raises(ValueError, match="query_ok"):
        SkillsConfig(
            provenance=["t"], skills=[SkillEntry(canonical="x", category="tool", query_ok=True)]
        )
    assert default_matcher().query_ok_canonical("GO") == "go"


def test_residue_keeps_unknown_place_word() -> None:
    p = parse_rules("senior java near whitefield")
    assert "whitefield" in p.residue
    assert "near" not in p.residue and "java" not in p.residue


def test_role_words_and_skills_never_become_chips() -> None:
    p = parse_rules("python backend engineer")
    assert p.chips == []
    assert p.filters == SearchFilters()


async def test_explicit_without_auto_is_verbatim() -> None:
    explicit = SearchFilters(cities=["Pune"], min_salary_lpa=6)
    p = await parse_query(
        "backend python bengaluru 10 LPA remote ok",
        explicit,
        False,
        engine=None,
        gateway=None,
        parse_model="haiku",
        settings=SETTINGS,
    )
    assert p.filters == explicit
    assert {c.source for c in p.chips} == {"user"}
    # nothing was consumed from the text: the user removed those chips on purpose
    assert "bengaluru" in p.lex_text.lower()


async def test_explicit_empty_without_auto_has_no_filters() -> None:
    p = await parse_query(
        "python bengaluru 10 LPA",
        None,
        False,
        engine=None,
        gateway=None,
        parse_model="haiku",
        settings=SETTINGS,
    )
    assert p.filters == SearchFilters()
    assert p.chips == []


async def test_explicit_overrides_parsed_per_field() -> None:
    p = await parse_query(
        "backend python bengaluru 10 LPA",
        SearchFilters(cities=["Pune"]),
        True,
        engine=None,
        gateway=None,
        parse_model="haiku",
        settings=SETTINGS,
    )
    assert p.filters.cities == ["Pune"]
    assert p.filters.min_salary_lpa == 10
    by_field = {c.field: c.source for c in p.chips}
    assert by_field == {"city": "user", "min_salary": "rules"}
