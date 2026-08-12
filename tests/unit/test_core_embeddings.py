"""Unit tests for the embedding provider registry."""
from __future__ import annotations

import pytest

from agentbase.core.embeddings import (
    EmbeddingProvider,
    EmbeddingRegistry,
    HashEmbedding,
    embedding_registry,
    register_embedding_provider,
)


class TestHashEmbedding:
    def test_is_embedding_provider(self):
        provider = HashEmbedding()
        assert isinstance(provider, EmbeddingProvider)

    def test_embed_returns_floats(self):
        provider = HashEmbedding(dimension=64)
        vec = provider.embed("hello world")
        assert len(vec) == 64
        assert all(isinstance(v, float) for v in vec)

    def test_embed_is_deterministic(self):
        provider = HashEmbedding(dimension=64)
        v1 = provider.embed("test text")
        v2 = provider.embed("test text")
        assert v1 == v2

    def test_different_texts_different_vectors(self):
        provider = HashEmbedding(dimension=64)
        v1 = provider.embed("hello")
        v2 = provider.embed("world")
        assert v1 != v2

    def test_unit_vector(self):
        """Hash embedding should produce normalized (unit) vectors."""
        import math

        provider = HashEmbedding(dimension=64)
        vec = provider.embed("some text")
        magnitude = math.sqrt(sum(v * v for v in vec))
        assert abs(magnitude - 1.0) < 0.01  # approximately unit length

    def test_custom_dimension(self):
        provider = HashEmbedding(dimension=128)
        assert provider.dimension == 128
        vec = provider.embed("test")
        assert len(vec) == 128

    def test_embed_batch(self):
        provider = HashEmbedding(dimension=32)
        vecs = provider.embed_batch(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 32


class TestEmbeddingRegistry:
    def test_register_and_get(self):
        reg = EmbeddingRegistry()
        provider = HashEmbedding(dimension=32)
        reg.register("test", provider)
        assert reg.get("test") is provider

    def test_get_not_found(self):
        reg = EmbeddingRegistry()
        with pytest.raises(Exception, match="Unknown embedding provider"):
            reg.get("nonexistent")

    def test_register_duplicate_raises(self):
        reg = EmbeddingRegistry()
        reg.register("test", HashEmbedding())
        with pytest.raises(Exception, match="already registered"):
            reg.register("test", HashEmbedding())

    def test_register_override(self):
        reg = EmbeddingRegistry()
        p1 = HashEmbedding(dimension=32)
        p2 = HashEmbedding(dimension=64)
        reg.register("test", p1)
        reg.register("test", p2, override=True)
        assert reg.get("test") is p2

    def test_has(self):
        reg = EmbeddingRegistry()
        reg.register("test", HashEmbedding())
        assert reg.has("test") is True
        assert reg.has("missing") is False

    def test_names(self):
        reg = EmbeddingRegistry()
        reg.register("alpha", HashEmbedding())
        reg.register("beta", HashEmbedding())
        assert reg.names() == ["alpha", "beta"]

    def test_case_insensitive(self):
        reg = EmbeddingRegistry()
        reg.register("MyProvider", HashEmbedding())
        assert reg.has("myprovider") is True
        assert reg.has("MYPROVIDER") is True


class TestGlobalRegistry:
    def test_hash_registered_by_default(self):
        assert embedding_registry.has("hash")
        provider = embedding_registry.get("hash")
        assert isinstance(provider, HashEmbedding)

    def test_register_decorator(self):
        @register_embedding_provider("test_custom", override=True)
        class CustomEmbedding:
            @property
            def dimension(self) -> int:
                return 8

            def embed(self, text: str) -> list[float]:
                return [0.1] * 8

        assert embedding_registry.has("test_custom")
        provider = embedding_registry.get("test_custom")
        vec = provider.embed("anything")
        assert len(vec) == 8
