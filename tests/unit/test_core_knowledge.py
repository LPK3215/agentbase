"""Unit tests for KnowledgeBase."""
from __future__ import annotations

import pytest

from agentbase.core.knowledge import KnowledgeBase, _chunk_text


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(db_path=tmp_path / "test_kb.db")


class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = _chunk_text("Hello world")
        assert len(chunks) == 1
        assert "Hello world" in chunks[0]

    def test_multiple_paragraphs(self):
        text = "\n\n".join(f"Paragraph {i}. " * 20 for i in range(5))
        chunks = _chunk_text(text, max_chunk_size=200)
        assert len(chunks) > 1

    def test_oversized_paragraph_split(self):
        text = "A" * 600
        chunks = _chunk_text(text, max_chunk_size=500)
        assert len(chunks) == 2
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 100

    def test_empty_text(self):
        chunks = _chunk_text("")
        assert chunks == []

    def test_only_whitespace(self):
        chunks = _chunk_text("   \n\n  \n  ")
        assert chunks == []


class TestKnowledgeBase:
    def test_add_and_get(self, kb):
        doc = kb.add_document(source="test.md", title="Test", content="Hello world content")
        assert doc.id is not None
        assert doc.title == "Test"
        assert doc.content == "Hello world content"
        assert doc.chunk_count >= 1

        fetched = kb.get_document(doc_id=doc.id)
        assert fetched is not None
        assert fetched.title == "Test"

    def test_get_not_found(self, kb):
        assert kb.get_document(doc_id=99999) is None

    def test_list_documents(self, kb):
        kb.add_document(source="a.md", title="A", content="aaa")
        kb.add_document(source="b.md", title="B", content="bbb")
        docs = kb.list_documents()
        assert len(docs) == 2

    def test_list_empty(self, kb):
        assert kb.list_documents() == []

    def test_update_document(self, kb):
        doc = kb.add_document(source="test.md", title="Old", content="old content")
        updated = kb.update_document(doc_id=doc.id, title="New", content="new content here")
        assert updated.title == "New"
        assert "new content" in updated.content

    def test_update_rebuilds_chunks(self, kb):
        doc = kb.add_document(source="test.md", title="Test", content="short")
        old_count = doc.chunk_count
        long_content = "\n\n".join(f"Paragraph {i}" * 50 for i in range(10))
        updated = kb.update_document(doc_id=doc.id, content=long_content)
        assert updated.chunk_count > old_count

    def test_update_partial(self, kb):
        doc = kb.add_document(source="test.md", title="Keep", content="keep this")
        updated = kb.update_document(doc_id=doc.id, title="Changed")
        assert updated.title == "Changed"
        assert updated.content == "keep this"

    def test_delete_document(self, kb):
        doc = kb.add_document(source="test.md", title="Test", content="temp")
        assert kb.delete_document(doc_id=doc.id) is True
        assert kb.get_document(doc_id=doc.id) is None
        assert kb.delete_document(doc_id=doc.id) is False

    def test_delete_cascades_chunks(self, kb):
        doc = kb.add_document(source="test.md", title="Test", content="a" * 600)
        kb.delete_document(doc_id=doc.id)
        # Verify chunks are gone (no direct API, but no error)
        assert kb.get_document(doc_id=doc.id) is None

    def test_text_search(self, kb):
        kb.add_document(source="python.md", title="Python Guide", content="How to install Python on your system")
        kb.add_document(source="java.md", title="Java Guide", content="How to install Java on your system")
        results = kb.search("Python", top_k=5)
        assert len(results) >= 1
        assert any("Python" in r.document.title for r in results)

    def test_search_no_results(self, kb):
        kb.add_document(source="a.md", title="A", content="hello world")
        results = kb.search("nonexistent_query_xyz")
        assert results == []

    def test_search_top_k(self, kb):
        for i in range(10):
            kb.add_document(source=f"doc_{i}.md", title=f"Doc {i}", content=f"common word document {i}")
        results = kb.search("common", top_k=3)
        assert len(results) <= 3

    def test_ingest_file(self, kb, tmp_path):
        test_file = tmp_path / "test_doc.md"
        test_file.write_text("# Test Document\n\nThis is test content.", encoding="utf-8")
        doc = kb.ingest_file(test_file)
        assert doc.title == "test_doc.md"
        assert "test content" in doc.content
        assert doc.metadata.get("file_extension") == ".md"

    def test_ingest_file_uses_parser_registry(self, kb, tmp_path):
        """ingest_file should use the registered parser for the file type."""
        test_file = tmp_path / "test_doc.txt"
        test_file.write_text("Plain text content here.", encoding="utf-8")
        doc = kb.ingest_file(test_file)
        assert "Plain text content" in doc.content
        assert doc.metadata.get("parser") is not None
        assert "TextParser" in doc.metadata.get("parser", "")

    def test_ingest_file_with_custom_parser(self, kb, tmp_path):
        """ingest_file should use a custom registered parser."""
        from agentbase.core.parsers import parser_registry

        class FakePdfParser:
            extensions = [".pdf"]

            def parse(self, path):
                return "extracted pdf text"

        parser_registry.register(FakePdfParser(), override=True)

        fake_pdf = tmp_path / "doc.pdf"
        fake_pdf.write_text("binary placeholder", encoding="utf-8")
        doc = kb.ingest_file(fake_pdf)
        assert doc.content == "extracted pdf text"
        assert "FakePdfParser" in doc.metadata.get("parser", "")


    def test_add_with_metadata(self, kb):
        doc = kb.add_document(
            source="test.md",
            title="Test",
            content="content",
            metadata={"author": "tester", "version": 2},
        )
        fetched = kb.get_document(doc_id=doc.id)
        assert fetched.metadata["author"] == "tester"
        assert fetched.metadata["version"] == 2

    def test_to_dict(self, kb):
        doc = kb.add_document(source="test.md", title="Test", content="content")
        d = doc.to_dict()
        assert d["title"] == "Test"
        assert d["source"] == "test.md"
        assert "created_at" in d
        assert "chunk_count" in d
