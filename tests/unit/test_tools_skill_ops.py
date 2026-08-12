"""Unit tests for skill_ops tools."""
from __future__ import annotations

import pytest

from agentbase.core.skills import SkillManager


@pytest.fixture
def skill_mgr(tmp_path):
    return SkillManager(skills_dir=tmp_path / "skills")


@pytest.fixture
def ctx(skill_mgr):
    return {"skill_manager": skill_mgr}


class TestSkillListTool:
    def test_empty(self, ctx):
        from agentbase.extensions.tools.skill_ops import build_skill_list_tool

        tool_fn = build_skill_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "no skills" in result.lower() or result == ""

    def test_with_skills(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_list_tool

        skill_mgr.create(name="test_skill", description="A test", body="Body")
        tool_fn = build_skill_list_tool(context=ctx)
        result = tool_fn.invoke({})
        assert "test_skill" in result


class TestSkillGetTool:
    def test_get_existing(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_get_tool

        skill_mgr.create(name="my_skill", description="Desc", body="Content here")
        tool_fn = build_skill_get_tool(context=ctx)
        result = tool_fn.invoke({"name": "my_skill"})
        assert "my_skill" in result
        assert "Content here" in result

    def test_get_not_found(self, ctx):
        from agentbase.extensions.tools.skill_ops import build_skill_get_tool

        tool_fn = build_skill_get_tool(context=ctx)
        result = tool_fn.invoke({"name": "nonexistent"})
        assert "not found" in result.lower()


class TestSkillCreateTool:
    def test_create(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_create_tool

        tool_fn = build_skill_create_tool(context=ctx)
        result = tool_fn.invoke({"name": "new_skill", "description": "New", "body": "Body"})
        assert "Created" in result or "new_skill" in result
        assert skill_mgr.get("new_skill") is not None

    def test_create_duplicate(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_create_tool

        skill_mgr.create(name="dup", description="First", body="B")
        tool_fn = build_skill_create_tool(context=ctx)
        result = tool_fn.invoke({"name": "dup", "description": "Second", "body": "B2"})
        assert "already" in result.lower() or "exists" in result.lower()


class TestSkillUpdateTool:
    def test_update(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_update_tool

        skill_mgr.create(name="upd", description="Old", body="Old body")
        tool_fn = build_skill_update_tool(context=ctx)
        result = tool_fn.invoke({"name": "upd", "description": "New desc", "body": "New body"})
        assert "Updated" in result or "upd" in result
        updated = skill_mgr.get("upd")
        assert updated.description == "New desc"

    def test_update_not_found(self, ctx):
        from agentbase.extensions.tools.skill_ops import build_skill_update_tool

        tool_fn = build_skill_update_tool(context=ctx)
        result = tool_fn.invoke({"name": "missing", "description": "X"})
        assert "not found" in result.lower()


class TestSkillDeleteTool:
    def test_delete(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_delete_tool

        skill_mgr.create(name="del", description="To delete", body="B")
        tool_fn = build_skill_delete_tool(context=ctx)
        result = tool_fn.invoke({"name": "del"})
        assert "Deleted" in result
        with pytest.raises(KeyError):
            skill_mgr.get("del")

    def test_delete_not_found(self, ctx):
        from agentbase.extensions.tools.skill_ops import build_skill_delete_tool

        tool_fn = build_skill_delete_tool(context=ctx)
        result = tool_fn.invoke({"name": "missing"})
        assert "not found" in result.lower()


class TestSkillSearchTool:
    def test_search_found(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_search_tool

        skill_mgr.create(name="python_skill", description="Python automation", body="Python code")
        skill_mgr.create(name="java_skill", description="Java automation", body="Java code")
        tool_fn = build_skill_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "Python"})
        assert "python_skill" in result
        assert "java_skill" not in result

    def test_search_no_results(self, ctx):
        from agentbase.extensions.tools.skill_ops import build_skill_search_tool

        tool_fn = build_skill_search_tool(context=ctx)
        result = tool_fn.invoke({"query": "nonexistent_xyz"})
        assert "no skills" in result.lower()
