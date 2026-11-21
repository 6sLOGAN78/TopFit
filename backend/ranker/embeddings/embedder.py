"""Embedding abstraction: real fastembed model in prod, deterministic fake in tests."""

import hashlib
import struct
from collections.abc import Sequence
from typing import Any, Protocol

from ranker.settings import Settings

EMBED_DIM = 384


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Wraps fastembed.TextEmbedding. No BGE query prefix is added (STACK.md verdict 11)."""

    dim: int = EMBED_DIM

    def __init__(self, model_name: str, threads: int) -> None:
        from fastembed import TextEmbedding  # lazy: keeps onnxruntime out of fake-embedder runs

        self._model: Any = TextEmbedding(model_name=model_name, threads=threads)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(list(texts))]


class FakeEmbedder:
    """Deterministic, network-free vectors derived from sha256 of the text."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            for (value,) in struct.iter_unpack(">I", digest):
                out.append(value / 0xFFFFFFFF * 2.0 - 1.0)
            counter += 1
        return out[: self.dim]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder()
    return FastEmbedEmbedder(settings.embed_model, settings.embed_threads)
