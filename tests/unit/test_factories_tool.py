"""Unit tests for tool_factory."""
from __future__ import annotations

import pytest

from agentbase.factories.tool_factory import build_tools


class TestBuildTools:
    def test_build_known_tools(self, bootstrapped):
        """Test building well-known tools that need no context."""
        tools = build_tools(["echo", "get_time"], context={})
        assert len(tools) == 2

    def test_build_empty_list(self):
        tools = build_tools([], context={})
        assert tools == []

    def test_build_unknown_tool_raises(self, bootstrapped):
        # By default unknown tools are skipped (graceful degradation); in
        # strict mode (skip_on_error=False) an unknown tool must raise.
        with pytest.raises(Exception, match="Unknown tool"):
            build_tools(["nonexistent_tool_xyz"], context={}, skip_on_error=False)

    def test_build_tools_with_context(self, tmp_path, bootstrapped):
        """Test building tools that require workspace_dir context."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        tools = build_tools(
            ["echo", "list_workspace"],
            context={"workspace_dir": workspace},
        )
        assert len(tools) == 2

    def test_build_tool_returns_none_raises(self, bootstrapped):
        """If a builder returns None, FactoryError should be raised."""
        from agentbase.registry.tools import register_tool

        @register_tool("null_tool", override=True)
        def build_null(context=None):
            return None

        from agentbase.factories.tool_factory import build_tools as _bt
        with pytest.raises(Exception, match="returned None"):
            _bt(["null_tool"], context={}, skip_on_error=False)

    def test_build_all_default_agent_tools(self, tmp_path, bootstrapped):
        """Test building the full default agent toolset end-to-end."""
        from agentbase.core.knowledge import KnowledgeBase
        from agentbase.core.memory import MemoryManager
        from agentbase.core.skills import SkillManager

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "skills").mkdir()

        ctx = {
            "root_dir": tmp_path,
            "workspace_dir": workspace,
            "skill_manager": SkillManager(skills_dir=workspace / "skills"),
            "memory_manager": MemoryManager(db_path=tmp_path / "mem.db"),
            "knowledge_base": KnowledgeBase(db_path=tmp_path / "kb.db"),
            "search_provider": None,
        }

        # Build the exact toolset from default.yaml
        tool_names = [
            "echo", "list_workspace", "get_time", "read_file", "write_file",
            "grep", "now_local",
            "skill_list", "skill_get", "skill_create", "skill_update",
            "skill_delete", "skill_search",
            "memory_save", "memory_get", "memory_list", "memory_search",
            "memory_delete",
            "kb_add", "kb_get", "kb_list", "kb_search", "kb_update",
            "kb_delete", "kb_ingest", "kb_batch_ingest",
            "web_search", "web_fetch",
        ]
        tools = build_tools(tool_names, context=ctx)
        assert len(tools) == len(tool_names)
        # Verify each tool is callable (has invoke method)
        for t in tools:
            assert hasattr(t, "invoke")
