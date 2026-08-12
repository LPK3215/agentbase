"""Unit tests for SkillManager."""
from __future__ import annotations

import pytest

from agentbase.core.skills import SkillManager, _parse_skill


@pytest.fixture
def skill_mgr(tmp_path):
    return SkillManager(skills_dir=tmp_path / "skills")


class TestSkillManager:
    def test_create_and_get(self, skill_mgr):
        skill = skill_mgr.create("test_skill", description="A test", body="# Test\nHello")
        assert skill.name == "test_skill"
        assert skill.description == "A test"
        assert "Hello" in skill.body

        fetched = skill_mgr.get("test_skill")
        assert fetched.name == "test_skill"
        assert fetched.description == "A test"
        assert "Hello" in fetched.body

    def test_create_duplicate_raises(self, skill_mgr):
        skill_mgr.create("dup", body="first")
        with pytest.raises(ValueError, match="already exists"):
            skill_mgr.create("dup", body="second")

    def test_get_not_found_raises(self, skill_mgr):
        with pytest.raises(KeyError, match="not found"):
            skill_mgr.get("nonexistent")

    def test_update(self, skill_mgr):
        skill_mgr.create("update_me", description="old", body="old body")
        skill_mgr.update("update_me", description="new desc", body="new body")
        fetched = skill_mgr.get("update_me")
        assert fetched.description == "new desc"
        assert fetched.body == "new body"

    def test_update_partial(self, skill_mgr):
        skill_mgr.create("partial", description="keep", body="original")
        skill_mgr.update("partial", body="changed")
        fetched = skill_mgr.get("partial")
        assert fetched.description == "keep"  # unchanged
        assert fetched.body == "changed"

    def test_delete(self, skill_mgr):
        skill_mgr.create("delete_me", body="temp")
        assert skill_mgr.delete("delete_me") is True
        assert skill_mgr.delete("delete_me") is False  # already deleted

    def test_list(self, skill_mgr):
        skill_mgr.create("alpha", body="a")
        skill_mgr.create("beta", body="b")
        names = [s.name for s in skill_mgr.list()]
        assert names == ["alpha", "beta"]

    def test_list_empty(self, skill_mgr):
        assert skill_mgr.list() == []

    def test_search(self, skill_mgr):
        skill_mgr.create("python_tool", description="Python helper", body="runs python code")
        skill_mgr.create("json_tool", description="JSON formatter", body="formats json")
        results = skill_mgr.search("python")
        assert len(results) == 1
        assert results[0].name == "python_tool"

    def test_search_no_results(self, skill_mgr):
        skill_mgr.create("alpha", body="hello")
        assert skill_mgr.search("nonexistent") == []

    def test_path_traversal_safe(self, skill_mgr):
        # Path traversal attempts should be sanitized
        skill_mgr.create("../../../etc/passwd", body="safe")
        # Should create a file with sanitized name, not traverse
        assert skill_mgr.get("../../../etc/passwd").body == "safe"

    def test_skill_with_triggers(self, skill_mgr):
        skill_mgr.create("triggered", description="has triggers", body="body", triggers=["on_start", "on_end"])
        fetched = skill_mgr.get("triggered")
        assert fetched.triggers == ["on_start", "on_end"]
        assert "on_start" in fetched.content

    def test_parse_skill_without_frontmatter(self):
        skill = _parse_skill("# Just markdown\nNo frontmatter.")
        assert skill.name == ""
        assert skill.description == ""
        assert "Just markdown" in skill.body

    def test_parse_skill_with_frontmatter(self):
        raw = "---\nname: test\ndescription: A test\ntriggers:\n  - on_start\n---\n\n# Body"
        skill = _parse_skill(raw)
        assert skill.name == "test"
        assert skill.description == "A test"
        assert skill.triggers == ["on_start"]
        assert "Body" in skill.body

    def test_skill_to_dict(self, skill_mgr):
        skill_mgr.create("dict_test", description="dict", body="body")
        d = skill_mgr.get("dict_test").to_dict()
        assert d["name"] == "dict_test"
        assert d["description"] == "dict"
        assert "file_path" in d
