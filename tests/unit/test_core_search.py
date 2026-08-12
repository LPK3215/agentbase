"""Unit tests for the web search provider registry."""
from __future__ import annotations

import pytest

from agentbase.core.search import (
    DuckDuckGoSearch,
    SearchProvider,
    SearchRegistry,
    SearchResult,
    register_search_provider,
    search_registry,
)


class TestSearchResult:
    def test_to_dict(self):
        r = SearchResult(title="Test", url="https://example.com", snippet="A snippet", source="ddg")
        d = r.to_dict()
        assert d["title"] == "Test"
        assert d["url"] == "https://example.com"
        assert d["snippet"] == "A snippet"
        assert d["source"] == "ddg"


class TestDuckDuckGoSearch:
    def test_is_search_provider(self):
        provider = DuckDuckGoSearch()
        assert isinstance(provider, SearchProvider)

    def test_search_returns_list(self):
        """Test that search returns a list (may be empty if network unavailable)."""
        provider = DuckDuckGoSearch()
        results = provider.search("python programming language", max_results=3)
        assert isinstance(results, list)
        # If network is available, we should get results
        # If not, we get a single error result
        if results:
            assert isinstance(results[0], SearchResult)


class TestSearchRegistry:
    def test_register_and_get(self):
        reg = SearchRegistry()
        provider = DuckDuckGoSearch()
        reg.register("test", provider)
        assert reg.get("test") is provider

    def test_get_not_found(self):
        reg = SearchRegistry()
        with pytest.raises(Exception, match="Unknown search provider"):
            reg.get("nonexistent")

    def test_register_duplicate_raises(self):
        reg = SearchRegistry()
        reg.register("test", DuckDuckGoSearch())
        with pytest.raises(Exception, match="already registered"):
            reg.register("test", DuckDuckGoSearch())

    def test_register_override(self):
        reg = SearchRegistry()
        p1 = DuckDuckGoSearch()
        p2 = DuckDuckGoSearch()
        reg.register("test", p1)
        reg.register("test", p2, override=True)
        assert reg.get("test") is p2

    def test_has(self):
        reg = SearchRegistry()
        reg.register("test", DuckDuckGoSearch())
        assert reg.has("test") is True
        assert reg.has("missing") is False

    def test_names(self):
        reg = SearchRegistry()
        reg.register("alpha", DuckDuckGoSearch())
        reg.register("beta", DuckDuckGoSearch())
        assert reg.names() == ["alpha", "beta"]


class TestGlobalRegistry:
    def test_duckduckgo_registered_by_default(self):
        assert search_registry.has("duckduckgo")
        provider = search_registry.get("duckduckgo")
        assert isinstance(provider, DuckDuckGoSearch)

    def test_register_decorator(self):
        @register_search_provider("test_custom", override=True)
        class CustomSearch:
            def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
                return [SearchResult(title=f"Result for {query}", url="https://test.com", snippet="", source="custom")]

        assert search_registry.has("test_custom")
        provider = search_registry.get("test_custom")
        results = provider.search("test query")
        assert len(results) == 1
        assert "test query" in results[0].title


class TestKnowledgeBaseWithEmbeddings:
    """Verify KnowledgeBase works with EmbeddingProvider and persists vectors."""

    def test_kb_with_hash_embeddings(self, tmp_path):
        from agentbase.core.embeddings import HashEmbedding
        from agentbase.core.knowledge import KnowledgeBase

        kb = KnowledgeBase(
            db_path=tmp_path / "kb.db",
            embedding_provider=HashEmbedding(dimension=64),
        )
        kb.add_document(source="test.md", title="Python Guide", content="How to install Python on your system")
        kb.add_document(source="test2.md", title="Java Guide", content="How to install Java on your system")

        # Vector search should work
        results = kb.search("Python", top_k=2)
        assert len(results) > 0
        assert all(hasattr(r, "score") for r in results)

        kb.close()

    def test_embeddings_persisted(self, tmp_path):
        """Verify that embeddings are stored in the database, not recomputed each search."""
        from agentbase.core.embeddings import HashEmbedding
        from agentbase.core.knowledge import KnowledgeBase

        kb = KnowledgeBase(
            db_path=tmp_path / "kb.db",
            embedding_provider=HashEmbedding(dimension=32),
        )
        kb.add_document(source="doc.md", title="Test", content="This is a test document about AI")

        # Check that the embedding column has data
        row = kb._db.fetchone("SELECT embedding FROM kb_chunks LIMIT 1")
        assert row is not None
        emb_raw = row["embedding"] if hasattr(row, "__getitem__") else row[0]
        assert emb_raw is not None  # Embedding was stored

        import json
        vec = json.loads(emb_raw)
        assert len(vec) == 32

        kb.close()

    def test_kb_without_embeddings_uses_text_search(self, tmp_path):
        from agentbase.core.knowledge import KnowledgeBase

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        kb.add_document(source="test.md", title="Guide", content="How to install Python")
        results = kb.search("Python", top_k=5)
        assert len(results) > 0
        # Text search uses weighted relevance scoring (content match > 0).
        assert results[0].score > 0
        # A query present in the chunk content is a positive hit.
        assert "python" in results[0].chunk.content.lower()
        kb.close()
