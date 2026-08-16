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


# ---------------------------------------------------------------------------
# _get_mgr — error handling
# ---------------------------------------------------------------------------


class TestGetMgrErrors:
    def test_no_context_raises(self):
        from agentbase.extensions.tools.skill_ops import _get_mgr

        with pytest.raises(RuntimeError, match="skill_manager not available"):
            _get_mgr(None)

    def test_empty_context_raises(self):
        from agentbase.extensions.tools.skill_ops import _get_mgr

        with pytest.raises(RuntimeError, match="skill_manager not available"):
            _get_mgr({})

    def test_context_without_skill_manager_raises(self):
        from agentbase.extensions.tools.skill_ops import _get_mgr

        with pytest.raises(RuntimeError, match="skill_manager not available"):
            _get_mgr({"other_key": "value"})


# ---------------------------------------------------------------------------
# Supplementary tests — triggers, partial update
# ---------------------------------------------------------------------------


class TestSkillCreateWithTriggers:
    def test_create_with_triggers(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_create_tool

        tool_fn = build_skill_create_tool(context=ctx)
        result = tool_fn.invoke({
            "name": "trigger_skill",
            "description": "Has triggers",
            "body": "Body",
            "triggers": "foo, bar, baz",
        })
        assert "Created" in result
        skill = skill_mgr.get("trigger_skill")
        assert skill.triggers == ["foo", "bar", "baz"]

    def test_create_with_empty_triggers(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_create_tool

        tool_fn = build_skill_create_tool(context=ctx)
        result = tool_fn.invoke({
            "name": "no_triggers",
            "description": "No triggers",
            "body": "Body",
            "triggers": "",
        })
        assert "Created" in result
        skill = skill_mgr.get("no_triggers")
        assert skill.triggers == []


class TestSkillUpdatePartial:
    def test_update_description_only(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_update_tool

        skill_mgr.create(name="partial", description="Old desc", body="Old body")
        tool_fn = build_skill_update_tool(context=ctx)
        result = tool_fn.invoke({"name": "partial", "description": "New desc"})
        assert "Updated" in result
        updated = skill_mgr.get("partial")
        assert updated.description == "New desc"
        # Body should be unchanged
        assert "Old body" in updated.body

    def test_update_with_triggers(self, ctx, skill_mgr):
        from agentbase.extensions.tools.skill_ops import build_skill_update_tool

        skill_mgr.create(name="trig_upd", description="Desc", body="Body")
        tool_fn = build_skill_update_tool(context=ctx)
        result = tool_fn.invoke({
            "name": "trig_upd",
            "triggers": "alpha, beta",
        })
        assert "Updated" in result
        updated = skill_mgr.get("trig_upd")
        assert updated.triggers == ["alpha", "beta"]

    def test_update_all_empty_fields(self, ctx, skill_mgr):
        """All empty fields — update should succeed with no changes."""
        from agentbase.extensions.tools.skill_ops import build_skill_update_tool

        skill_mgr.create(name="nochange", description="Keep this", body="Keep body")
        tool_fn = build_skill_update_tool(context=ctx)
        result = tool_fn.invoke({
            "name": "nochange",
            "description": "",
            "body": "",
            "triggers": "",
        })
        assert "Updated" in result
        updated = skill_mgr.get("nochange")
        assert updated.description == "Keep this"
