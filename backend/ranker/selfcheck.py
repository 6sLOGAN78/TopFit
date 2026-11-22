"""Offline model self-check: loads the baked embedding model and asserts 384 dims.

Runs without DATABASE_URL/SESSION_SECRET so it works under `docker run --network none`.
"""

import json
import os
import sys
from collections.abc import Callable
from typing import Any

from ranker.config_loader import load_config
from ranker.embeddings.embedder import EMBED_DIM, FastEmbedEmbedder

MIN_SKILLS = 400


def _check_configs() -> list[str]:
    """Load every config file through its Pydantic model; raises on the first problem."""
    from ranker.ingest.normalize.dealbreakers import IndustriesConfig
    from ranker.ingest.normalize.location import CitiesConfig
    from ranker.ingest.normalize.region import RegionsConfig
    from ranker.ingest.normalize.salary import FxConfig
    from ranker.ingest.normalize.scope import ScopeConfig
    from ranker.ingest.skills import SkillsConfig
    from ranker.ingest.sources import SourcesConfig
    from ranker.ingest.sources.adzuna import AdzunaConfig
    from ranker.ingest.sources.remotive import RemotiveConfig
    from ranker.llm.cost import LlmPrices

    sources = load_config("sources.yaml", SourcesConfig)
    own: dict[str, Callable[[dict[str, Any]], object]] = {
        "adzuna": AdzunaConfig.model_validate,
        "remotive": RemotiveConfig.model_validate,
    }
    for name, section in sources.root.items():
        if name in own:
            own[name](section.model_dump())
    load_config("cities.yaml", CitiesConfig)
    load_config("regions.yaml", RegionsConfig)
    load_config("scope.yaml", ScopeConfig)
    load_config("fx.yaml", FxConfig)
    skills = load_config("skills.yaml", SkillsConfig)
    if len(skills.skills) < MIN_SKILLS:
        raise ValueError("skills taxonomy too small")
    load_config("industries.yaml", IndustriesConfig)
    load_config("llm_prices.yaml", LlmPrices)
    return [
        "sources.yaml",
        "cities.yaml",
        "regions.yaml",
        "scope.yaml",
        "fx.yaml",
        "skills.yaml",
        "industries.yaml",
        "llm_prices.yaml",
    ]


def main() -> int:
    model = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    try:
        threads = int(os.environ.get("EMBED_THREADS", "2"))
        embedder = FastEmbedEmbedder(model, threads)
        dim = len(embedder.embed(["selfcheck"])[0])
        if dim != EMBED_DIM:
            print(json.dumps({"ok": False, "error": f"DimMismatch:{dim}"}))
            return 1
        configs = _check_configs()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "model": model,
                "dim": dim,
                "configs": configs,
                "cache": os.environ.get("FASTEMBED_CACHE_PATH"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
