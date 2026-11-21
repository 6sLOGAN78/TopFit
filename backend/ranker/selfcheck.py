"""Offline model self-check: loads the baked embedding model and asserts 384 dims.

Runs without DATABASE_URL/SESSION_SECRET so it works under `docker run --network none`.
"""

import json
import os
import sys

from ranker.embeddings.embedder import EMBED_DIM, FastEmbedEmbedder


def main() -> int:
    model = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    try:
        threads = int(os.environ.get("EMBED_THREADS", "2"))
        embedder = FastEmbedEmbedder(model, threads)
        dim = len(embedder.embed(["selfcheck"])[0])
        if dim != EMBED_DIM:
            print(json.dumps({"ok": False, "error": f"DimMismatch:{dim}"}))
            return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "model": model,
                "dim": dim,
                "cache": os.environ.get("FASTEMBED_CACHE_PATH"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
