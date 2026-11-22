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


class RankingConfig(BaseModel):
    """Later plans add optional sections (guest weights, diversity) with defaults."""

    model_config = ConfigDict(extra="forbid")

    version: int
    retrieval: Retrieval
    page_size: int = 20


def ranking_config() -> RankingConfig:
    return load_config("ranking.yaml", RankingConfig)


def pipeline_version(prompt_version: str) -> str:
    """First 12 hex chars of sha256(ranking.yaml bytes + prompt version)."""
    raw = (config_dir() / "ranking.yaml").read_bytes()
    return hashlib.sha256(raw + prompt_version.encode()).hexdigest()[:12]
