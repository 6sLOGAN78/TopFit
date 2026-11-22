import json
from collections.abc import Callable
from pathlib import Path

from ranker.evals.grading import (
    agreement_report,
    blind_review,
    select_review_indices,
)
from ranker.evals.ranking_eval import Judgment, Persona, load_judgments

PERSONAS = [Persona(id="p1", query="python backend roles in Pune")]


def _write(path: Path, grades: list[int]) -> None:
    with path.open("w") as fh:
        for i, g in enumerate(grades):
            fh.write(
                json.dumps(
                    {
                        "persona_id": "p1",
                        "job_id": i + 1,
                        "grade": g,
                        "graded_by": "claude-sonnet-5-5",
                        "reviewed": False,
                        "human_grade": None,
                        "rubric_version": "grade-v1",
                    }
                )
                + "\n"
            )


def _scripted(answers: list[str]) -> Callable[[str], str]:
    it = iter(answers)
    return lambda _label: next(it)


def test_selection_is_seeded_and_stable() -> None:
    a = select_review_indices(50, 0.2, 7)
    assert a == select_review_indices(50, 0.2, 7)
    assert len(a) == 10 and a == sorted(set(a))
    assert a != select_review_indices(50, 0.2, 8)
    assert select_review_indices(0, 0.2, 7) == []


def test_blind_review_hides_grade_and_records_human_grade(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write(path, [3, 0, 1, 2, 3, 0, 1, 2, 3, 0])  # 10 rows -> 2 reviewed
    shown: list[str] = []
    n = blind_review(
        path,
        PERSONAS,
        lambda jid: f"Posting number {jid}",
        fraction=0.2,
        seed=7,
        prompt=_scripted(["x", "2", "1"]),  # one invalid answer is re-prompted
        show=shown.append,
    )
    assert n == 2
    out = "\n".join(shown)
    assert "python backend roles in Pune" in out and "Posting number" in out
    assert "grade" not in out.lower()
    assert "claude" not in out.lower() and "graded_by" not in out
    rows = load_judgments(path)
    reviewed = [r for r in rows if r.reviewed]
    assert len(reviewed) == 2
    assert sorted(r.human_grade for r in reviewed if r.human_grade is not None) == [1, 2]
    # same seed picks the same items on a second run; nothing left to do for reviewed ones
    again = blind_review(
        path, PERSONAS, str, fraction=0.2, seed=7, prompt=_scripted([]), show=lambda _s: None
    )
    assert again == 0


def test_quit_saves_progress(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write(path, [1] * 10)
    n = blind_review(
        path,
        PERSONAS,
        str,
        fraction=0.5,
        seed=7,
        prompt=_scripted(["3", "q"]),
        show=lambda _s: None,
    )
    assert n == 1
    assert sum(r.reviewed for r in load_judgments(path)) == 1


def _j(grade: int, human: int | None) -> Judgment:
    return Judgment(
        persona_id="p",
        job_id=grade * 10 + (human or 0),
        grade=grade,
        graded_by="m",
        reviewed=human is not None,
        human_grade=human,
        rubric_version="grade-v1",
    )


def test_agreement_report() -> None:
    rows = [_j(3, 3), _j(2, 2), _j(1, 2), _j(0, 2), _j(3, None)]
    rep = agreement_report(rows)
    assert rep.n == 4
    assert rep.exact_pct == 50.0
    assert rep.within1_pct == 75.0
    assert rep.kappa_quadratic is not None and rep.kappa_linear is not None
    assert -1.0 <= rep.kappa_linear <= 1.0


def test_agreement_perfect_and_empty() -> None:
    perfect = agreement_report([_j(0, 0), _j(1, 1), _j(2, 2), _j(3, 3)])
    assert perfect.exact_pct == 100.0 and perfect.kappa_quadratic == 1.0
    empty = agreement_report([_j(2, None)])
    assert empty.n == 0 and empty.exact_pct is None
