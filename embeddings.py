"""
Text embeddings — provider-agnostic, mirroring judge.py.

Used by the RAG layer for semantic retrieval. Providers:
- ``openai`` — text-embedding-3-small (default when OPENAI_API_KEY is set).
- ``fake``   — deterministic, offline hashed bag-of-words. Captures lexical
               overlap as vector similarity so the semantic path runs without a
               key (tests/demos). Not as good as real embeddings, but real.

``get_embedder`` returns None (not an error) when no embedder can be built, so
the RAG layer can fall back to lexical overlap silently.
"""

import hashlib
import math
import re
from typing import List, Optional, Protocol

import config


class EmbeddingError(RuntimeError):
    """Raised when an embedding request fails at runtime."""


def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9æøåÆØÅ]+", text.lower()) if len(t) > 2]


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity. Returns 0.0 on empty or dimension-mismatched inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Embedder(Protocol):
    def embed(self, texts: List[str]) -> List[List[float]]: ...


class FakeEmbedder:
    """Deterministic hashed bag-of-words embedding (offline)."""

    def __init__(self, dim: int = config.EMBEDDING_DIM):
        self.dim = dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in _tokenize(text):
                bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
            out.append(_normalize(vec))
        return out


class OpenAIEmbedder:
    """OpenAI embeddings."""

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def embed(self, texts: List[str]) -> List[List[float]]:
        try:
            response = self.client.embeddings.create(model=self.model, input=texts)
        except Exception as exc:
            raise EmbeddingError(f"OpenAI embedding request failed: {exc}") from exc
        return [item.embedding for item in response.data]


def get_embedder(provider: Optional[str] = None) -> Optional[Embedder]:
    """Build the configured embedder, or None if one can't be made (→ lexical)."""
    provider = (provider or config.EMBEDDING_PROVIDER).lower()
    if provider in ("", "none", "off", "lexical"):
        return None
    if provider == "fake":
        return FakeEmbedder()
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            return None
        return OpenAIEmbedder(config.OPENAI_API_KEY, config.OPENAI_EMBED_MODEL)
    return None
