import itertools

import pytest

from ranker.evals.metrics import weighted_kappa


def _reference(a: list[int], b: list[int], k: int, power: int) -> float:
    """Independent loop implementation of 1 - sum(w*o)/sum(w*e)."""
    n = len(a)
    o = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        o[x][y] += 1 / n
    ra = [sum(o[i]) for i in range(k)]
    rb = [sum(o[i][j] for i in range(k)) for j in range(k)]
    pairs = list(itertools.product(range(k), repeat=2))
    num = sum(abs(i - j) ** power / (k - 1) ** power * o[i][j] for i, j in pairs)
    den = sum(abs(i - j) ** power / (k - 1) ** power * ra[i] * rb[j] for i, j in pairs)
    return 1 - num / den


def test_identical_is_one() -> None:
    a = [0, 1, 2, 3, 2, 1]
    assert weighted_kappa(a, a) == 1.0
    assert weighted_kappa(a, a, kind="linear") == 1.0


def test_hand_computed_2x2() -> None:
    # o=[[.25,.25],[0,.5]], e=[[.125,.375],[.125,.375]] -> 1 - .25/.5
    a, b = [0, 0, 1, 1], [0, 1, 1, 1]
    assert weighted_kappa(a, b, k=2) == pytest.approx(0.5, abs=1e-9)
    assert weighted_kappa(a, b, k=2, kind="linear") == pytest.approx(0.5, abs=1e-9)


def test_4x4_fixture() -> None:
    a = [0, 0, 1, 1, 2, 2, 3, 3, 1, 2]
    b = [0, 1, 1, 2, 2, 3, 3, 3, 0, 2]
    assert weighted_kappa(a, b) == pytest.approx(_reference(a, b, 4, 2), abs=1e-9)
    assert weighted_kappa(a, b, kind="linear") == pytest.approx(_reference(a, b, 4, 1), abs=1e-9)
    assert 0 < weighted_kappa(a, b) < 1


def test_constant_ratings_is_one() -> None:
    assert weighted_kappa([2, 2, 2], [2, 2, 2]) == 1.0
