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


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage
# ---------------------------------------------------------------------------


class TestEmbeddingRegistryExtras:
    def test_count(self):
        reg = EmbeddingRegistry()
        assert reg.count == 0
        reg.register("a", HashEmbedding())
        assert reg.count == 1
        reg.register("b", HashEmbedding())
        assert reg.count == 2

    def test_unregister_existing(self):
        reg = EmbeddingRegistry()
        reg.register("test", HashEmbedding())
        assert reg.unregister("test") is True
        assert not reg.has("test")

    def test_unregister_non_existing(self):
        reg = EmbeddingRegistry()
        assert reg.unregister("nonexistent") is False

    def test_register_empty_name_raises(self):
        reg = EmbeddingRegistry()
        with pytest.raises(Exception, match="empty"):
            reg.register("", HashEmbedding())

    def test_get_available_listed_in_error(self):
        reg = EmbeddingRegistry()
        with pytest.raises(Exception, match="<empty>"):
            reg.get("nonexistent")


class TestHashEmbeddingExtras:
    def test_cache_hit(self):
        """Second embed call uses cached result."""
        provider = HashEmbedding(dimension=64)
        v1 = provider.embed("cached text")
        v2 = provider.embed("cached text")
        assert v1 is v2  # Same object from cache

    def test_large_dimension(self):
        """Large dimension requires multiple hash rounds."""
        provider = HashEmbedding(dimension=512)
        vec = provider.embed("large dim test")
        assert len(vec) == 512


class TestOpenAIEmbeddingProvider:
    def test_dimension(self):
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(dimension=768)
        assert provider.dimension == 768

    def test_batch_size_clamped(self):
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(batch_size=99999)
        assert provider._batch_size == 2048
        provider2 = OpenAIEmbeddingProvider(batch_size=0)
        assert provider2._batch_size == 1

    def test_get_client_no_openai_package(self):
        from unittest.mock import patch
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(api_key="fake")
        # Mock openai as not installed
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="openai"):
                provider._get_client()

    def test_embed_cached(self):
        """Test embed with a mocked client returns cached result on second call."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(api_key="fake", dimension=128)

        # Mock _get_client
        fake_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.data[0].embedding = [0.1] * 128
        fake_client.embeddings.create.return_value = fake_resp

        provider._client = fake_client
        v1 = provider.embed("test text")
        assert len(v1) == 128
        # Second call should use cache, not call client again
        v2 = provider.embed("test text")
        assert v1 == v2
        assert fake_client.embeddings.create.call_count == 1

    def test_embed_batch_with_mock(self):
        """Test embed_batch with mocked client."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(api_key="fake", dimension=64, batch_size=2)

        texts = ["alpha", "beta", "alpha"]  # alpha duplicated

        fake_client = MagicMock()
        # Batch response: two embeddings
        fake_resp = MagicMock()
        item0 = MagicMock()
        item0.index = 0
        item0.embedding = [0.1] * 64
        item1 = MagicMock()
        item1.index = 1
        item1.embedding = [0.2] * 64
        fake_resp.data = [item0, item1]
        fake_client.embeddings.create.return_value = fake_resp

        provider._client = fake_client
        results = provider.embed_batch(texts)
        assert len(results) == 3
        # alpha (index 0 and 2) should be the same
        assert results[0] == results[2]
        # Only one API call for 2 unique texts
        assert fake_client.embeddings.create.call_count == 1

    def test_embed_batch_all_cached(self):
        """Test embed_batch when all texts are already cached."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider(api_key="fake", dimension=64)

        # Pre-populate cache
        cache_key = __import__("hashlib").md5("cached".encode()).hexdigest()
        provider._cache[cache_key] = [0.5] * 64

        results = provider.embed_batch(["cached"])
        assert results == [[0.5] * 64]

    def test_get_client_with_env(self):
        """Test _get_client reads env vars."""
        from unittest.mock import patch, MagicMock
        from agentbase.core.embeddings import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider()
        # Mock the openai import
        mock_openai_mod = MagicMock()
        mock_openai_cls = MagicMock()
        mock_openai_mod.OpenAI = mock_openai_cls

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "env-key", "OPENAI_BASE_URL": "https://custom.api.com"}):
                provider._get_client()
                # Client should have been created with env vars
                mock_openai_cls.assert_called_once()
                call_kwargs = mock_openai_cls.call_args
                assert call_kwargs.kwargs.get("api_key") == "env-key"
                assert call_kwargs.kwargs.get("base_url") == "https://custom.api.com"


class TestSentenceTransformersProvider:
    def test_dimension_without_model(self):
        """dimension property triggers model load."""
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider(dimension=384)
        assert provider.dimension == 384

    def test_get_model_no_package(self):
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider()
        with pytest.raises(ImportError, match="sentence-transformers"):
            provider._get_model()

    def test_embed_with_mock(self):
        """Test embed with mocked model."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider(dimension=64)
        fake_model = MagicMock()
        fake_model.encode.return_value = MagicMock(tolist=lambda: [0.1] * 64)
        provider._model = fake_model

        result = provider.embed("test text")
        assert len(result) == 64

    def test_embed_batch_with_mock(self):
        """Test embed_batch with mocked model."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider(dimension=64)
        fake_model = MagicMock()
        vec1 = MagicMock()
        vec1.tolist.return_value = [0.1] * 64
        vec2 = MagicMock()
        vec2.tolist.return_value = [0.2] * 64
        fake_model.encode.return_value = [vec1, vec2]
        provider._model = fake_model

        results = provider.embed_batch(["a", "b"])
        assert len(results) == 2
        assert len(results[0]) == 64

    def test_dimension_triggers_model_load(self):
        """When dimension is None, dimension property loads model."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider(dimension=None)
        # Mock _get_model to return a fake model and set _dimension
        fake_model = MagicMock()
        fake_model.get_sentence_embedding_dimension.return_value = 768
        original_get_model = provider._get_model
        provider._get_model = lambda: (setattr(provider, '_model', fake_model), setattr(provider, '_dimension', 768), fake_model)[-1]

        assert provider.dimension == 768

    def test_dimension_fallback(self):
        """When dimension is None and model returns None, falls back to 384."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import SentenceTransformersProvider

        provider = SentenceTransformersProvider(dimension=None)
        fake_model = MagicMock()
        fake_model.get_sentence_embedding_dimension.return_value = None
        provider._model = fake_model

        # dimension is None, _get_model called, _dimension set to None from model
        # then `return self._dimension or 384` → 384
        assert provider.dimension == 384


class TestCrossEncoderReranker:
    def test_get_model_no_package(self):
        from agentbase.core.embeddings import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        with pytest.raises(ImportError, match="sentence-transformers"):
            reranker._get_model()

    def test_rerank_with_mock(self):
        """Test rerank with mocked model."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        fake_model = MagicMock()
        # Mock predict to return array-like with indexing
        scores = [0.9, 0.1, 0.5]
        fake_model.predict.return_value = scores
        reranker._model = fake_model

        results = reranker.rerank("query", ["doc1", "doc2", "doc3"], top_k=2)
        assert len(results) == 2
        # doc1 (index 0) has highest score 0.9
        assert results[0][0] == 0  # original index
        assert results[0][1] == 0.9
        # doc3 (index 2) has score 0.5
        assert results[1][0] == 2

    def test_rerank_top_k_exceeds_docs(self):
        """When top_k > number of docs, all docs are returned."""
        from unittest.mock import MagicMock
        from agentbase.core.embeddings import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        fake_model = MagicMock()
        fake_model.predict.return_value = [0.5]
        reranker._model = fake_model

        results = reranker.rerank("query", ["only doc"], top_k=10)
        assert len(results) == 1
