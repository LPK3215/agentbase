"""Unit tests for the conversation history core module."""
from __future__ import annotations

import threading

import pytest

from agentbase.core.conversation import (
    Conversation,
    ConversationFilter,
    ConversationManager,
    ConversationStats,
    InMemoryConversationProvider,
    Message,
    NullConversationProvider,
    _apply_filter,
    _auto_title,
    _extract_content,
    conversation_registry,
    extract_messages_from_result,
    get_conversation_manager,
    init_conversation_manager,
    register_conversation_provider,
    reset_conversation_manager,
    set_conversation_manager,
)

# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class TestMessage:
    """Tests for the Message data model."""

    def test_creation_minimal(self):
        m = Message(role="user", content="Hello")
        assert m.role == "user"
        assert m.content == "Hello"
        assert m.metadata == {}
        assert m.timestamp
        assert m.id  # auto-assigned

    def test_creation_full(self):
        m = Message(
            role="assistant",
            content="Hi there!",
            metadata={"tokens": 42},
            timestamp="2025-01-01T00:00:00+00:00",
            id="msg-001",
        )
        assert m.role == "assistant"
        assert m.content == "Hi there!"
        assert m.metadata == {"tokens": 42}
        assert m.timestamp == "2025-01-01T00:00:00+00:00"
        assert m.id == "msg-001"

    def test_auto_id_unique(self):
        m1 = Message(role="user", content="A")
        m2 = Message(role="user", content="B")
        assert m1.id != m2.id

    def test_to_dict(self):
        m = Message(role="user", content="Hello", metadata={"k": "v"}, id="m1")
        d = m.to_dict()
        assert d["id"] == "m1"
        assert d["role"] == "user"
        assert d["content"] == "Hello"
        assert d["metadata"] == {"k": "v"}
        assert "timestamp" in d


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

class TestConversation:
    """Tests for the Conversation data model."""

    def test_creation_minimal(self):
        c = Conversation(thread_id="t1")
        assert c.thread_id == "t1"
        assert c.agent_name == ""
        assert c.user_id == ""
        assert c.title == ""
        assert c.tags == []
        assert c.archived is False
        assert c.messages == []
        assert c.message_count == 0
        assert c.metadata == {}
        assert c.created_at
        assert c.updated_at
        assert c.finished_at == ""
        assert c.duration_ms is None

    def test_creation_full(self):
        msgs = [Message(role="user", content="Hi"), Message(role="assistant", content="Hello!")]
        c = Conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            title="Test Conversation",
            tags=["test", "demo"],
            archived=True,
            messages=msgs,
            message_count=2,
            metadata={"model": "gpt-4"},
            duration_ms=1500.0,
        )
        assert c.agent_name == "default"
        assert c.user_id == "u1"
        assert c.title == "Test Conversation"
        assert c.tags == ["test", "demo"]
        assert c.archived is True
        assert len(c.messages) == 2
        assert c.duration_ms == 1500.0

    def test_to_dict_with_messages(self):
        c = Conversation(
            thread_id="t1",
            agent_name="default",
            messages=[Message(role="user", content="Hi")],
            message_count=1,
        )
        d = c.to_dict(include_messages=True)
        assert "messages" in d
        assert len(d["messages"]) == 1

    def test_to_dict_without_messages(self):
        c = Conversation(
            thread_id="t1",
            agent_name="default",
            messages=[Message(role="user", content="Hi")],
            message_count=1,
        )
        d = c.to_dict(include_messages=False)
        assert "messages" not in d
        assert d["message_count"] == 1


# ---------------------------------------------------------------------------
# ConversationFilter
# ---------------------------------------------------------------------------

class TestConversationFilter:
    """Tests for ConversationFilter."""

    def test_default_all_none(self):
        f = ConversationFilter()
        assert f.user_id is None
        assert f.agent_name is None
        assert f.archived is None
        assert f.tag is None
        assert f.start_time is None
        assert f.end_time is None

    def test_set_fields(self):
        f = ConversationFilter(user_id="u1", agent_name="default", archived=True, tag="test")
        assert f.user_id == "u1"
        assert f.agent_name == "default"
        assert f.archived is True
        assert f.tag == "test"


# ---------------------------------------------------------------------------
# ConversationStats
# ---------------------------------------------------------------------------

class TestConversationStats:
    """Tests for ConversationStats."""

    def test_default_zero(self):
        s = ConversationStats()
        assert s.total_conversations == 0
        assert s.total_messages == 0
        assert s.avg_messages == 0.0
        assert s.conversations_by_agent == {}
        assert s.conversations_by_user == {}
        assert s.archived_count == 0

    def test_to_dict(self):
        s = ConversationStats(
            total_conversations=5,
            total_messages=20,
            avg_messages=4.0,
            conversations_by_agent={"default": 3, "researcher": 2},
            conversations_by_user={"u1": 4, "u2": 1},
            archived_count=1,
        )
        d = s.to_dict()
        assert d["total_conversations"] == 5
        assert d["total_messages"] == 20
        assert d["avg_messages"] == 4.0
        assert d["archived_count"] == 1


# ---------------------------------------------------------------------------
# NullConversationProvider
# ---------------------------------------------------------------------------

class TestNullConversationProvider:
    """Tests for NullConversationProvider."""

    def test_record_returns_conversation(self):
        p = NullConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert conv.thread_id == "t1"
        assert conv.message_count == 1
        assert len(conv.messages) == 0  # Null provider doesn't store messages

    def test_get_history_returns_none(self):
        p = NullConversationProvider()
        assert p.get_history(thread_id="t1") is None

    def test_list_returns_empty(self):
        p = NullConversationProvider()
        assert p.list_conversations() == []

    def test_update_raises_not_found(self):
        p = NullConversationProvider()
        from agentbase.runtime.errors import ErrorCode, RegistryError
        with pytest.raises(RegistryError) as exc_info:
            p.update_conversation(thread_id="t1", title="New")
        assert exc_info.value.code == ErrorCode.CONVERSATION_NOT_FOUND

    def test_delete_returns_false(self):
        p = NullConversationProvider()
        assert p.delete_conversation(thread_id="t1") is False

    def test_get_stats_returns_empty(self):
        p = NullConversationProvider()
        stats = p.get_stats()
        assert stats.total_conversations == 0

    def test_count_returns_zero(self):
        p = NullConversationProvider()
        assert p.count() == 0

    def test_close_noop(self):
        p = NullConversationProvider()
        p.close()  # should not raise


# ---------------------------------------------------------------------------
# InMemoryConversationProvider
# ---------------------------------------------------------------------------

class TestInMemoryConversationProvider:
    """Tests for InMemoryConversationProvider."""

    def _sample_messages(self) -> list[dict]:
        return [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

    def test_record_creates_conversation(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
        )
        assert conv.thread_id == "t1"
        assert conv.agent_name == "default"
        assert conv.user_id == "u1"
        assert conv.message_count == 2
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "Hi there!"

    def test_record_auto_title(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "What is the weather?"}],
        )
        assert conv.title == "What is the weather?"

    def test_record_explicit_title(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
            title="My Chat",
        )
        assert conv.title == "My Chat"

    def test_record_long_title_truncated(self):
        p = InMemoryConversationProvider()
        long_msg = "A" * 100
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": long_msg}],
        )
        assert len(conv.title) <= 80

    def test_record_no_user_message_uses_untitled(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "assistant", "content": "Hello!"}],
        )
        assert conv.title == "Untitled conversation"

    def test_record_updates_existing(self):
        p = InMemoryConversationProvider()
        p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "First"}],
            title="Original Title",
        )
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "Second"}, {"role": "assistant", "content": "Reply"}],
            title="Updated Title",
        )
        assert conv.title == "Updated Title"
        assert conv.message_count == 2
        assert conv.messages[0].content == "Second"
        assert conv.messages[1].content == "Reply"

    def test_record_with_tags(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
            tags=["important", "test"],
        )
        assert conv.tags == ["important", "test"]

    def test_record_with_duration(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
            duration_ms=500.0,
        )
        assert conv.duration_ms == 500.0
        assert conv.finished_at

    def test_record_with_metadata(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
            metadata={"model": "gpt-4", "request_id": "req-1"},
        )
        assert conv.metadata["model"] == "gpt-4"
        assert conv.metadata["request_id"] == "req-1"

    def test_record_empty_messages(self):
        p = InMemoryConversationProvider()
        conv = p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[],
        )
        assert conv.message_count == 0
        assert conv.title == "Untitled conversation"

    def test_get_history_found(self):
        p = InMemoryConversationProvider()
        p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
        )
        conv = p.get_history(thread_id="t1")
        assert conv is not None
        assert conv.thread_id == "t1"
        assert len(conv.messages) == 2

    def test_get_history_not_found(self):
        p = InMemoryConversationProvider()
        assert p.get_history(thread_id="nonexistent") is None

    def test_get_history_without_messages(self):
        p = InMemoryConversationProvider()
        p.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=self._sample_messages(),
        )
        conv = p.get_history(thread_id="t1", include_messages=False)
        assert conv is not None
        assert conv.message_count == 2
        assert len(conv.messages) == 0

    def test_list_conversations_empty(self):
        p = InMemoryConversationProvider()
        convs = p.list_conversations()
        assert convs == []

    def test_list_conversations_all(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "Hello"}])
        convs = p.list_conversations()
        assert len(convs) == 2

    def test_list_filter_by_user(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "Hello"}])
        f = ConversationFilter(user_id="u1")
        convs = p.list_conversations(filter=f)
        assert len(convs) == 1
        assert convs[0].thread_id == "t1"

    def test_list_filter_by_agent(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "Hello"}])
        f = ConversationFilter(agent_name="a2")
        convs = p.list_conversations(filter=f)
        assert len(convs) == 1
        assert convs[0].thread_id == "t2"

    def test_list_filter_by_archived(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "Hello"}])
        p.update_conversation(thread_id="t1", archived=True)
        f = ConversationFilter(archived=True)
        convs = p.list_conversations(filter=f)
        assert len(convs) == 1
        assert convs[0].thread_id == "t1"

    def test_list_filter_by_tag(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "Hi"}], tags=["important"])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "Hello"}], tags=["casual"])
        f = ConversationFilter(tag="important")
        convs = p.list_conversations(filter=f)
        assert len(convs) == 1
        assert convs[0].thread_id == "t1"

    def test_list_pagination(self):
        p = InMemoryConversationProvider()
        for i in range(10):
            p.record_conversation(thread_id=f"t{i}", agent_name="a", user_id="u", messages=[{"role": "user", "content": f"Msg {i}"}])
        convs = p.list_conversations(limit=3, offset=0)
        assert len(convs) == 3
        convs2 = p.list_conversations(limit=3, offset=3)
        assert len(convs2) == 3
        assert convs[0].thread_id != convs2[0].thread_id

    def test_list_sort_by_created_at_asc(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "A"}])
        p.record_conversation(thread_id="t2", agent_name="a", user_id="u", messages=[{"role": "user", "content": "B"}])
        convs = p.list_conversations(sort_by="created_at", sort_order="asc")
        assert convs[0].thread_id == "t1"

    def test_list_sort_by_message_count_desc(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "A"}])
        p.record_conversation(thread_id="t2", agent_name="a", user_id="u", messages=[{"role": "user", "content": "B"}, {"role": "assistant", "content": "C"}])
        convs = p.list_conversations(sort_by="message_count", sort_order="desc")
        assert convs[0].thread_id == "t2"

    def test_list_offset_beyond_range(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "A"}])
        convs = p.list_conversations(offset=100)
        assert convs == []

    def test_update_title(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}])
        conv = p.update_conversation(thread_id="t1", title="New Title")
        assert conv.title == "New Title"

    def test_update_tags(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}])
        conv = p.update_conversation(thread_id="t1", tags=["new", "tags"])
        assert conv.tags == ["new", "tags"]

    def test_update_archived(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}])
        conv = p.update_conversation(thread_id="t1", archived=True)
        assert conv.archived is True

    def test_update_metadata_merges(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}], metadata={"k1": "v1"})
        conv = p.update_conversation(thread_id="t1", metadata={"k2": "v2"})
        assert conv.metadata == {"k1": "v1", "k2": "v2"}

    def test_update_not_found(self):
        p = InMemoryConversationProvider()
        from agentbase.runtime.errors import ErrorCode, RegistryError
        with pytest.raises(RegistryError) as exc_info:
            p.update_conversation(thread_id="nonexistent", title="Test")
        assert exc_info.value.code == ErrorCode.CONVERSATION_NOT_FOUND

    def test_delete_existing(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}])
        assert p.delete_conversation(thread_id="t1") is True
        assert p.get_history(thread_id="t1") is None

    def test_delete_not_found(self):
        p = InMemoryConversationProvider()
        assert p.delete_conversation(thread_id="nonexistent") is False

    def test_get_stats_empty(self):
        p = InMemoryConversationProvider()
        stats = p.get_stats()
        assert stats.total_conversations == 0
        assert stats.total_messages == 0

    def test_get_stats_with_data(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}])
        p.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "C"}])
        stats = p.get_stats()
        assert stats.total_conversations == 2
        assert stats.total_messages == 3
        assert stats.avg_messages == 1.5
        assert stats.conversations_by_agent == {"a1": 1, "a2": 1}
        assert stats.conversations_by_user == {"u1": 1, "u2": 1}

    def test_count_empty(self):
        p = InMemoryConversationProvider()
        assert p.count() == 0

    def test_count_with_data(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "A"}])
        p.record_conversation(thread_id="t2", agent_name="a", user_id="u2", messages=[{"role": "user", "content": "B"}])
        assert p.count() == 2
        f = ConversationFilter(user_id="u1")
        assert p.count(filter=f) == 1

    def test_eviction(self):
        p = InMemoryConversationProvider(max_conversations=3)
        for i in range(5):
            p.record_conversation(thread_id=f"t{i}", agent_name="a", user_id="u", messages=[{"role": "user", "content": f"Msg {i}"}])
        # Should have evicted oldest 2
        assert p.count() == 3
        # t0 and t1 should be evicted (oldest)
        assert p.get_history(thread_id="t0") is None
        assert p.get_history(thread_id="t1") is None
        assert p.get_history(thread_id="t4") is not None

    def test_close_clears(self):
        p = InMemoryConversationProvider()
        p.record_conversation(thread_id="t1", agent_name="a", user_id="u", messages=[{"role": "user", "content": "Hi"}])
        p.close()
        assert p.count() == 0

    def test_thread_safety(self):
        p = InMemoryConversationProvider()
        errors: list[Exception] = []

        def worker(tid: str):
            try:
                for i in range(50):
                    p.record_conversation(
                        thread_id=f"{tid}-{i}",
                        agent_name="a",
                        user_id="u",
                        messages=[{"role": "user", "content": "Hi"}],
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"t{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert p.count() == 200


# ---------------------------------------------------------------------------
# ConversationRegistry
# ---------------------------------------------------------------------------

class TestConversationRegistry:
    """Tests for ConversationRegistry."""

    def test_register_builtin(self):
        assert conversation_registry.has("memory")
        assert conversation_registry.has("null")

    def test_create_memory(self):
        p = conversation_registry.create("memory")
        assert isinstance(p, InMemoryConversationProvider)

    def test_create_null(self):
        p = conversation_registry.create("null")
        assert isinstance(p, NullConversationProvider)

    def test_create_unknown(self):
        from agentbase.runtime.errors import ErrorCode, RegistryError
        with pytest.raises(RegistryError) as exc_info:
            conversation_registry.create("nonexistent")
        assert exc_info.value.code == ErrorCode.REG_NOT_FOUND

    def test_register_custom(self):
        @register_conversation_provider("custom_test")
        class CustomProvider(NullConversationProvider):
            pass

        assert conversation_registry.has("custom_test")
        p = conversation_registry.create("custom_test")
        assert isinstance(p, CustomProvider)

    def test_register_empty_name(self):
        from agentbase.runtime.errors import ErrorCode, RegistryError
        with pytest.raises(RegistryError) as exc_info:
            conversation_registry.register("", NullConversationProvider)
        assert exc_info.value.code == ErrorCode.REG_EMPTY_NAME

    def test_list_providers_includes_builtins(self):
        providers = conversation_registry.list_providers()
        assert "memory" in providers
        assert "null" in providers


# ---------------------------------------------------------------------------
# ConversationManager
# ---------------------------------------------------------------------------

class TestConversationManager:
    """Tests for ConversationManager facade."""

    def test_disabled_uses_null(self):
        mgr = ConversationManager(provider="memory", enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr._provider, NullConversationProvider)

    def test_enabled_memory(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr._provider, InMemoryConversationProvider)

    def test_unknown_provider_falls_back(self):
        mgr = ConversationManager(provider="nonexistent", enabled=True)
        assert isinstance(mgr._provider, InMemoryConversationProvider)

    def test_record_and_get(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "Hello"}],
        )
        conv = mgr.get_history(thread_id="t1")
        assert conv is not None
        assert conv.messages[0].content == "Hello"

    def test_disabled_record_returns_without_persisting(self):
        mgr = ConversationManager(provider="memory", enabled=False)
        conv = mgr.record_conversation(
            thread_id="t1",
            agent_name="default",
            user_id="u1",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert conv.thread_id == "t1"
        assert mgr.get_history(thread_id="t1") is None

    def test_list_via_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "A"}])
        mgr.record_conversation(thread_id="t2", agent_name="a", user_id="u2", messages=[{"role": "user", "content": "B"}])
        convs = mgr.list_conversations(user_id="u1")
        assert len(convs) == 1
        assert convs[0].thread_id == "t1"

    def test_update_via_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        conv = mgr.update_conversation(thread_id="t1", title="Updated")
        assert conv.title == "Updated"

    def test_delete_via_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        assert mgr.delete_conversation(thread_id="t1") is True
        assert mgr.get_history(thread_id="t1") is None

    def test_stats_via_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a1", user_id="u1", messages=[{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}])
        mgr.record_conversation(thread_id="t2", agent_name="a2", user_id="u2", messages=[{"role": "user", "content": "C"}])
        stats = mgr.get_stats()
        assert stats.total_conversations == 2
        assert stats.total_messages == 3

    def test_count_via_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "A"}])
        assert mgr.count() == 1
        assert mgr.count(user_id="u1") == 1
        assert mgr.count(user_id="nonexistent") == 0

    def test_close(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        mgr.record_conversation(thread_id="t1", agent_name="a", user_id="u1", messages=[{"role": "user", "content": "Hi"}])
        mgr.close()
        assert mgr.count() == 0


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

class TestSingletonManagement:
    """Tests for singleton management functions."""

    def setup_method(self):
        reset_conversation_manager()

    def teardown_method(self):
        reset_conversation_manager()

    def test_init_creates_singleton(self):
        mgr = init_conversation_manager(provider="memory", enabled=True)
        assert mgr is not None
        assert mgr.enabled is True

    def test_get_returns_initialised(self):
        mgr = init_conversation_manager(provider="memory", enabled=True)
        assert get_conversation_manager() is mgr

    def test_get_without_init_raises(self):
        with pytest.raises(RuntimeError):
            get_conversation_manager()

    def test_set_manager(self):
        mgr = ConversationManager(provider="memory", enabled=True)
        set_conversation_manager(mgr)
        assert get_conversation_manager() is mgr

    def test_reset_clears(self):
        init_conversation_manager(provider="memory", enabled=True)
        reset_conversation_manager()
        with pytest.raises(RuntimeError):
            get_conversation_manager()

    def test_double_init_returns_same(self):
        mgr1 = init_conversation_manager(provider="memory", enabled=True)
        mgr2 = init_conversation_manager(provider="memory", enabled=True)
        assert mgr1 is mgr2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    """Tests for helper functions."""

    def test_extract_content_string(self):
        assert _extract_content("Hello") == "Hello"

    def test_extract_content_none(self):
        assert _extract_content(None) == ""

    def test_extract_content_list_of_dicts(self):
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "World"},
        ]
        assert _extract_content(content) == "Hello World"

    def test_extract_content_list_of_strings(self):
        assert _extract_content(["A", "B"]) == "AB"

    def test_extract_content_dict(self):
        assert _extract_content({"key": "val"}) == "{'key': 'val'}"

    def test_auto_title_from_user_message(self):
        msgs = [Message(role="user", content="What is Python?")]
        assert _auto_title(msgs) == "What is Python?"

    def test_auto_title_truncated(self):
        long = "A" * 100
        msgs = [Message(role="user", content=long)]
        title = _auto_title(msgs)
        assert len(title) <= 80
        assert title.endswith("...")

    def test_auto_title_no_user_message(self):
        msgs = [Message(role="assistant", content="Hi")]
        assert _auto_title(msgs) == "Untitled conversation"

    def test_auto_title_empty_messages(self):
        assert _auto_title([]) == "Untitled conversation"

    def test_apply_filter_no_filter(self):
        convs = [
            Conversation(thread_id="t1", agent_name="a", user_id="u1"),
            Conversation(thread_id="t2", agent_name="b", user_id="u2"),
        ]
        assert len(_apply_filter(convs, ConversationFilter())) == 2

    def test_apply_filter_user_id(self):
        convs = [
            Conversation(thread_id="t1", agent_name="a", user_id="u1"),
            Conversation(thread_id="t2", agent_name="b", user_id="u2"),
        ]
        f = ConversationFilter(user_id="u1")
        assert len(_apply_filter(convs, f)) == 1

    def test_apply_filter_agent_name(self):
        convs = [
            Conversation(thread_id="t1", agent_name="a", user_id="u1"),
            Conversation(thread_id="t2", agent_name="b", user_id="u2"),
        ]
        f = ConversationFilter(agent_name="b")
        assert len(_apply_filter(convs, f)) == 1

    def test_apply_filter_archived(self):
        convs = [
            Conversation(thread_id="t1", archived=True),
            Conversation(thread_id="t2", archived=False),
        ]
        f = ConversationFilter(archived=True)
        assert len(_apply_filter(convs, f)) == 1

    def test_apply_filter_tag(self):
        convs = [
            Conversation(thread_id="t1", tags=["important", "test"]),
            Conversation(thread_id="t2", tags=["casual"]),
        ]
        f = ConversationFilter(tag="important")
        assert len(_apply_filter(convs, f)) == 1

    def test_apply_filter_time_range(self):
        convs = [
            Conversation(thread_id="t1", created_at="2025-01-01T00:00:00+00:00"),
            Conversation(thread_id="t2", created_at="2025-06-01T00:00:00+00:00"),
        ]
        f = ConversationFilter(start_time="2025-03-01T00:00:00+00:00")
        assert len(_apply_filter(convs, f)) == 1
        f2 = ConversationFilter(end_time="2025-03-01T00:00:00+00:00")
        assert len(_apply_filter(convs, f2)) == 1


class TestExtractMessagesFromResult:
    """Tests for extract_messages_from_result."""

    def test_extract_from_dict_with_messages(self):
        result = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ]
        }
        msgs = extract_messages_from_result(result)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Hi!"

    def test_extract_from_none(self):
        assert extract_messages_from_result(None) == []

    def test_extract_from_dict_without_messages(self):
        assert extract_messages_from_result({"other": "data"}) == []

    def test_extract_from_object_with_messages_attr(self):
        class FakeMessage:
            def __init__(self, role, content):
                self.type = role
                self.content = content
                self.id = "msg-1"
                self.response_metadata = {}

        class FakeResult:
            messages = [FakeMessage("user", "Hello"), FakeMessage("assistant", "World")]

        msgs = extract_messages_from_result(FakeResult())
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_extract_with_structured_content(self):
        result = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            ]
        }
        msgs = extract_messages_from_result(result)
        assert len(msgs) == 1
        # Content is a list, should be passed through
        assert msgs[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_extract_with_missing_role_uses_type(self):
        result = {
            "messages": [
                {"type": "human", "content": "Hello"},
            ]
        }
        msgs = extract_messages_from_result(result)
        assert msgs[0]["role"] == "human"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Verify providers implement the ConversationProvider protocol."""

    def test_in_memory_is_protocol_compliant(self):
        from agentbase.core.conversation import ConversationProvider
        p = InMemoryConversationProvider()
        assert isinstance(p, ConversationProvider)

    def test_null_is_protocol_compliant(self):
        from agentbase.core.conversation import ConversationProvider
        p = NullConversationProvider()
        assert isinstance(p, ConversationProvider)