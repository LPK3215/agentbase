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

    # --- Supplementary tests for missing branches ---

    def test_build_unknown_skip_on_error(self, bootstrapped):
        """Unknown tool with skip_on_error=True should be skipped, not raise."""
        result = build_tools(["nonexistent_xyz"], context={}, skip_on_error=True)
        assert result == []

    def test_build_tool_typeerror_fallback_success(self):
        """If builder(context=...) raises TypeError, fallback to builder() should work."""
        from agentbase.registry.tools import register_tool

        @register_tool("fallback_tool_tf", override=True)
        def build_fallback(context=None):
            if context is not None:
                raise TypeError("no context arg")
            return "tool_instance"

        result = build_tools(["fallback_tool_tf"], context={}, skip_on_error=False)
        assert len(result) == 1
        assert result[0] == "tool_instance"

    def test_build_tool_typeerror_fallback_also_fails_skip(self):
        """If both builder(context=...) and builder() fail, skip with skip_on_error=True."""
        from agentbase.registry.tools import register_tool

        @register_tool("double_fail_tool", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        result = build_tools(["double_fail_tool"], context={}, skip_on_error=True)
        assert result == []

    def test_build_tool_typeerror_fallback_also_fails_raise(self):
        """If both builder(context=...) and builder() fail, raise with skip_on_error=False."""
        from agentbase.registry.tools import register_tool
        from agentbase.runtime.errors import FactoryError

        @register_tool("double_fail_tool2", override=True)
        def build_double_fail(context=None):
            if context is not None:
                raise TypeError("no context arg")
            raise RuntimeError("always fails")

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_tools(["double_fail_tool2"], context={}, skip_on_error=False)

    def test_build_tool_exception_skip(self):
        """If builder raises generic Exception, skip with skip_on_error=True."""
        from agentbase.registry.tools import register_tool

        @register_tool("exc_tool", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        result = build_tools(["exc_tool"], context={}, skip_on_error=True)
        assert result == []

    def test_build_tool_exception_raise(self):
        """If builder raises generic Exception, raise with skip_on_error=False."""
        from agentbase.registry.tools import register_tool
        from agentbase.runtime.errors import FactoryError

        @register_tool("exc_tool2", override=True)
        def build_exc(context=None):
            raise ValueError("builder error")

        with pytest.raises(FactoryError, match="builder.*failed"):
            build_tools(["exc_tool2"], context={}, skip_on_error=False)

    def test_build_tool_returns_none_skip(self):
        """If builder returns None, skip with skip_on_error=True."""
        from agentbase.registry.tools import register_tool

        @register_tool("null_tool_skip", override=True)
        def build_null(context=None):
            return None

        result = build_tools(["null_tool_skip"], context={}, skip_on_error=True)
        assert result == []

    def test_build_mixed_skip_and_success(self):
        """Mix of valid and invalid tools should return only valid ones."""
        from agentbase.registry.tools import register_tool

        @register_tool("valid_mixed_tool", override=True)
        def build_valid(context=None):
            return "valid_tool"

        result = build_tools(
            ["valid_mixed_tool", "nonexistent_xyz"],
            context={},
            skip_on_error=True,
        )
        assert len(result) == 1
        assert result[0] == "valid_tool"
