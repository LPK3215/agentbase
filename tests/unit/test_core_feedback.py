"""Unit tests for the feedback collection core module."""
from __future__ import annotations

import threading

import pytest

from agentbase.core.feedback import (
    FeedbackFilter,
    FeedbackManager,
    FeedbackRecord,
    FeedbackRegistry,
    FeedbackStats,
    InMemoryFeedbackProvider,
    NullFeedbackProvider,
    _apply_feedback_filter,
    _compute_stats,
    feedback_registry,
    get_feedback_manager,
    register_feedback_provider,
    reset_feedback_manager,
    set_feedback_manager,
)

# ---------------------------------------------------------------------------
# FeedbackRecord
# ---------------------------------------------------------------------------

class TestFeedbackRecord:
    """Tests for the FeedbackRecord data model."""

    def test_record_creation_minimal(self):
        """A record can be created with only thread_id."""
        r = FeedbackRecord(thread_id="t1")
        assert r.thread_id == "t1"
        assert r.message_id == ""
        assert r.rating is None
        assert r.comment == ""
        assert r.user_id == ""
        assert r.agent_name == ""
        assert r.tags == []
        assert r.metadata == {}
        assert r.id  # auto-assigned
        assert r.created_at  # auto-assigned

    def test_record_creation_full(self):
        """A record can be created with all fields."""
        r = FeedbackRecord(
            thread_id="t1",
            message_id="m1",
            rating=5,
            comment="Great!",
            user_id="u1",
            agent_name="agent1",
            tags=["helpful"],
            metadata={"key": "val"},
        )
        assert r.thread_id == "t1"
        assert r.message_id == "m1"
        assert r.rating == 5
        assert r.comment == "Great!"
        assert r.user_id == "u1"
        assert r.agent_name == "agent1"
        assert r.tags == ["helpful"]
        assert r.metadata == {"key": "val"}

    def test_record_auto_id(self):
        """Records get auto-assigned unique IDs."""
        r1 = FeedbackRecord(thread_id="t1")
        r2 = FeedbackRecord(thread_id="t1")
        assert r1.id != r2.id
        assert len(r1.id) == 16

    def test_record_custom_id(self):
        """Records can have a custom ID."""
        r = FeedbackRecord(thread_id="t1", id="custom-id")
        assert r.id == "custom-id"

    def test_record_sentiment_5_star_positive(self):
        """Rating >= 4 → positive sentiment."""
        r = FeedbackRecord(thread_id="t1", rating=4)
        assert r.sentiment == "positive"
        r2 = FeedbackRecord(thread_id="t1", rating=5)
        assert r2.sentiment == "positive"

    def test_record_sentiment_5_star_neutral(self):
        """Rating == 3 → neutral sentiment."""
        r = FeedbackRecord(thread_id="t1", rating=3)
        assert r.sentiment == "neutral"

    def test_record_sentiment_5_star_negative(self):
        """Rating <= 2 (but > 1) → negative sentiment."""
        r = FeedbackRecord(thread_id="t1", rating=2)
        assert r.sentiment == "negative"
        r2 = FeedbackRecord(thread_id="t1", rating=1.5)
        assert r2.sentiment == "negative"

    def test_record_sentiment_thumbs_up(self):
        """Rating +1 (thumbs up) → positive."""
        r = FeedbackRecord(thread_id="t1", rating=1)
        assert r.sentiment == "positive"

    def test_record_sentiment_thumbs_down(self):
        """Rating -1 (thumbs down) → negative."""
        r = FeedbackRecord(thread_id="t1", rating=-1)
        assert r.sentiment == "negative"

    def test_record_sentiment_no_rating(self):
        """No rating → unknown."""
        r = FeedbackRecord(thread_id="t1", rating=None)
        assert r.sentiment == "unknown"

    def test_record_to_dict(self):
        """to_dict returns all fields."""
        r = FeedbackRecord(
            thread_id="t1",
            message_id="m1",
            rating=5,
            comment="Great!",
            user_id="u1",
            agent_name="agent1",
            tags=["helpful", "fast"],
            metadata={"key": "val"},
        )
        d = r.to_dict()
        assert d["thread_id"] == "t1"
        assert d["message_id"] == "m1"
        assert d["rating"] == 5
        assert d["comment"] == "Great!"
        assert d["user_id"] == "u1"
        assert d["agent_name"] == "agent1"
        assert d["tags"] == ["helpful", "fast"]
        assert d["metadata"] == {"key": "val"}
        assert d["sentiment"] == "positive"
        assert "id" in d
        assert "created_at" in d


# ---------------------------------------------------------------------------
# FeedbackFilter
# ---------------------------------------------------------------------------

class TestFeedbackFilter:
    """Tests for the FeedbackFilter data model."""

    def test_filter_defaults(self):
        """Default filter has all None values."""
        f = FeedbackFilter()
        assert f.thread_id is None
        assert f.message_id is None
        assert f.user_id is None
        assert f.agent_name is None
        assert f.sentiment is None
        assert f.min_rating is None
        assert f.max_rating is None
        assert f.since is None
        assert f.until is None
        assert f.tags is None
        assert f.limit == 100
        assert f.offset == 0

    def test_filter_custom(self):
        """Filter can be created with all fields."""
        f = FeedbackFilter(
            thread_id="t1",
            user_id="u1",
            min_rating=3,
            max_rating=5,
            limit=50,
            offset=10,
        )
        assert f.thread_id == "t1"
        assert f.user_id == "u1"
        assert f.min_rating == 3
        assert f.max_rating == 5
        assert f.limit == 50
        assert f.offset == 10


# ---------------------------------------------------------------------------
# FeedbackStats
# ---------------------------------------------------------------------------

class TestFeedbackStats:
    """Tests for the FeedbackStats data model."""

    def test_stats_defaults(self):
        """Default stats are all zero."""
        s = FeedbackStats()
        assert s.total == 0
        assert s.average_rating == 0.0
        assert s.rating_distribution == {}
        assert s.sentiment_distribution == {}
        assert s.by_agent == {}
        assert s.by_thread == {}
        assert s.with_comments == 0
        assert s.with_tags == 0

    def test_stats_to_dict(self):
        """to_dict returns all fields."""
        s = FeedbackStats(
            total=10,
            average_rating=4.2,
            rating_distribution={"5": 6, "4": 4},
            sentiment_distribution={"positive": 10},
            by_agent={"default": {"total": 10, "average_rating": 4.2}},
            by_thread={"t1": 10},
            with_comments=5,
            with_tags=3,
        )
        d = s.to_dict()
        assert d["total"] == 10
        assert d["average_rating"] == 4.2
        assert d["rating_distribution"] == {"5": 6, "4": 4}
        assert d["sentiment_distribution"] == {"positive": 10}
        assert d["by_agent"]["default"]["total"] == 10
        assert d["by_thread"] == {"t1": 10}
        assert d["with_comments"] == 5
        assert d["with_tags"] == 3


# ---------------------------------------------------------------------------
# NullFeedbackProvider
# ---------------------------------------------------------------------------

class TestNullFeedbackProvider:
    """Tests for the NullFeedbackProvider."""

    def test_null_create_returns_record(self):
        """Create returns the record unchanged."""
        provider = NullFeedbackProvider()
        record = FeedbackRecord(thread_id="t1", rating=5)
        result = provider.create(record)
        assert result is record

    def test_null_get_returns_none(self):
        """Get always returns None."""
        provider = NullFeedbackProvider()
        assert provider.get("any-id") is None

    def test_null_query_returns_empty(self):
        """Query always returns empty list."""
        provider = NullFeedbackProvider()
        assert provider.query() == []
        assert provider.query(FeedbackFilter(thread_id="t1")) == []

    def test_null_delete_returns_false(self):
        """Delete always returns False."""
        provider = NullFeedbackProvider()
        assert provider.delete("any-id") is False

    def test_null_stats_returns_empty(self):
        """Stats always returns zero-values."""
        provider = NullFeedbackProvider()
        stats = provider.stats()
        assert stats.total == 0
        assert stats.average_rating == 0.0

    def test_null_close_noop(self):
        """Close does not raise."""
        provider = NullFeedbackProvider()
        provider.close()


# ---------------------------------------------------------------------------
# InMemoryFeedbackProvider
# ---------------------------------------------------------------------------

class TestInMemoryFeedbackProvider:
    """Tests for the InMemoryFeedbackProvider."""

    def test_create_and_get(self):
        """Created record can be retrieved by ID."""
        provider = InMemoryFeedbackProvider()
        record = FeedbackRecord(thread_id="t1", rating=5, comment="Good")
        stored = provider.create(record)
        assert stored.id == record.id
        fetched = provider.get(record.id)
        assert fetched is not None
        assert fetched.thread_id == "t1"
        assert fetched.rating == 5

    def test_get_nonexistent(self):
        """Get returns None for unknown ID."""
        provider = InMemoryFeedbackProvider()
        assert provider.get("nonexistent") is None

    def test_delete(self):
        """Deleted record is removed."""
        provider = InMemoryFeedbackProvider()
        record = FeedbackRecord(thread_id="t1")
        provider.create(record)
        assert provider.delete(record.id) is True
        assert provider.get(record.id) is None

    def test_delete_nonexistent(self):
        """Delete returns False for unknown ID."""
        provider = InMemoryFeedbackProvider()
        assert provider.delete("nonexistent") is False

    def test_query_all(self):
        """Query without filter returns all records."""
        provider = InMemoryFeedbackProvider()
        for i in range(5):
            provider.create(FeedbackRecord(thread_id=f"t{i}", rating=i + 1))
        results = provider.query()
        assert len(results) == 5

    def test_query_filter_thread_id(self):
        """Filter by thread_id works."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", rating=5))
        provider.create(FeedbackRecord(thread_id="t2", rating=3))
        provider.create(FeedbackRecord(thread_id="t1", rating=4))
        results = provider.query(FeedbackFilter(thread_id="t1"))
        assert len(results) == 2

    def test_query_filter_rating_range(self):
        """Filter by min/max rating works."""
        provider = InMemoryFeedbackProvider()
        for r in [1, 2, 3, 4, 5]:
            provider.create(FeedbackRecord(thread_id="t1", rating=r))
        results = provider.query(FeedbackFilter(min_rating=3, max_rating=5))
        assert len(results) == 3

    def test_query_filter_agent_name(self):
        """Filter by agent_name works."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", agent_name="agent1", rating=5))
        provider.create(FeedbackRecord(thread_id="t1", agent_name="agent2", rating=3))
        results = provider.query(FeedbackFilter(agent_name="agent1"))
        assert len(results) == 1

    def test_query_filter_user_id(self):
        """Filter by user_id works."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", user_id="u1", rating=5))
        provider.create(FeedbackRecord(thread_id="t1", user_id="u2", rating=3))
        results = provider.query(FeedbackFilter(user_id="u1"))
        assert len(results) == 1

    def test_query_filter_sentiment(self):
        """Filter by sentiment works."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", rating=5))
        provider.create(FeedbackRecord(thread_id="t1", rating=2))
        provider.create(FeedbackRecord(thread_id="t1", rating=3))
        results = provider.query(FeedbackFilter(sentiment="positive"))
        assert len(results) == 1
        results = provider.query(FeedbackFilter(sentiment="negative"))
        assert len(results) == 1
        results = provider.query(FeedbackFilter(sentiment="neutral"))
        assert len(results) == 1

    def test_query_filter_tags(self):
        """Filter by tags works (any tag matches)."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", tags=["helpful", "fast"]))
        provider.create(FeedbackRecord(thread_id="t1", tags=["slow"]))
        provider.create(FeedbackRecord(thread_id="t1", tags=[]))
        results = provider.query(FeedbackFilter(tags=["helpful"]))
        assert len(results) == 1
        results = provider.query(FeedbackFilter(tags=["slow", "fast"]))
        assert len(results) == 2

    def test_query_filter_time_range(self):
        """Filter by since/until works."""
        provider = InMemoryFeedbackProvider()
        old = FeedbackRecord(thread_id="t1", rating=5)
        old.created_at = "2024-01-01T00:00:00+00:00"
        provider.create(old)
        new = FeedbackRecord(thread_id="t1", rating=5)
        new.created_at = "2024-06-01T00:00:00+00:00"
        provider.create(new)
        results = provider.query(FeedbackFilter(since="2024-03-01T00:00:00+00:00"))
        assert len(results) == 1
        results = provider.query(FeedbackFilter(until="2024-03-01T00:00:00+00:00"))
        assert len(results) == 1

    def test_query_pagination(self):
        """Pagination (limit + offset) works."""
        provider = InMemoryFeedbackProvider()
        for i in range(10):
            provider.create(FeedbackRecord(thread_id="t1", rating=i + 1))
        results = provider.query(FeedbackFilter(limit=5, offset=0))
        assert len(results) == 5
        results = provider.query(FeedbackFilter(limit=5, offset=5))
        assert len(results) == 5

    def test_query_sorted_desc(self):
        """Records are sorted by created_at descending (newest first)."""
        provider = InMemoryFeedbackProvider()
        for i in range(3):
            r = FeedbackRecord(thread_id="t1", rating=i + 1)
            r.created_at = f"2024-01-0{i + 1}T00:00:00+00:00"
            provider.create(r)
        results = provider.query()
        assert results[0].rating == 3  # newest first
        assert results[-1].rating == 1

    def test_stats(self):
        """Stats computation is correct."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1", agent_name="a1", rating=5, comment="Good"))
        provider.create(FeedbackRecord(thread_id="t1", agent_name="a1", rating=3))
        provider.create(FeedbackRecord(thread_id="t2", agent_name="a2", rating=1, tags=["bad"]))
        stats = provider.stats()
        assert stats.total == 3
        assert stats.average_rating == 3.0  # (5+3+1)/3
        assert stats.with_comments == 1
        assert stats.with_tags == 1
        assert "a1" in stats.by_agent
        assert stats.by_agent["a1"]["total"] == 2
        assert "t1" in stats.by_thread
        assert stats.by_thread["t1"] == 2

    def test_stats_empty(self):
        """Stats on empty provider returns zeros."""
        provider = InMemoryFeedbackProvider()
        stats = provider.stats()
        assert stats.total == 0
        assert stats.average_rating == 0.0

    def test_fifo_eviction(self):
        """FIFO eviction when max_records is reached."""
        provider = InMemoryFeedbackProvider(max_records=3)
        r1 = FeedbackRecord(thread_id="t1")
        r1.created_at = "2024-01-01T00:00:00+00:00"
        provider.create(r1)
        r2 = FeedbackRecord(thread_id="t2")
        r2.created_at = "2024-01-02T00:00:00+00:00"
        provider.create(r2)
        r3 = FeedbackRecord(thread_id="t3")
        r3.created_at = "2024-01-03T00:00:00+00:00"
        provider.create(r3)
        r4 = FeedbackRecord(thread_id="t4")
        r4.created_at = "2024-01-04T00:00:00+00:00"
        provider.create(r4)  # should evict r1
        assert provider.get(r1.id) is None
        assert provider.get(r2.id) is not None
        assert provider.get(r4.id) is not None

    def test_close_clears(self):
        """Close clears all records."""
        provider = InMemoryFeedbackProvider()
        provider.create(FeedbackRecord(thread_id="t1"))
        provider.close()
        assert provider.query() == []

    def test_protocol_compliance(self):
        """InMemoryFeedbackProvider satisfies FeedbackProvider Protocol."""
        from agentbase.core.feedback import FeedbackProvider
        assert isinstance(InMemoryFeedbackProvider(), FeedbackProvider)

    def test_null_protocol_compliance(self):
        """NullFeedbackProvider satisfies FeedbackProvider Protocol."""
        from agentbase.core.feedback import FeedbackProvider
        assert isinstance(NullFeedbackProvider(), FeedbackProvider)


# ---------------------------------------------------------------------------
# _apply_feedback_filter
# ---------------------------------------------------------------------------

class TestApplyFeedbackFilter:
    """Tests for the filter helper function."""

    def test_no_filter_returns_all(self):
        """No filter (None) returns all records."""
        records = [FeedbackRecord(thread_id=f"t{i}") for i in range(3)]
        results = _apply_feedback_filter(records, FeedbackFilter(limit=0))
        assert len(results) == 3

    def test_thread_id_filter(self):
        """Thread ID filter works."""
        records = [
            FeedbackRecord(thread_id="t1"),
            FeedbackRecord(thread_id="t2"),
            FeedbackRecord(thread_id="t1"),
        ]
        results = _apply_feedback_filter(records, FeedbackFilter(thread_id="t1", limit=0))
        assert len(results) == 2

    def test_min_rating_excludes_none(self):
        """Records with no rating are excluded by min_rating filter."""
        records = [
            FeedbackRecord(thread_id="t1", rating=5),
            FeedbackRecord(thread_id="t1", rating=None),
        ]
        results = _apply_feedback_filter(records, FeedbackFilter(min_rating=1, limit=0))
        assert len(results) == 1


# ---------------------------------------------------------------------------
# _compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    """Tests for the stats helper function."""

    def test_compute_stats(self):
        """Stats computation from a list of records."""
        records = [
            FeedbackRecord(thread_id="t1", agent_name="a1", rating=5, comment="good"),
            FeedbackRecord(thread_id="t1", agent_name="a1", rating=3),
            FeedbackRecord(thread_id="t2", agent_name="a2", rating=1, tags=["bad"]),
        ]
        stats = _compute_stats(records)
        assert stats.total == 3
        assert stats.average_rating == 3.0
        assert stats.with_comments == 1
        assert stats.with_tags == 1

    def test_compute_stats_empty(self):
        """Empty list returns zero stats."""
        stats = _compute_stats([])
        assert stats.total == 0


# ---------------------------------------------------------------------------
# FeedbackRegistry
# ---------------------------------------------------------------------------

class TestFeedbackRegistry:
    """Tests for the FeedbackRegistry."""

    def test_register_and_create(self):
        """Register a provider and create an instance."""
        registry = FeedbackRegistry()
        registry.register("custom", InMemoryFeedbackProvider)
        provider = registry.create("custom")
        assert isinstance(provider, InMemoryFeedbackProvider)

    def test_register_duplicate_fails(self):
        """Registering a duplicate name raises RegistryError."""
        from agentbase.runtime.errors import RegistryError

        registry = FeedbackRegistry()
        registry.register("test", NullFeedbackProvider)
        with pytest.raises(RegistryError):
            registry.register("test", NullFeedbackProvider)

    def test_register_duplicate_with_override(self):
        """Override flag allows re-registration."""
        registry = FeedbackRegistry()
        registry.register("test", NullFeedbackProvider)
        registry.register("test", InMemoryFeedbackProvider, override=True)
        provider = registry.create("test")
        assert isinstance(provider, InMemoryFeedbackProvider)

    def test_register_empty_name_fails(self):
        """Empty name raises RegistryError."""
        from agentbase.runtime.errors import RegistryError

        registry = FeedbackRegistry()
        with pytest.raises(RegistryError):
            registry.register("", NullFeedbackProvider)

    def test_create_unknown_fails(self):
        """Unknown provider raises RegistryError."""
        from agentbase.runtime.errors import RegistryError

        registry = FeedbackRegistry()
        with pytest.raises(RegistryError):
            registry.create("nonexistent")

    def test_has(self):
        """has() returns True for registered providers."""
        registry = FeedbackRegistry()
        registry.register("test", NullFeedbackProvider)
        assert registry.has("test") is True
        assert registry.has("nonexistent") is False

    def test_names(self):
        """names() returns sorted list of provider names."""
        registry = FeedbackRegistry()
        registry.register("b", NullFeedbackProvider)
        registry.register("a", NullFeedbackProvider)
        assert registry.names() == ["a", "b"]

    def test_count(self):
        """count property returns number of providers."""
        registry = FeedbackRegistry()
        assert registry.count == 0
        registry.register("a", NullFeedbackProvider)
        assert registry.count == 1

    def test_unregister(self):
        """unregister removes a provider."""
        registry = FeedbackRegistry()
        registry.register("test", NullFeedbackProvider)
        assert registry.unregister("test") is True
        assert registry.has("test") is False
        assert registry.unregister("test") is False

    def test_global_registry_has_defaults(self):
        """The global registry has null and memory providers."""
        assert feedback_registry.has("null")
        assert feedback_registry.has("memory")

    def test_register_decorator(self):
        """The @register_feedback_provider decorator works."""
        @register_feedback_provider("test_custom")
        class CustomProvider:
            def create(self, record):
                return record
            def get(self, record_id):
                return None
            def query(self, filter=None):
                return []
            def delete(self, record_id):
                return False
            def stats(self):
                return FeedbackStats()
            def close(self):
                pass

        assert feedback_registry.has("test_custom")
        provider = feedback_registry.create("test_custom")
        assert isinstance(provider, CustomProvider)


# ---------------------------------------------------------------------------
# FeedbackManager
# ---------------------------------------------------------------------------

class TestFeedbackManager:
    """Tests for the FeedbackManager facade."""

    def test_disabled_manager(self):
        """Disabled manager uses NullFeedbackProvider."""
        mgr = FeedbackManager(provider="memory", enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullFeedbackProvider)
        # Operations are no-ops
        assert mgr.list_feedback() == []
        stats = mgr.get_stats()
        assert stats.total == 0

    def test_enabled_manager(self):
        """Enabled manager uses the specified provider."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, InMemoryFeedbackProvider)

    def test_create_feedback(self):
        """create_feedback stores a record."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        record = mgr.create_feedback(
            thread_id="t1",
            rating=5,
            comment="Great!",
            agent_name="agent1",
        )
        assert record.id
        assert record.thread_id == "t1"
        assert record.rating == 5
        # Verify it's stored
        fetched = mgr.get_feedback(record.id)
        assert fetched is not None
        assert fetched.rating == 5

    def test_create_feedback_no_thread_id_raises(self):
        """create_feedback raises RegistryError when thread_id is empty."""
        from agentbase.runtime.errors import RegistryError

        mgr = FeedbackManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError):
            mgr.create_feedback(thread_id="", rating=5)

    def test_get_feedback_nonexistent(self):
        """get_feedback returns None for unknown ID."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        assert mgr.get_feedback("nonexistent") is None

    def test_update_feedback(self):
        """update_feedback updates fields on an existing record."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        record = mgr.create_feedback(thread_id="t1", rating=3)
        updated = mgr.update_feedback(record.id, rating=5, comment="Updated")
        assert updated is not None
        assert updated.rating == 5
        assert updated.comment == "Updated"

    def test_update_feedback_nonexistent(self):
        """update_feedback returns None for unknown ID."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        assert mgr.update_feedback("nonexistent", rating=5) is None

    def test_list_feedback(self):
        """list_feedback returns filtered records."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        mgr.create_feedback(thread_id="t1", rating=5)
        mgr.create_feedback(thread_id="t2", rating=3)
        mgr.create_feedback(thread_id="t1", rating=4)
        results = mgr.list_feedback(thread_id="t1")
        assert len(results) == 2

    def test_delete_feedback(self):
        """delete_feedback removes a record."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        record = mgr.create_feedback(thread_id="t1")
        assert mgr.delete_feedback(record.id) is True
        assert mgr.get_feedback(record.id) is None

    def test_delete_feedback_nonexistent(self):
        """delete_feedback returns False for unknown ID."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        assert mgr.delete_feedback("nonexistent") is False

    def test_get_stats(self):
        """get_stats returns aggregate statistics."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        mgr.create_feedback(thread_id="t1", agent_name="a1", rating=5)
        mgr.create_feedback(thread_id="t1", agent_name="a1", rating=3)
        mgr.create_feedback(thread_id="t2", agent_name="a2", rating=1)
        stats = mgr.get_stats()
        assert stats.total == 3
        assert stats.average_rating == 3.0

    def test_get_stats_filtered(self):
        """get_stats with filters returns filtered statistics."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        mgr.create_feedback(thread_id="t1", agent_name="a1", rating=5)
        mgr.create_feedback(thread_id="t1", agent_name="a2", rating=1)
        stats = mgr.get_stats(agent_name="a1")
        assert stats.total == 1
        assert stats.average_rating == 5.0

    def test_clear_all(self):
        """clear_all removes all records."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        mgr.create_feedback(thread_id="t1")
        mgr.create_feedback(thread_id="t2")
        count = mgr.clear_all()
        assert count == 2
        assert len(mgr.list_feedback()) == 0

    def test_close(self):
        """close does not raise."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        mgr.close()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

class TestSingleton:
    """Tests for singleton management functions."""

    def test_set_and_get(self):
        """set_feedback_manager + get_feedback_manager work."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        set_feedback_manager(mgr)
        assert get_feedback_manager() is mgr

    def test_get_without_set_raises(self):
        """get_feedback_manager raises RuntimeError if not set."""
        reset_feedback_manager()
        with pytest.raises(RuntimeError):
            get_feedback_manager()

    def test_reset(self):
        """reset_feedback_manager clears the singleton."""
        mgr = FeedbackManager(provider="memory", enabled=True)
        set_feedback_manager(mgr)
        reset_feedback_manager()
        with pytest.raises(RuntimeError):
            get_feedback_manager()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Thread-safety tests for InMemoryFeedbackProvider."""

    def test_concurrent_writes(self):
        """Concurrent writes don't corrupt data."""
        provider = InMemoryFeedbackProvider(max_records=10000)
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=provider.create,
                args=(FeedbackRecord(thread_id=f"t{i}", rating=i + 1),),
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        records = provider.query()
        assert len(records) == 10

    def test_concurrent_reads_and_writes(self):
        """Concurrent reads and writes are safe."""
        provider = InMemoryFeedbackProvider()
        # Pre-populate
        for i in range(50):
            provider.create(FeedbackRecord(thread_id=f"t{i}", rating=i % 5 + 1))

        errors = []

        def writer():
            for i in range(20):
                try:
                    provider.create(FeedbackRecord(thread_id=f"w{i}", rating=5))
                except Exception as e:
                    errors.append(e)

        def reader():
            for _ in range(20):
                try:
                    provider.query()
                    provider.stats()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
