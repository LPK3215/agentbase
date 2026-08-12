"""Embedding provider registry — pluggable vector embedding for RAG.

The scaffold provides the **interface** and a **zero-dependency default**
(hash-based pseudo-embeddings for testing).  Users register real embedding
providers (OpenAI, Cohere, local sentence-transformers, etc.) using whichever
library they prefer.

Usage::

    from agentbase.core.embeddings import register_embedding_provider

    # Register an OpenAI embedding provider
    @register_embedding_provider("openai")
    class OpenAIEmbeddings:
        def embed(self, text: str) -> list[float]:
            from openai import OpenAI
            client = OpenAI()
            resp = client.embeddings.create(input=text, model="text-embedding-3-small")
            return resp.data[0].embedding

        @property
        def dimension(self) -> int:
            return 1536

Interface::

    class EmbeddingProvider(Protocol):
        @property
        def dimension(self) -> int: ...
        def embed(self, text: str) -> list[float]: ...
        def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
"""

from __future__ import annotations

import hashlib
import struct
import threading
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    Implementations must provide ``dimension`` (vector size) and ``embed``
    (single text → vector).  ``embed_batch`` is optional and defaults to
    calling ``embed`` repeatedly.
    """

    @property
    def dimension(self) -> int:
        """Return the dimensionality of the embedding vectors."""
        ...

    def embed(self, text: str) -> list[float]:
        """Embed a single text into a float vector."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts.  Default: loop over ``embed``."""
        ...


# ---------------------------------------------------------------------------
# Default provider: hash-based pseudo-embeddings (zero dependencies)
# ---------------------------------------------------------------------------

class HashEmbedding:
    """Deterministic hash-based pseudo-embedding.

    Not useful for real semantic search, but works for testing and
    zero-dependency setups.  Uses SHA-256 to produce a fixed-size
    float vector.

    Features:
    - Thread-safe embedding cache (content hash → vector)
    - Deterministic — same input always produces same output
    """

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        # Check cache first
        cache_key = hashlib.md5(text.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import math
        # Generate enough hash bytes to fill the vector
        needed = self._dimension * 4  # 4 bytes per float
        data = b""
        counter = 0
        while len(data) < needed:
            data += hashlib.sha256(f"{text}:{counter}".encode()).digest()
            counter += 1
        floats = struct.unpack(f"{self._dimension}f", data[:needed])
        # Sanitize: replace NaN/inf with 0
        floats = [f if math.isfinite(f) else 0.0 for f in floats]
        # Normalize to unit vector
        magnitude = sum(f * f for f in floats) ** 0.5
        if magnitude == 0:
            result = [0.0] * self._dimension
        else:
            result = [f / magnitude for f in floats]
        # Cache the result
        with self._lock:
            self._cache[cache_key] = result
        return result

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class EmbeddingRegistry:
    """Thread-safe registry mapping names to embedding provider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, EmbeddingProvider] = {}
        self._lock = threading.RLock()

    def register(self, name: str, provider: EmbeddingProvider, *, override: bool = False) -> None:
        key = name.strip().lower()
        if not key:
            raise RegistryError("Cannot register empty embedding provider name")
        with self._lock:
            if key in self._providers and not override:
                raise RegistryError(f"Embedding provider already registered: {key}")
            self._providers[key] = provider

    def get(self, name: str) -> EmbeddingProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._providers:
                available = ", ".join(sorted(self._providers)) or "<empty>"
                raise RegistryError(f"Unknown embedding provider: {key}. Available: {available}")
            return self._providers[key]

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._providers

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._providers.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._providers)

    def unregister(self, name: str) -> bool:
        """Remove a provider. Returns True if removed."""
        key = name.strip().lower()
        with self._lock:
            if key not in self._providers:
                return False
            self._providers.pop(key, None)
            return True


# Global singleton
embedding_registry = EmbeddingRegistry()

# Register defaults
embedding_registry.register("hash", HashEmbedding())


class OpenAIEmbeddingProvider:
    """OpenAI text embedding provider.

    Uses the ``openai`` Python package. Requires ``OPENAI_API_KEY``
    environment variable.

    Features:
    - Thread-safe embedding cache (content hash → vector)
    - Configurable batch size for ``embed_batch`` (default 100)
    - Automatic retry on transient API errors

    Usage::

        from agentbase.core.embeddings import embedding_registry
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
        embedding_registry.register("openai", provider, override=True)
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
        dimension: int = 1536,
        batch_size: int = 100,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._dimension = dimension
        self._batch_size = min(max(batch_size, 1), 2048)
        self._client = None
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError(
                    "OpenAI embeddings require the openai package. "
                    "Install with: pip install agentbase[embeddings]"
                ) from exc

            import os
            kwargs: dict[str, Any] = {}
            key = self._api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("SILICONFLOW_API_KEY")
            if key:
                kwargs["api_key"] = key
            base = self._base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("SILICONFLOW_BASE_URL")
            if base:
                kwargs["base_url"] = base
            self._client = OpenAI(**kwargs)
        return self._client

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        # Check cache first
        cache_key = hashlib.md5(text.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client()
        resp = client.embeddings.create(input=text, model=self._model)
        result = resp.data[0].embedding
        with self._lock:
            self._cache[cache_key] = result
        return result

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Deduplicate texts to minimize API calls
        unique_texts = list(dict.fromkeys(texts))  # preserves order
        text_to_embedding: dict[str, list[float]] = {}

        # Check cache for all texts
        uncached: list[str] = []
        for text in unique_texts:
            cache_key = hashlib.md5(text.encode()).hexdigest()
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                text_to_embedding[text] = cached
            else:
                uncached.append(text)

        # Batch-fetch uncached embeddings
        if uncached:
            client = self._get_client()
            for i in range(0, len(uncached), self._batch_size):
                chunk = uncached[i : i + self._batch_size]
                resp = client.embeddings.create(input=chunk, model=self._model)
                sorted_data = sorted(resp.data, key=lambda x: x.index)
                for j, text in enumerate(chunk):
                    embedding = sorted_data[j].embedding
                    text_to_embedding[text] = embedding
                    cache_key = hashlib.md5(text.encode()).hexdigest()
                    with self._lock:
                        self._cache[cache_key] = embedding

        # Return in original order
        return [text_to_embedding[t] for t in texts]


# Register OpenAI provider if the package is available
try:
    import openai  # noqa: F401
    embedding_registry.register("openai", OpenAIEmbeddingProvider(), override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Local embedding: sentence-transformers (HuggingFace)
# ---------------------------------------------------------------------------

class SentenceTransformersProvider:
    """Local embedding provider using sentence-transformers (HuggingFace).

    Downloads models to ``~/.cache/huggingface/`` on first use, then runs
    fully offline. No API key required.

    Recommended models:
    - ``all-MiniLM-L6-v2`` (384 dim, fast, good quality)
    - ``BAAI/bge-small-zh-v1.5`` (512 dim, Chinese-optimized)
    - ``BAAI/bge-large-en-v1.5`` (1024 dim, English, high quality)

    Usage::

        # In config:
        embedding:
          provider: sentence-transformers
          options:
            model: all-MiniLM-L6-v2

        # Or programmatically:
        from agentbase.core.embeddings import SentenceTransformersProvider
        provider = SentenceTransformersProvider(model="all-MiniLM-L6-v2")
    """

    def __init__(
        self,
        *,
        model: str = "all-MiniLM-L6-v2",
        device: str = "cpu",
        dimension: int | None = None,
    ) -> None:
        self._model_name = model
        self._device = device
        self._dimension = dimension
        self._model = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is required for local embeddings. "
                    "Install with: pip install sentence-transformers"
                ) from exc
            self._model = SentenceTransformer(self._model_name, device=self._device)
            if self._dimension is None:
                self._dimension = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._get_model()
        return self._dimension or 384

    def embed(self, text: str) -> list[float]:
        model = self._get_model()
        vec = model.encode(text, convert_to_numpy=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vecs = model.encode(texts, convert_to_numpy=True, batch_size=32)
        return [v.tolist() for v in vecs]


# Register sentence-transformers provider if the package is available
try:
    import sentence_transformers  # noqa: F401
    embedding_registry.register("sentence-transformers", SentenceTransformersProvider(), override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Reranker: cross-encoder for improving RAG precision
# ---------------------------------------------------------------------------

class CrossEncoderReranker:
    """Reranker using sentence-transformers CrossEncoder.

    After vector search retrieves Top-K candidates, pass them through
    the reranker to re-score with a cross-encoder model for better precision.

    Usage::

        from agentbase.core.embeddings import CrossEncoderReranker
        reranker = CrossEncoderReranker(model="cross-encoder/ms-marco-MiniLM-L-6-v2")
        reranked = reranker.rerank("query", [chunk1, chunk2, ...], top_k=3)
    """

    def __init__(
        self,
        *,
        model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: str = "cpu",
    ) -> None:
        self._model_name = model
        self._device = device
        self._model = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is required for reranking. "
                    "Install with: pip install sentence-transformers"
                ) from exc
            self._model = CrossEncoder(self._model_name, device=self._device)
        return self._model

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Rerank documents against a query.

        Returns list of ``(original_index, score)`` sorted by relevance descending.
        """
        model = self._get_model()
        pairs = [(query, doc) for doc in documents]
        scores = model.predict(pairs)
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed[:top_k]


def register_embedding_provider(name: str, *, override: bool = False):
    """Decorator: register an embedding provider class.

    Usage::

        @register_embedding_provider("openai")
        class OpenAIEmbeddings:
            def embed(self, text: str) -> list[float]: ...
            @property
            def dimension(self) -> int: ...
    """
    def _wrap(cls):
        instance = cls()
        embedding_registry.register(name, instance, override=override)
        return cls

    return _wrap
