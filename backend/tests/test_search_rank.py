from datetime import UTC, datetime, timedelta

from ranker.search.config import ranking_config
from ranker.search.rank import (
    Candidate,
    Scored,
    apply_company_cap,
    score_candidates,
    why_matched,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
CFG = ranking_config()


def _c(
    id: int,  # noqa: A002
    *,
    rrf: float = 0.02,
    company: str | None = None,
    age_days: float = 1,
    region: bool = True,
    salary: bool = False,
    ghost: bool = False,
    unknown_sen: bool = False,
) -> Candidate:
    return Candidate(
        id=id,
        rrf=rrf,
        lex_rank=1,
        vec_rank=None,
        cos=None,
        company_key=company or f"j:{id}",
        posted_or_first_seen=NOW - timedelta(days=age_days),
        region_certain=region,
        salary_known=salary,
        ghost=ghost,
        seniority_unknown=unknown_sen,
    )


def _score(
    cands: list[Candidate], *, salary_filter: bool = False, seniority_filter: bool = False
) -> list[Scored]:
    return score_candidates(
        cands, CFG, now=NOW, salary_filter=salary_filter, seniority_filter=seniority_filter
    )


def test_rrf_norm_in_unit_range_and_not_minmax() -> None:
    top = 2 / (CFG.retrieval.rrf_k + 1)
    out = _score([_c(1, rrf=top), _c(2, rrf=top / 2)])
    # raw RRF is carried unchanged; a lone candidate is not stretched to 1.0
    lone = _score([_c(3, rrf=top / 2, age_days=0)])[0]
    w = CFG.guest_weights
    assert abs(lone.score - (w.rrf * 0.5 + w.recency + w.region_certain)) < 1e-9
    assert out[0].candidate.id == 1


def test_fresh_beats_old_at_equal_rrf() -> None:
    out = _score([_c(1, age_days=30), _c(2, age_days=0)])
    assert [s.candidate.id for s in out] == [2, 1]


def test_region_certain_beats_unknown() -> None:
    out = _score([_c(1, region=False), _c(2, region=True)])
    assert out[0].candidate.id == 2


def test_salary_known_only_with_salary_filter() -> None:
    a, b = _c(1, salary=True), _c(2, salary=False)
    assert [s.candidate.id for s in _score([b, a], salary_filter=True)] == [1, 2]
    no_filter = _score([a, b])
    assert no_filter[0].score == no_filter[1].score


def test_ghost_ranks_lower_but_present() -> None:
    out = _score([_c(1, ghost=True), _c(2)])
    assert [s.candidate.id for s in out] == [2, 1]
    assert out[1].features["ghost"] == 1.0


def test_unknown_seniority_penalty_only_with_filter() -> None:
    a, b = _c(1, unknown_sen=True), _c(2)
    assert [s.candidate.id for s in _score([a, b], seniority_filter=True)] == [2, 1]
    plain = _score([a, b])
    assert plain[0].score == plain[1].score


def _scored_company(keys: list[str]) -> list[Scored]:
    return [Scored(_c(i, company=k), 1.0 - i / 100, {}) for i, k in enumerate(keys)]


def test_company_cap_defers_extras_after_top_ten() -> None:
    keys = ["A", "A", "A", "B", "A", "C", "D", "E", "F", "G", "H", "I"]
    items = _scored_company(keys)
    out = apply_company_cap(items, 2)
    assert len(out) == len(items)
    top = [s.candidate.company_key for s in out[:10]]
    assert top.count("A") == 2
    assert [s.candidate.id for s in out[10:]] == [2, 4]
    deferred = [s.candidate.id for s in out[10:] if s.candidate.company_key == "A"]
    assert deferred == [2, 4]
    assert {s.candidate.id for s in out} == {s.candidate.id for s in items}


def test_company_cap_short_list_stable() -> None:
    items = _scored_company(["A", "A", "A", "B"])
    out = apply_company_cap(items, 2)
    assert [s.candidate.id for s in out] == [0, 1, 3, 2]


def test_why_matched_formats() -> None:
    posted = NOW - timedelta(days=2, hours=1)
    assert (
        why_matched(matched_terms=["python", "backend"], place="Bengaluru", posted=posted, now=NOW)
        == "Matches python, backend · Bengaluru · posted 2d ago"
    )
    assert (
        why_matched(matched_terms=[], place="Remote · India OK", posted=posted, now=NOW)
        == "Remote · India OK · posted 2d ago"
    )
    assert (
        why_matched(matched_terms=[], place="", posted=NOW - timedelta(hours=3), now=NOW)
        == "posted today"
    )
    assert why_matched(matched_terms=list("abcde"), place="", posted=posted, now=NOW).startswith(
        "Matches a, b, c ·"
    )
