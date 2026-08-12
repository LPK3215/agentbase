"""Unit tests for web_fetch, kb_update, and kb_batch_ingest tools."""
from __future__ import annotations

import json

from agentbase.core.knowledge import KnowledgeBase


class TestWebFetchTool:
    def test_build_and_call_html(self):
        """Test that web_fetch can extract text from HTML."""
        from agentbase.extensions.tools.web_fetch import _html_to_text

        # Test _html_to_text directly
        html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        text = _html_to_text(html)
        assert "Title" in text
        assert "Hello world" in text

    def test_html_to_text_removes_scripts(self):
        from agentbase.extensions.tools.web_fetch import _html_to_text

        html = "<script>alert('xss')</script><p>content</p>"
        text = _html_to_text(html)
        assert "alert" not in text
        assert "content" in text

    def test_html_to_text_unescapes_entities(self):
        from agentbase.extensions.tools.web_fetch import _html_to_text

        html = "<p>5 &lt; 10 &amp; 3 &gt; 1</p>"
        text = _html_to_text(html)
        assert "5 < 10 & 3 > 1" in text

    def test_html_to_text_normalizes_whitespace(self):
        from agentbase.extensions.tools.web_fetch import _html_to_text

        html = "<p>Para 1</p>\n\n\n\n<p>Para 2</p>"
        text = _html_to_text(html)
        # Should not have excessive blank lines
        assert "\n\n\n" not in text

    def test_tool_invoke_with_invalid_url(self):
        """web_fetch should return error message for invalid URLs."""
        from agentbase.extensions.tools.web_fetch import build_web_fetch_tool

        tool_fn = build_web_fetch_tool(context={})
        result = tool_fn.invoke({"url": "http://this-domain-does-not-exist-xyz.invalid", "max_length": 1000})
        assert "Fetch failed" in result


class TestKbUpdateTool:
    def test_update_title(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        doc = kb.add_document(source="test.md", title="Old Title", content="content")

        tool_fn = build_kb_update_tool(context={"knowledge_base": kb})
        result = tool_fn.invoke({"doc_id": doc.id, "title": "New Title"})
        assert "New Title" in result
        assert "Updated" in result

        updated = kb.get_document(doc_id=doc.id)
        assert updated.title == "New Title"
        kb.close()

    def test_update_content_rebuilds_chunks(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        doc = kb.add_document(source="test.md", title="Test", content="short")
        old_count = doc.chunk_count

        long_content = "\n\n".join(f"Paragraph {i}" * 50 for i in range(10))
        tool_fn = build_kb_update_tool(context={"knowledge_base": kb})
        result = tool_fn.invoke({"doc_id": doc.id, "content": long_content})
        assert "Updated" in result

        updated = kb.get_document(doc_id=doc.id)
        assert updated.chunk_count > old_count
        kb.close()

    def test_update_metadata(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        doc = kb.add_document(source="test.md", title="Test", content="content", metadata={"old": "val"})

        tool_fn = build_kb_update_tool(context={"knowledge_base": kb})
        result = tool_fn.invoke({"doc_id": doc.id, "metadata": json.dumps({"new": "val"})})
        assert "Updated" in result

        updated = kb.get_document(doc_id=doc.id)
        assert updated.metadata == {"new": "val"}
        kb.close()

    def test_update_not_found(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        tool_fn = build_kb_update_tool(context={"knowledge_base": kb})
        result = tool_fn.invoke({"doc_id": 99999, "title": "X"})
        assert "not found" in result.lower()
        kb.close()

    def test_update_nothing_provided(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        doc = kb.add_document(source="test.md", title="Test", content="content")

        tool_fn = build_kb_update_tool(context={"knowledge_base": kb})
        result = tool_fn.invoke({"doc_id": doc.id})
        assert "Nothing to update" in result
        kb.close()


class TestKbBatchIngestTool:
    def test_batch_ingest_directory(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        workspace = tmp_path / "workspace"
        docs_dir = workspace / "docs"
        docs_dir.mkdir(parents=True)

        (docs_dir / "a.md").write_text("# Document A\n\nContent A", encoding="utf-8")
        (docs_dir / "b.txt").write_text("Plain text B", encoding="utf-8")
        (docs_dir / "c.md").write_text("# Document C\n\nContent C", encoding="utf-8")

        tool_fn = build_kb_batch_ingest_tool(context={
            "knowledge_base": kb,
            "workspace_dir": workspace,
        })
        result = tool_fn.invoke({"directory": "docs"})
        assert "Ingested 3/3" in result
        assert "a.md" in result
        assert "b.txt" in result
        assert "c.md" in result

        docs = kb.list_documents()
        assert len(docs) == 3
        kb.close()

    def test_batch_ingest_with_pattern(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        workspace = tmp_path / "workspace"
        docs_dir = workspace / "docs"
        docs_dir.mkdir(parents=True)

        (docs_dir / "a.md").write_text("A", encoding="utf-8")
        (docs_dir / "b.txt").write_text("B", encoding="utf-8")

        tool_fn = build_kb_batch_ingest_tool(context={
            "knowledge_base": kb,
            "workspace_dir": workspace,
        })
        # Only .md files
        result = tool_fn.invoke({"directory": "docs", "pattern": "*.md"})
        assert "Ingested 1/1" in result
        assert "a.md" in result

        docs = kb.list_documents()
        assert len(docs) == 1
        kb.close()

    def test_batch_ingest_empty_directory(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        workspace = tmp_path / "workspace"
        docs_dir = workspace / "empty"
        docs_dir.mkdir(parents=True)

        tool_fn = build_kb_batch_ingest_tool(context={
            "knowledge_base": kb,
            "workspace_dir": workspace,
        })
        result = tool_fn.invoke({"directory": "empty"})
        assert "No files found" in result
        kb.close()

    def test_batch_ingest_not_found(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)

        tool_fn = build_kb_batch_ingest_tool(context={
            "knowledge_base": kb,
            "workspace_dir": workspace,
        })
        result = tool_fn.invoke({"directory": "nonexistent"})
        assert "not found" in result.lower()
        kb.close()

    def test_batch_ingest_nested_directories(self, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        kb = KnowledgeBase(db_path=tmp_path / "kb.db")
        workspace = tmp_path / "workspace"
        docs_dir = workspace / "docs"
        sub_dir = docs_dir / "sub"
        sub_dir.mkdir(parents=True)

        (docs_dir / "top.md").write_text("Top level", encoding="utf-8")
        (sub_dir / "nested.md").write_text("Nested level", encoding="utf-8")

        tool_fn = build_kb_batch_ingest_tool(context={
            "knowledge_base": kb,
            "workspace_dir": workspace,
        })
        result = tool_fn.invoke({"directory": "docs"})
        # Should find both files with default **/* pattern
        assert "Ingested 2/2" in result
        kb.close()
