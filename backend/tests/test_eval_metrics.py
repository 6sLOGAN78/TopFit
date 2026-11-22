import math

from hypothesis import given
from hypothesis import strategies as st

from ranker.evals.metrics import (
    bootstrap_ci,
    dcg,
    mrr,
    ndcg_at_k,
    p_at_k,
    recall_at_k,
    unjudged_rate_at_k,
)

JUDGED = {1: 3, 2: 2, 3: 0, 4: 1, 5: 2}


def test_dcg_hand_computed() -> None:
    expected = 7 / math.log2(2) + 3 / math.log2(3) + 0 + 1 / math.log2(5) + 3 / math.log2(6)
    assert abs(dcg([3, 2, 0, 1, 2], 10) - expected) < 1e-9


def test_ndcg_hand_computed() -> None:
    ranked = [3, 1, 2, 4, 5, 99]
    actual = dcg([0, 3, 2, 1, 2, 0], 10)
    ideal = dcg([3, 2, 2, 1, 0], 10)
    assert abs(ndcg_at_k(ranked, JUDGED) - actual / ideal) < 1e-9


def test_ndcg_perfect_none_and_unjudged() -> None:
    assert abs(ndcg_at_k([1, 2, 5, 4, 3], JUDGED) - 1.0) < 1e-12
    assert ndcg_at_k([1, 2], {1: 0, 2: 0}) == 0.0
    assert ndcg_at_k([], JUDGED) == 0.0
    assert ndcg_at_k([100, 101, 1], JUDGED) < ndcg_at_k([1, 100, 101], JUDGED)


def test_recall_mrr_p5() -> None:
    assert recall_at_k([1, 3, 9], JUDGED) == 1 / 3
    assert recall_at_k([1, 2, 5], JUDGED) == 1.0
    assert recall_at_k([1, 2, 5], JUDGED, k=1) == 1 / 3
    assert math.isnan(recall_at_k([1], {1: 1}))
    assert mrr([9, 2, 1], JUDGED) == 1 / 3
    assert mrr([2, 5], JUDGED) == 0.0
    assert p_at_k([1, 2, 3, 4, 5], JUDGED) == 3 / 5
    assert unjudged_rate_at_k([1, 50, 51, 2], JUDGED) == 0.5


def test_bootstrap_reproducible_and_contains_mean() -> None:
    xs = [0.2, 0.5, 0.9, 0.4, 0.7, float("nan"), 0.6]
    a = bootstrap_ci(xs, b=10_000, seed=20261122)
    assert a == bootstrap_ci(xs, b=10_000, seed=20261122)
    mean, lo, hi = a
    assert abs(mean - 0.55) < 1e-9
    assert lo <= mean <= hi
    assert all(math.isnan(v) for v in bootstrap_ci([float("nan")]))


_ids = st.lists(st.integers(0, 30), unique=True, max_size=40)
_judged = st.dictionaries(st.integers(0, 30), st.integers(0, 3), max_size=30)


@given(_ids, _judged)
def test_metrics_in_unit_interval(ranked: list[int], judged: dict[int, int]) -> None:
    for v in (
        ndcg_at_k(ranked, judged),
        mrr(ranked, judged),
        p_at_k(ranked, judged),
        unjudged_rate_at_k(ranked, judged),
    ):
        assert 0.0 <= v <= 1.0 + 1e-12
    r = recall_at_k(ranked, judged)
    assert math.isnan(r) or 0.0 <= r <= 1.0
