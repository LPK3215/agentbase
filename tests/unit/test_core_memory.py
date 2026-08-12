"""Unit tests for MemoryManager."""
from __future__ import annotations

import pytest

from agentbase.core.memory import MemoryManager


@pytest.fixture
def mem_mgr(tmp_path):
    return MemoryManager(db_path=tmp_path / "test_memory.db")


class TestMemoryManager:
    def test_save_and_get(self, mem_mgr):
        mem = mem_mgr.save(agent_name="default", key="pref1", content="likes concise answers")
        assert mem.key == "pref1"
        assert mem.content == "likes concise answers"
        assert mem.id is not None

        fetched = mem_mgr.get(agent_name="default", key="pref1")
        assert fetched.content == "likes concise answers"

    def test_save_upsert(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="k1", content="v1")
        mem_mgr.save(agent_name="default", key="k1", content="v2")
        fetched = mem_mgr.get(agent_name="default", key="k1")
        assert fetched.content == "v2"

    def test_get_not_found(self, mem_mgr):
        with pytest.raises(KeyError, match="not found"):
            mem_mgr.get(agent_name="default", key="nonexistent")

    def test_save_with_tags(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="tagged", content="content", tags=["important", "user_pref"])
        mem = mem_mgr.get(agent_name="default", key="tagged")
        assert "important" in mem.tags
        assert "user_pref" in mem.tags

    def test_save_with_metadata(self, mem_mgr):
        mem_mgr.save(
            agent_name="default",
            key="meta",
            content="content",
            metadata={"source": "test", "confidence": 0.9},
        )
        mem = mem_mgr.get(agent_name="default", key="meta")
        assert mem.metadata["source"] == "test"
        assert mem.metadata["confidence"] == 0.9

    def test_list_all(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="k1", content="c1")
        mem_mgr.save(agent_name="default", key="k2", content="c2")
        mem_mgr.save(agent_name="coder", key="k1", content="c3")
        all_mems = mem_mgr.list()
        assert len(all_mems) == 3

    def test_list_by_agent(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="k1", content="c1")
        mem_mgr.save(agent_name="coder", key="k2", content="c2")
        default_mems = mem_mgr.list(agent_name="default")
        assert len(default_mems) == 1
        assert default_mems[0].key == "k1"

    def test_list_by_tag(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="k1", content="c1", tags=["important"])
        mem_mgr.save(agent_name="default", key="k2", content="c2", tags=["minor"])
        tagged = mem_mgr.list(tag="important")
        assert len(tagged) == 1
        assert tagged[0].key == "k1"

    def test_search(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="food", content="pizza is great")
        mem_mgr.save(agent_name="default", key="drink", content="coffee is essential")
        results = mem_mgr.search(query="coffee")
        assert len(results) == 1
        assert results[0].key == "drink"

    def test_search_by_key(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="python_config", content="some config")
        results = mem_mgr.search(query="python")
        assert len(results) == 1

    def test_search_with_agent_filter(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="k1", content="shared content")
        mem_mgr.save(agent_name="coder", key="k2", content="shared content")
        results = mem_mgr.search(query="shared", agent_name="coder")
        assert len(results) == 1
        assert results[0].agent_name == "coder"

    def test_delete(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="temp", content="temp")
        assert mem_mgr.delete(agent_name="default", key="temp") is True
        assert mem_mgr.delete(agent_name="default", key="temp") is False

    def test_agent_isolation(self, mem_mgr):
        mem_mgr.save(agent_name="agent_a", key="shared_key", content="a's content")
        mem_mgr.save(agent_name="agent_b", key="shared_key", content="b's content")
        a_mem = mem_mgr.get(agent_name="agent_a", key="shared_key")
        b_mem = mem_mgr.get(agent_name="agent_b", key="shared_key")
        assert a_mem.content == "a's content"
        assert b_mem.content == "b's content"

    def test_to_dict(self, mem_mgr):
        mem_mgr.save(agent_name="default", key="dict_test", content="c", tags=["t1"])
        d = mem_mgr.get(agent_name="default", key="dict_test").to_dict()
        assert d["key"] == "dict_test"
        assert d["tags"] == ["t1"]
        assert "created_at" in d
