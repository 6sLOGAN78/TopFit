"""Typed ranking configuration (config/ranking.yaml) and the pipeline version hash."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ranker.config_loader import config_dir, load_config


class Retrieval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lexical_k: int
    vector_k: int
    rrf_k: int
    fused_k: int
    vector_mode: Literal["exact", "hnsw"]
    skill_rank_weight: float
    set_aside_universe_k: int
    # Compute set-aside/relax counts only when fewer than this many candidates were fused
    # (they exist to explain a short result list). 0 = always compute (ADR 0004).
    set_aside_when_results_below: int = 0


class GuestWeights(BaseModel):
    """Weights of the light guest signals added to the normalised RRF score (D-11)."""

    model_config = ConfigDict(extra="forbid")

    rrf: float = 1.0
    recency: float = 0.15
    region_certain: float = 0.10
    salary_known: float = 0.05
    ghost_penalty: float = 0.25
    unknown_seniority_penalty: float = 0.05


class Diversity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_per_company_top10: int = 2


class RankingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    retrieval: Retrieval
    page_size: int = 20
    guest_weights: GuestWeights = GuestWeights()
    recency_half_life_days: float = 14
    diversity: Diversity = Diversity()


def ranking_config() -> RankingConfig:
    return load_config("ranking.yaml", RankingConfig)


def pipeline_version(prompt_version: str) -> str:
    """First 12 hex chars of sha256(ranking.yaml bytes + prompt version)."""
    raw = (config_dir() / "ranking.yaml").read_bytes()
    return hashlib.sha256(raw + prompt_version.encode()).hexdigest()[:12]
