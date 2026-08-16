"""Unit tests for memory_ops tools."""
from __future__ import annotations

import pytest

from agentbase.core.memory import MemoryManager


@pytest.fixture
def mem_mgr(tmp_path):
    return MemoryManager(db_path=tmp_path / "mem.db")


@pytest.fixture
def ctx(mem_mgr):
    return {"memory_manager": mem_mgr}


class TestMemorySaveTool:
    def test_save(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_save_tool

        tool_fn = build_memory_save_tool(context=ctx)
        result = tool_fn.invoke({"key": "pref1", "content": "likes concise", "agent": "default"})
        assert "Saved" in result or "pref1" in result
        mem = mem_mgr.get(agent_name="default", key="pref1")
        assert mem.content == "likes concise"

    def test_save_upsert(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_save_tool

        mem_mgr.save(agent_name="default", key="k1", content="v1")
        tool_fn = build_memory_save_tool(context=ctx)
        result = tool_fn.invoke({"key": "k1", "content": "v2", "agent": "default"})
        assert "Saved" in result or "Updated" in result
        mem = mem_mgr.get(agent_name="default", key="k1")
        assert mem.content == "v2"

    def test_save_with_tags(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_save_tool

        tool_fn = build_memory_save_tool(context=ctx)
        result = tool_fn.invoke({
            "key": "tagged",
            "content": "content",
            "agent": "default",
            "tags": "important, user_pref",
        })
        assert "Saved" in result
        mem = mem_mgr.get(agent_name="default", key="tagged")
        assert "important" in mem.tags
        assert "user_pref" in mem.tags


class TestMemoryGetTool:
    def test_get_existing(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_get_tool

        mem_mgr.save(agent_name="default", key="k1", content="hello world")
        tool_fn = build_memory_get_tool(context=ctx)
        result = tool_fn.invoke({"key": "k1", "agent": "default"})
        assert "hello world" in result

    def test_get_not_found(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_get_tool

        tool_fn = build_memory_get_tool(context=ctx)
        result = tool_fn.invoke({"key": "missing", "agent": "default"})
        assert "not found" in result.lower()


class TestMemoryListTool:
    def test_list_all(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_list_tool

        mem_mgr.save(agent_name="default", key="k1", content="c1")
        mem_mgr.save(agent_name="default", key="k2", content="c2")
        tool_fn = build_memory_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "k1" in result
        assert "k2" in result

    def test_list_empty(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_list_tool

        tool_fn = build_memory_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "no memories" in result.lower() or result == ""

    def test_list_by_agent(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_list_tool

        mem_mgr.save(agent_name="agent_a", key="k1", content="a")
        mem_mgr.save(agent_name="agent_b", key="k2", content="b")
        tool_fn = build_memory_list_tool(context=ctx)
        result = tool_fn.invoke({"agent": "agent_a"})
        assert "k1" in result
        assert "k2" not in result


class TestMemorySearchTool:
    def test_search_found(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_search_tool

        mem_mgr.save(agent_name="default", key="food", content="pizza is great")
        mem_mgr.save(agent_name="default", key="drink", content="coffee is essential")
        tool_fn = build_memory_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "coffee"})
        assert "coffee" in result.lower() or "drink" in result

    def test_search_no_results(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_search_tool

        tool_fn = build_memory_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "nonexistent_xyz"})
        assert "no memories" in result.lower()


class TestMemoryDeleteTool:
    def test_delete(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_delete_tool

        mem_mgr.save(agent_name="default", key="temp", content="temp")
        tool_fn = build_memory_delete_tool(context=ctx)
        result = tool_fn.invoke({"key": "temp", "agent": "default"})
        assert "Deleted" in result
        with pytest.raises(KeyError):
            mem_mgr.get(agent_name="default", key="temp")

    def test_delete_not_found(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_delete_tool

        tool_fn = build_memory_delete_tool(context=ctx)
        result = tool_fn.invoke({"key": "missing", "agent": "default"})
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage
# ---------------------------------------------------------------------------


class TestMemoryOpsExtras:
    def test_get_mgr_no_context(self):
        from agentbase.extensions.tools.memory_ops import _get_mgr

        with pytest.raises(RuntimeError, match="memory_manager not available"):
            _get_mgr(None)

    def test_get_mgr_missing_key(self):
        from agentbase.extensions.tools.memory_ops import _get_mgr

        with pytest.raises(RuntimeError, match="memory_manager not available"):
            _get_mgr({"other_key": "value"})

    def test_save_with_tags_and_metadata(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_save_tool

        tool_fn = build_memory_save_tool(context=ctx)
        result = tool_fn.invoke({
            "key": "tagged",
            "content": "tagged content",
            "agent": "default",
            "tags": "alpha,beta",
            "metadata": '{"priority": "high"}',
        })
        assert "Saved" in result
        mem = mem_mgr.get(agent_name="default", key="tagged")
        assert "alpha" in mem.tags
        assert "beta" in mem.tags
        assert mem.metadata["priority"] == "high"

    def test_save_with_invalid_metadata(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_save_tool

        tool_fn = build_memory_save_tool(context=ctx)
        result = tool_fn.invoke({
            "key": "bad_meta",
            "content": "content",
            "agent": "default",
            "metadata": "not valid json",
        })
        assert "Saved" in result
        mem = mem_mgr.get(agent_name="default", key="bad_meta")
        assert mem.metadata["raw"] == "not valid json"

    def test_get_not_found(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_get_tool

        tool_fn = build_memory_get_tool(context=ctx)
        result = tool_fn.invoke({"key": "nonexistent", "agent": "default"})
        assert "not found" in result.lower() or "no memory" in result.lower()

    def test_list_empty(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_list_tool

        tool_fn = build_memory_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "no memories" in result.lower()

    def test_list_with_filter(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_list_tool

        mem_mgr.save(agent_name="a1", key="k1", content="content1")
        mem_mgr.save(agent_name="a2", key="k2", content="content2")

        tool_fn = build_memory_list_tool(context=ctx)
        result = tool_fn.invoke({"agent": "a1"})
        assert "a1/k1" in result
        assert "a2/k2" not in result

    def test_search_empty(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_search_tool

        tool_fn = build_memory_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "nothing matches this"})
        assert "no memories" in result.lower()

    def test_search_with_results(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_search_tool

        mem_mgr.save(agent_name="default", key="k1", content="python is great")
        tool_fn = build_memory_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "python"})
        assert "python" in result

    def test_count_no_filter(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_count_tool

        mem_mgr.save(agent_name="default", key="k1", content="c1")
        mem_mgr.save(agent_name="default", key="k2", content="c2")

        tool_fn = build_memory_count_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "2" in result

    def test_count_with_agent(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_count_tool

        mem_mgr.save(agent_name="a1", key="k1", content="c1")
        mem_mgr.save(agent_name="a2", key="k2", content="c2")

        tool_fn = build_memory_count_tool(context=ctx)
        result = tool_fn.invoke({"agent": "a1"})
        assert "1" in result
        assert "a1" in result

    def test_batch_save_success(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool

        items = '[{"key": "b1", "content": "batch1"}, {"key": "b2", "content": "batch2", "tags": "tag1"}]'
        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": items, "agent": "default"})
        assert "Batch saved 2" in result
        assert mem_mgr.get(agent_name="default", key="b1").content == "batch1"

    def test_batch_save_invalid_json(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool

        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": "not json", "agent": "default"})
        assert "Invalid JSON" in result

    def test_batch_save_not_array(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool

        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": '{"key": "val"}', "agent": "default"})
        assert "array" in result

    def test_batch_save_no_valid_entries(self, ctx):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool

        items = '[{"no_key": "missing key and content"}]'
        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": items, "agent": "default"})
        assert "No valid entries" in result

    def test_batch_save_with_metadata(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool
        import json

        inner_meta = json.dumps({"p": 1})
        items = json.dumps([{"key": "b1", "content": "c1", "metadata": inner_meta}])
        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": items, "agent": "default"})
        assert "Batch saved 1" in result
        mem = mem_mgr.get(agent_name="default", key="b1")
        assert mem.metadata["p"] == 1

    def test_batch_save_with_invalid_metadata(self, ctx, mem_mgr):
        from agentbase.extensions.tools.memory_ops import build_memory_batch_save_tool
        import json

        items = json.dumps([{"key": "b1", "content": "c1", "metadata": "bad json"}])
        tool_fn = build_memory_batch_save_tool(context=ctx)
        result = tool_fn.invoke({"items": items, "agent": "default"})
        assert "Batch saved 1" in result
        mem = mem_mgr.get(agent_name="default", key="b1")
        assert mem.metadata["raw"] == "bad json"
