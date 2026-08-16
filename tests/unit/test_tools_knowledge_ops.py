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


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage
# ---------------------------------------------------------------------------


class TestKbOpsExtras:
    def test_get_kb_no_context(self):
        from agentbase.extensions.tools.knowledge_ops import _get_kb

        with pytest.raises(RuntimeError, match="knowledge_base not available"):
            _get_kb(None)

    def test_get_kb_missing_key(self):
        from agentbase.extensions.tools.knowledge_ops import _get_kb

        with pytest.raises(RuntimeError, match="knowledge_base not available"):
            _get_kb({"other": "value"})

    def test_ingest_workspace_fallback_root_dir(self, kb, tmp_path):
        """Test kb_ingest uses root_dir/workspace when workspace_dir not in context."""
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool

        root = tmp_path / "root"
        ws = root / "workspace"
        ws.mkdir(parents=True)
        test_file = ws / "doc.txt"
        test_file.write_text("content", encoding="utf-8")

        ctx = {"knowledge_base": kb, "root_dir": str(root)}
        tool_fn = build_kb_ingest_tool(context=ctx)
        result = tool_fn.invoke({"path": "doc.txt"})
        assert "Ingested" in result

    def test_ingest_workspace_fallback_default(self, kb):
        """Test kb_ingest uses default 'workspace' when no workspace_dir or root_dir."""
        from agentbase.extensions.tools.knowledge_ops import build_kb_ingest_tool
        from pathlib import Path

        ctx = {"knowledge_base": kb}
        tool_fn = build_kb_ingest_tool(context=ctx)
        # workspace will be Path("workspace") — file won't exist
        result = tool_fn.invoke({"path": "nonexistent.txt"})
        assert "not found" in result.lower() or "File not found" in result


class TestKbUpdateTool:
    def test_update_title(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        doc = kb.add_document(source="test.md", title="Old Title", content="Old content")
        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": doc.id, "title": "New Title"})
        assert "Updated" in result
        assert "New Title" in result

    def test_update_content(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        doc = kb.add_document(source="test.md", title="Doc", content="Old content")
        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": doc.id, "content": "New content here"})
        assert "Updated" in result

    def test_update_metadata(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        doc = kb.add_document(source="test.md", title="Doc", content="Content")
        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({
            "doc_id": doc.id,
            "metadata": json.dumps({"key": "val"}),
        })
        assert "Updated" in result
        updated = kb.get_document(doc_id=doc.id)
        assert updated.metadata["key"] == "val"

    def test_update_invalid_metadata(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        doc = kb.add_document(source="test.md", title="Doc", content="Content")
        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({
            "doc_id": doc.id,
            "metadata": "not json",
        })
        assert "Updated" in result
        updated = kb.get_document(doc_id=doc.id)
        assert updated.metadata["raw"] == "not json"

    def test_update_not_found(self, ctx):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": 99999, "title": "Whatever"})
        assert "not found" in result.lower()

    def test_update_nothing_to_update(self, ctx, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_update_tool

        doc = kb.add_document(source="test.md", title="Doc", content="Content")
        tool_fn = build_kb_update_tool(context=ctx)
        result = tool_fn.invoke({"doc_id": doc.id})
        assert "Nothing to update" in result


class TestKbBatchIngestTool:
    def test_batch_ingest_success(self, ctx, workspace, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        (workspace / "a.txt").write_text("alpha content", encoding="utf-8")
        (workspace / "b.txt").write_text("beta content", encoding="utf-8")

        tool_fn = build_kb_batch_ingest_tool(context=ctx)
        result = tool_fn.invoke({"directory": ".", "pattern": "*.txt"})
        assert "Ingested 2/2" in result
        assert "a.txt" in result
        assert "b.txt" in result

    def test_batch_ingest_dir_not_found(self, ctx, workspace):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        tool_fn = build_kb_batch_ingest_tool(context=ctx)
        result = tool_fn.invoke({"directory": "nonexistent_dir"})
        assert "Directory not found" in result

    def test_batch_ingest_no_files(self, ctx, workspace):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        (workspace / "subdir").mkdir()
        tool_fn = build_kb_batch_ingest_tool(context=ctx)
        result = tool_fn.invoke({"directory": "subdir"})
        assert "No files found" in result

    def test_batch_ingest_path_traversal(self, ctx, workspace):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        tool_fn = build_kb_batch_ingest_tool(context=ctx)
        result = tool_fn.invoke({"directory": "../../../etc"})
        assert "escapes" in result.lower() or "workspace" in result.lower()

    def test_batch_ingest_with_errors(self, ctx, workspace, kb):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool
        from unittest.mock import patch

        (workspace / "good.txt").write_text("good content", encoding="utf-8")
        (workspace / "bad.txt").write_text("bad content", encoding="utf-8")

        tool_fn = build_kb_batch_ingest_tool(context=ctx)

        # Mock ingest_file to fail on bad.txt
        original_ingest = kb.ingest_file
        call_count = [0]

        def mock_ingest(path, title=None):
            call_count[0] += 1
            if "bad" in str(path):
                raise RuntimeError("parse error")
            return original_ingest(path, title=title)

        with patch.object(kb, "ingest_file", side_effect=mock_ingest):
            result = tool_fn.invoke({"directory": ".", "pattern": "*.txt"})
            assert "1/2" in result
            assert "Errors" in result
            assert "bad.txt" in result
            assert "parse error" in result

    def test_batch_ingest_workspace_fallback_root_dir(self, kb, tmp_path):
        from agentbase.extensions.tools.knowledge_ops import build_kb_batch_ingest_tool

        root = tmp_path / "root"
        ws = root / "workspace"
        ws.mkdir(parents=True)
        (ws / "doc.txt").write_text("content", encoding="utf-8")

        ctx = {"knowledge_base": kb, "root_dir": str(root)}
        tool_fn = build_kb_batch_ingest_tool(context=ctx)
        result = tool_fn.invoke({"directory": "."})
        assert "Ingested 1/1" in result
