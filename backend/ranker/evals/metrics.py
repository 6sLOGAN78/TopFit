"""Ranking metrics (SPEC 12.3), percentile bootstrap CIs and weighted Cohen's kappa.

Pure numpy, no sklearn. Grades are 0..3; unjudged ids count as 0 (report unjudged@k).
"""

from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np

BOOTSTRAP_SEED = 20261122
RELEVANT_GRADE = 2


def dcg(grades: Sequence[int], k: int) -> float:
    g = np.asarray(list(grades)[:k], dtype=float)
    if g.size == 0:
        return 0.0
    return float(((2**g - 1) / np.log2(np.arange(2, g.size + 2))).sum())


def ndcg_at_k(ranked_ids: Sequence[int], judged: Mapping[int, int], k: int = 10) -> float:
    ideal = dcg(sorted(judged.values(), reverse=True), k)
    if ideal == 0:
        return 0.0
    return dcg([judged.get(i, 0) for i in ranked_ids], k) / ideal


def recall_at_k(ranked_ids: Sequence[int], judged: Mapping[int, int], k: int = 50) -> float:
    """Share of relevant (grade >= 2) jobs retrieved in the top k; nan when none are relevant."""
    rel = {i for i, g in judged.items() if g >= RELEVANT_GRADE}
    if not rel:
        return float("nan")
    return len(rel & set(ranked_ids[:k])) / len(rel)


def mrr(ranked_ids: Sequence[int], judged: Mapping[int, int]) -> float:
    """Reciprocal rank of the first grade-3 job."""
    return next((1 / (r + 1) for r, i in enumerate(ranked_ids) if judged.get(i, 0) == 3), 0.0)


def p_at_k(ranked_ids: Sequence[int], judged: Mapping[int, int], k: int = 5) -> float:
    return sum(judged.get(i, 0) >= RELEVANT_GRADE for i in ranked_ids[:k]) / k


def unjudged_rate_at_k(ranked_ids: Sequence[int], judged: Mapping[int, int], k: int = 10) -> float:
    top = list(ranked_ids[:k])
    if not top:
        return 0.0
    return sum(i not in judged for i in top) / len(top)


def bootstrap_ci(
    per_persona: Sequence[float],
    b: int = 10_000,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Seeded percentile bootstrap of the mean; nan entries are dropped. Returns (mean, lo, hi)."""
    x = np.asarray([v for v in per_persona if not np.isnan(v)], dtype=float)
    if x.size == 0:
        return float("nan"), float("nan"), float("nan")
    idx = np.random.default_rng(seed).integers(0, x.size, size=(b, x.size))
    means = x[idx].mean(axis=1)
    return (
        float(x.mean()),
        float(np.quantile(means, alpha / 2)),
        float(np.quantile(means, 1 - alpha / 2)),
    )


def weighted_kappa(
    a: Sequence[int],
    b: Sequence[int],
    k: int = 4,
    kind: Literal["quadratic", "linear"] = "quadratic",
) -> float:
    o = np.zeros((k, k))
    for x, y in zip(a, b, strict=True):
        o[x, y] += 1
    total = o.sum()
    if total == 0:
        return 1.0
    o /= total
    e = np.outer(o.sum(1), o.sum(0))
    i, j = np.indices((k, k))
    if kind == "quadratic":
        w = ((i - j) ** 2) / (k - 1) ** 2
    else:
        w = np.abs(i - j) / (k - 1)
    denom = float((w * e).sum())
    if denom == 0:
        return 1.0
    return float(1 - (w * o).sum() / denom)
