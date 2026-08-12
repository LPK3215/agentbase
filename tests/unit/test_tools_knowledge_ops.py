"""Unit tests for knowledge_ops tools (kb_add, kb_get, kb_list, kb_search, kb_delete, kb_ingest)."""
from __future__ import annotations

import json

import pytest

from agentbase.core.knowledge import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(db_path=tmp_path / "kb.db")


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def ctx(kb, workspace):
    return {"knowledge_base": kb, "workspace_dir": workspace}


class TestKbAddTool:
    def test_add_basic(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_add_tool

        tool_fn = build_kb_add_tool(context=ctx)
        result = tool_fn.invoke({"source": "test.md", "title": "Test", "content": "Hello world"})
        assert "Added" in result
        assert "id=" in result
        assert "chunks=" in result

    def test_add_with_metadata(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_add_tool

        tool_fn = build_kb_add_tool(context=ctx)
        result = tool_fn.invoke({
            "source": "doc.md",
            "title": "Doc",
            "content": "Content",
            "metadata": json.dumps({"author": "tester"}),
        })
        assert "Added" in result
        docs = kb.list_documents()
        assert docs[0].metadata["author"] == "tester"

    def test_add_invalid_metadata_fallback(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_add_tool

        tool_fn = build_kb_add_tool(context=ctx)
        result = tool_fn.invoke({
            "source": "doc.md",
            "title": "Doc",
            "content": "Content",
            "metadata": "not json",
        })
        assert "Added" in result
        docs = kb.list_documents()
        assert docs[0].metadata.get("raw") == "not json"


class TestKbGetTool:
    def test_get_existing(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_get_tool

        doc = kb.add_document(source="test.md", title="My Doc", content="Content")
        tool_fn = build_kb_get_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": doc.id})
        data = json.loads(result)
        assert data["title"] == "My Doc"
        assert data["content"] == "Content"

    def test_get_not_found(self, ctx):
        from agentbase.extensions.tools.knowledge_ops import build_kb_get_tool

        tool_fn = build_kb_get_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": 99999})
        assert "not found" in result.lower()


class TestKbListTool:
    def test_list_empty(self, ctx):
        from agentbase.extensions.tools.knowledge_ops import build_kb_list_tool

        tool_fn = build_kb_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "empty" in result.lower()

    def test_list_with_docs(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_list_tool

        kb.add_document(source="a.md", title="Alpha", content="aaa")
        kb.add_document(source="b.md", title="Beta", content="bbb")
        tool_fn = build_kb_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "Alpha" in result
        assert "Beta" in result
        assert "a.md" in result


class TestKbSearchTool:
    def test_search_found(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_search_tool

        kb.add_document(source="python.md", title="Python Guide", content="How to install Python")
        kb.add_document(source="java.md", title="Java Guide", content="How to install Java")
        tool_fn = build_kb_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "Python", "top_k": 5})
        assert "Python" in result

    def test_search_no_results(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_search_tool

        kb.add_document(source="a.md", title="A", content="hello")
        tool_fn = build_kb_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "nonexistent_xyz"})
        assert "no results" in result.lower()

    def test_search_top_k(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_search_tool

        for i in range(10):
            kb.add_document(source=f"doc_{i}.md", title=f"Doc {i}", content=f"common keyword {i}")
        tool_fn = build_kb_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "common", "top_k": 3})
        # Should not have more than 3 result lines (excluding header)
        lines = [line for line in result.split("\n") if line.startswith("- [")]
        assert len(lines) <= 3


class TestKbDeleteTool:
    def test_delete_existing(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_delete_tool

        doc = kb.add_document(source="test.md", title="Test", content="temp")
        tool_fn = build_kb_delete_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": doc.id})
        assert "Deleted" in result
        assert kb.get_document(doc_id=doc.id) is None

    def test_delete_not_found(self, ctx):
        from agentbase.extensions.tools.knowledge_ops import build_kb_delete_tool

        tool_fn = build_kb_delete_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": 99999})
        assert "not found" in result.lower()


class TestKbIngestTool:
    def test_ingest_file(self, ctx, workspace, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool

        test_file = workspace / "doc.md"
        test_file.write_text("# Title\n\nContent here", encoding="utf-8")
        tool_fn = build_kb_ingest_tool(context=ctx)
        result = tool_fn.invoke({"path": "doc.md"})
        assert "Ingested" in result
        assert "id=" in result

    def test_ingest_with_title(self, ctx, workspace, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool

        test_file = workspace / "doc.txt"
        test_file.write_text("Plain text content", encoding="utf-8")
        tool_fn = build_kb_ingest_tool(context=ctx)
        result = tool_fn.invoke({"path": "doc.txt", "title": "Custom Title"})
        assert "Custom Title" in result

    def test_ingest_not_found(self, ctx, workspace):
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool

        tool_fn = build_kb_ingest_tool(context=ctx)
        result = tool_fn.invoke({"path": "nonexistent.md"})
        assert "not found" in result.lower() or "File not found" in result

    def test_ingest_path_traversal(self, ctx, workspace):
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool

        tool_fn = build_kb_ingest_tool(context=ctx)
        result = tool_fn.invoke({"path": "../../../etc/passwd"})
        assert "escapes" in result.lower() or "workspace" in result.lower()
