"""Unit tests for the notification center core module."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from agentbase.core.notification import (
    InMemoryNotificationProvider,
    Notification,
    NotificationFilter,
    NotificationManager,
    NotificationRegistry,
    NotificationStats,
    NullNotificationProvider,
    _apply_notification_filter,
    _compute_stats,
    get_notification_manager,
    notification_registry,
    register_notification_provider,
    reset_notification_manager,
    set_notification_manager,
)


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

class TestNotification:
    """Tests for the Notification data model."""

    def test_creation_minimal(self):
        n = Notification(user_id="u1", title="Hello")
        assert n.user_id == "u1"
        assert n.title == "Hello"
        assert n.message == ""
        assert n.category == "system"
        assert n.severity == "info"
        assert n.read is False
        assert n.action_url == ""
        assert n.action_label == ""
        assert n.metadata == {}
        assert n.created_at
        assert n.read_at == ""
        assert n.expires_at == ""
        assert n.id  # auto-assigned

    def test_creation_full(self):
        n = Notification(
            user_id="u1",
            title="Alert",
            message="Something happened",
            category="quota_alert",
            severity="warning",
            action_url="/usage",
            action_label="View Usage",
            metadata={"key": "val"},
            expires_at="2099-01-01T00:00:00+00:00",
        )
        assert n.user_id == "u1"
        assert n.title == "Alert"
        assert n.message == "Something happened"
        assert n.category == "quota_alert"
        assert n.severity == "warning"
        assert n.action_url == "/usage"
        assert n.action_label == "View Usage"
        assert n.metadata == {"key": "val"}
        assert n.expires_at == "2099-01-01T00:00:00+00:00"

    def test_auto_id(self):
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B")
        assert n1.id != n2.id
        assert len(n1.id) == 16

    def test_custom_id(self):
        n = Notification(user_id="u1", title="A", id="custom-id")
        assert n.id == "custom-id"

    def test_is_expired_no_expiry(self):
        n = Notification(user_id="u1", title="A")
        assert n.is_expired is False

    def test_is_expired_future(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        n = Notification(user_id="u1", title="A", expires_at=future)
        assert n.is_expired is False

    def test_is_expired_past(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        n = Notification(user_id="u1", title="A", expires_at=past)
        assert n.is_expired is True

    def test_to_dict(self):
        n = Notification(
            user_id="u1",
            title="Test",
            message="Body",
            category="security",
            severity="error",
            metadata={"k": "v"},
        )
        d = n.to_dict()
        assert d["user_id"] == "u1"
        assert d["title"] == "Test"
        assert d["message"] == "Body"
        assert d["category"] == "security"
        assert d["severity"] == "error"
        assert d["read"] is False
        assert d["metadata"] == {"k": "v"}
        assert "id" in d
        assert "created_at" in d
        assert "is_expired" in d


# ---------------------------------------------------------------------------
# NullNotificationProvider
# ---------------------------------------------------------------------------

class TestNullNotificationProvider:
    """Tests for the NullNotificationProvider."""

    def test_create(self):
        provider = NullNotificationProvider()
        n = Notification(user_id="u1", title="A")
        result = provider.create(n)
        assert result is n

    def test_get(self):
        provider = NullNotificationProvider()
        assert provider.get("any-id") is None

    def test_query(self):
        provider = NullNotificationProvider()
        assert provider.query() == []
        assert provider.query(NotificationFilter(user_id="u1")) == []

    def test_update(self):
        provider = NullNotificationProvider()
        assert provider.update("id", {"title": "X"}) is None

    def test_mark_read(self):
        provider = NullNotificationProvider()
        assert provider.mark_read("id") is None
        assert provider.mark_read("id", read=False) is None

    def test_mark_all_read(self):
        provider = NullNotificationProvider()
        assert provider.mark_all_read("u1") == 0

    def test_delete(self):
        provider = NullNotificationProvider()
        assert provider.delete("id") is False

    def test_stats(self):
        provider = NullNotificationProvider()
        s = provider.stats()
        assert s.total == 0
        assert s.unread == 0
        assert s.read == 0

    def test_close(self):
        provider = NullNotificationProvider()
        provider.close()


# ---------------------------------------------------------------------------
# InMemoryNotificationProvider
# ---------------------------------------------------------------------------

class TestInMemoryNotificationProvider:
    """Tests for the InMemoryNotificationProvider."""

    def test_create_and_get(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="Hello")
        provider.create(n)
        assert provider.get(n.id) is n

    def test_get_not_found(self):
        provider = InMemoryNotificationProvider()
        assert provider.get("nonexistent") is None

    def test_query_all(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A", created_at="2024-01-01T00:00:00+00:00")
        n2 = Notification(user_id="u2", title="B", created_at="2024-01-02T00:00:00+00:00")
        provider.create(n1)
        provider.create(n2)
        results = provider.query()
        assert len(results) == 2
        # Newest first
        assert results[0].id == n2.id

    def test_query_by_user(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u2", title="B")
        n3 = Notification(user_id="*", title="C")  # broadcast
        provider.create(n1)
        provider.create(n2)
        provider.create(n3)
        results = provider.query(NotificationFilter(user_id="u1"))
        # Should include user-specific + broadcast
        ids = {r.id for r in results}
        assert n1.id in ids
        assert n2.id not in ids
        assert n3.id in ids

    def test_query_by_user_no_broadcast(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A")
        n3 = Notification(user_id="*", title="C")
        provider.create(n1)
        provider.create(n3)
        results = provider.query(
            NotificationFilter(user_id="u1", include_broadcast=False)
        )
        ids = {r.id for r in results}
        assert n1.id in ids
        assert n3.id not in ids

    def test_query_by_category(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A", category="system")
        n2 = Notification(user_id="u1", title="B", category="quota_alert")
        provider.create(n1)
        provider.create(n2)
        results = provider.query(NotificationFilter(category="system"))
        assert len(results) == 1
        assert results[0].id == n1.id

    def test_query_by_severity(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A", severity="error")
        n2 = Notification(user_id="u1", title="B", severity="info")
        provider.create(n1)
        provider.create(n2)
        results = provider.query(NotificationFilter(severity="error"))
        assert len(results) == 1
        assert results[0].id == n1.id

    def test_query_unread_only(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B")
        provider.create(n1)
        provider.create(n2)
        provider.mark_read(n1.id)
        results = provider.query(NotificationFilter(unread_only=True))
        assert len(results) == 1
        assert results[0].id == n2.id

    def test_query_since_until(self):
        provider = InMemoryNotificationProvider()
        old_time = "2020-01-01T00:00:00+00:00"
        new_time = "2025-01-01T00:00:00+00:00"
        n1 = Notification(user_id="u1", title="A", created_at=old_time)
        n2 = Notification(user_id="u1", title="B", created_at=new_time)
        provider.create(n1)
        provider.create(n2)
        # Since filter
        results = provider.query(NotificationFilter(since="2024-01-01T00:00:00+00:00"))
        assert len(results) == 1
        assert results[0].id == n2.id
        # Until filter
        results = provider.query(NotificationFilter(until="2024-01-01T00:00:00+00:00"))
        assert len(results) == 1
        assert results[0].id == n1.id

    def test_query_limit_offset(self):
        provider = InMemoryNotificationProvider()
        for i in range(10):
            provider.create(Notification(user_id="u1", title=f"N{i}"))
        results = provider.query(NotificationFilter(limit=3, offset=0))
        assert len(results) == 3
        results2 = provider.query(NotificationFilter(limit=3, offset=3))
        assert len(results2) == 3
        assert results[0].id != results2[0].id

    def test_query_expired_filtered(self):
        provider = InMemoryNotificationProvider()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B", expires_at=past)
        provider.create(n1)
        provider.create(n2)
        results = provider.query()
        assert len(results) == 1
        assert results[0].id == n1.id

    def test_mark_read(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        provider.create(n)
        result = provider.mark_read(n.id)
        assert result is not None
        assert result.read is True
        assert result.read_at != ""

    def test_mark_unread(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        provider.create(n)
        provider.mark_read(n.id)
        result = provider.mark_read(n.id, read=False)
        assert result is not None
        assert result.read is False
        assert result.read_at == ""

    def test_mark_read_not_found(self):
        provider = InMemoryNotificationProvider()
        assert provider.mark_read("nonexistent") is None

    def test_mark_all_read(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B")
        n3 = Notification(user_id="*", title="C")
        n4 = Notification(user_id="u2", title="D")
        provider.create(n1)
        provider.create(n2)
        provider.create(n3)
        provider.create(n4)
        count = provider.mark_all_read("u1")
        # n1, n2 (user-specific) + n3 (broadcast) = 3
        assert count == 3
        assert provider.get(n1.id).read is True
        assert provider.get(n2.id).read is True
        assert provider.get(n3.id).read is True
        assert provider.get(n4.id).read is False

    def test_mark_all_read_already_read(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B")
        provider.create(n1)
        provider.create(n2)
        provider.mark_read(n1.id)
        count = provider.mark_all_read("u1")
        assert count == 1  # only n2 was unread

    def test_mark_all_read_no_user(self):
        provider = InMemoryNotificationProvider()
        assert provider.mark_all_read("nonexistent") == 0

    def test_update(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        provider.create(n)
        result = provider.update(n.id, {"title": "Updated", "severity": "error"})
        assert result is not None
        assert result.title == "Updated"
        assert result.severity == "error"

    def test_update_not_found(self):
        provider = InMemoryNotificationProvider()
        assert provider.update("nonexistent", {"title": "X"}) is None

    def test_update_protected_fields(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        original_id = n.id
        original_created = n.created_at
        provider.create(n)
        provider.update(n.id, {"id": "hacked", "created_at": "hacked"})
        result = provider.get(n.id)
        assert result.id == original_id
        assert result.created_at == original_created

    def test_delete(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        provider.create(n)
        assert provider.delete(n.id) is True
        assert provider.get(n.id) is None

    def test_delete_not_found(self):
        provider = InMemoryNotificationProvider()
        assert provider.delete("nonexistent") is False

    def test_stats(self):
        provider = InMemoryNotificationProvider()
        n1 = Notification(user_id="u1", title="A", category="system", severity="info")
        n2 = Notification(user_id="u1", title="B", category="quota_alert", severity="warning")
        n3 = Notification(user_id="*", title="C", category="system", severity="error")
        provider.create(n1)
        provider.create(n2)
        provider.create(n3)
        provider.mark_read(n1.id)
        s = provider.stats()
        assert s.total == 3
        assert s.unread == 2
        assert s.read == 1
        assert s.by_category["system"] == 2
        assert s.by_category["quota_alert"] == 1
        assert s.by_severity["info"] == 1
        assert s.by_severity["warning"] == 1
        assert s.by_severity["error"] == 1
        assert s.by_user["u1"] == 2
        assert s.by_user["*"] == 1
        assert s.broadcasts == 1

    def test_stats_empty(self):
        provider = InMemoryNotificationProvider()
        s = provider.stats()
        assert s.total == 0

    def test_stats_excludes_expired(self):
        provider = InMemoryNotificationProvider()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B", expires_at=past)
        provider.create(n1)
        provider.create(n2)
        s = provider.stats()
        assert s.total == 1

    def test_fifo_eviction(self):
        provider = InMemoryNotificationProvider(max_records=3)
        n1 = Notification(user_id="u1", title="A", created_at="2020-01-01T00:00:00+00:00")
        n2 = Notification(user_id="u1", title="B", created_at="2021-01-01T00:00:00+00:00")
        n3 = Notification(user_id="u1", title="C", created_at="2022-01-01T00:00:00+00:00")
        n4 = Notification(user_id="u1", title="D", created_at="2023-01-01T00:00:00+00:00")
        provider.create(n1)
        provider.create(n2)
        provider.create(n3)
        provider.create(n4)
        # n1 should have been evicted (oldest)
        assert provider.get(n1.id) is None
        assert provider.get(n4.id) is not None

    def test_close(self):
        provider = InMemoryNotificationProvider()
        n = Notification(user_id="u1", title="A")
        provider.create(n)
        provider.close()
        assert provider.query() == []


# ---------------------------------------------------------------------------
# NotificationFilter
# ---------------------------------------------------------------------------

class TestNotificationFilter:
    """Tests for NotificationFilter."""

    def test_defaults(self):
        flt = NotificationFilter()
        assert flt.user_id is None
        assert flt.category is None
        assert flt.severity is None
        assert flt.unread_only is False
        assert flt.since is None
        assert flt.until is None
        assert flt.include_broadcast is True
        assert flt.limit == 100
        assert flt.offset == 0


# ---------------------------------------------------------------------------
# _apply_notification_filter
# ---------------------------------------------------------------------------

class TestApplyNotificationFilter:
    """Tests for the _apply_notification_filter helper."""

    def test_filter_by_user(self):
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u2", title="B")
        result = _apply_notification_filter([n1, n2], NotificationFilter(user_id="u1"))
        assert len(result) == 1
        assert result[0].id == n1.id

    def test_filter_include_broadcast(self):
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="*", title="B")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(user_id="u1", include_broadcast=True)
        )
        assert len(result) == 2

    def test_filter_exclude_broadcast(self):
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="*", title="B")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(user_id="u1", include_broadcast=False)
        )
        assert len(result) == 1
        assert result[0].id == n1.id

    def test_filter_unread_only(self):
        n1 = Notification(user_id="u1", title="A")
        n2 = Notification(user_id="u1", title="B")
        n1.read = True
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(unread_only=True)
        )
        assert len(result) == 1
        assert result[0].id == n2.id

    def test_filter_category(self):
        n1 = Notification(user_id="u1", title="A", category="system")
        n2 = Notification(user_id="u1", title="B", category="security")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(category="security")
        )
        assert len(result) == 1
        assert result[0].id == n2.id

    def test_filter_severity(self):
        n1 = Notification(user_id="u1", title="A", severity="info")
        n2 = Notification(user_id="u1", title="B", severity="critical")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(severity="critical")
        )
        assert len(result) == 1
        assert result[0].id == n2.id

    def test_filter_since(self):
        n1 = Notification(user_id="u1", title="A", created_at="2020-01-01T00:00:00+00:00")
        n2 = Notification(user_id="u1", title="B", created_at="2025-01-01T00:00:00+00:00")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(since="2024-01-01T00:00:00+00:00")
        )
        assert len(result) == 1
        assert result[0].id == n2.id

    def test_filter_until(self):
        n1 = Notification(user_id="u1", title="A", created_at="2020-01-01T00:00:00+00:00")
        n2 = Notification(user_id="u1", title="B", created_at="2025-01-01T00:00:00+00:00")
        result = _apply_notification_filter(
            [n1, n2], NotificationFilter(until="2024-01-01T00:00:00+00:00")
        )
        assert len(result) == 1
        assert result[0].id == n1.id

    def test_filter_limit(self):
        items = [Notification(user_id="u1", title=f"N{i}") for i in range(10)]
        result = _apply_notification_filter(items, NotificationFilter(limit=3))
        assert len(result) == 3

    def test_filter_offset(self):
        items = [
            Notification(user_id="u1", title=f"N{i}", created_at=f"2024-01-{i+1:02d}T00:00:00+00:00")
            for i in range(10)
        ]
        result = _apply_notification_filter(items, NotificationFilter(limit=3, offset=5))
        assert len(result) == 3
        # With offset=5 and newest-first ordering, result[0] should be items[4]
        assert result[0].id == items[4].id

    def test_filter_combined(self):
        n1 = Notification(user_id="u1", title="A", category="system", severity="info")
        n2 = Notification(user_id="u1", title="B", category="system", severity="warning")
        n3 = Notification(user_id="u2", title="C", category="system", severity="warning")
        result = _apply_notification_filter(
            [n1, n2, n3],
            NotificationFilter(user_id="u1", category="system", severity="warning"),
        )
        assert len(result) == 1
        assert result[0].id == n2.id

    def test_filter_sorting_descending(self):
        n1 = Notification(user_id="u1", title="A", created_at="2020-01-01T00:00:00+00:00")
        n2 = Notification(user_id="u1", title="B", created_at="2025-01-01T00:00:00+00:00")
        result = _apply_notification_filter([n1, n2], NotificationFilter())
        assert result[0].id == n2.id  # newest first


# ---------------------------------------------------------------------------
# _compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    """Tests for the _compute_stats helper."""

    def test_empty(self):
        s = _compute_stats([])
        assert s.total == 0
        assert s.unread == 0
        assert s.read == 0

    def test_mixed(self):
        n1 = Notification(user_id="u1", title="A", category="system", severity="info")
        n2 = Notification(user_id="u1", title="B", category="quota_alert", severity="warning")
        n3 = Notification(user_id="*", title="C", category="system", severity="error")
        n1.read = True
        s = _compute_stats([n1, n2, n3])
        assert s.total == 3
        assert s.read == 1
        assert s.unread == 2
        assert s.by_category["system"] == 2
        assert s.by_category["quota_alert"] == 1
        assert s.by_severity["info"] == 1
        assert s.by_severity["warning"] == 1
        assert s.by_severity["error"] == 1
        assert s.by_user["u1"] == 2
        assert s.by_user["*"] == 1
        assert s.broadcasts == 1


# ---------------------------------------------------------------------------
# NotificationRegistry
# ---------------------------------------------------------------------------

class TestNotificationRegistry:
    """Tests for the NotificationRegistry."""

    def test_register_and_create(self):
        registry = NotificationRegistry()
        registry.register("test_provider", InMemoryNotificationProvider)
        provider = registry.create("test_provider")
        assert isinstance(provider, InMemoryNotificationProvider)

    def test_register_duplicate(self):
        registry = NotificationRegistry()
        registry.register("test", InMemoryNotificationProvider)
        with pytest.raises(Exception):
            registry.register("test", InMemoryNotificationProvider)

    def test_register_override(self):
        registry = NotificationRegistry()
        registry.register("test", InMemoryNotificationProvider)
        registry.register("test", InMemoryNotificationProvider, override=True)

    def test_register_empty_name(self):
        registry = NotificationRegistry()
        with pytest.raises(Exception):
            registry.register("", InMemoryNotificationProvider)

    def test_create_not_found(self):
        registry = NotificationRegistry()
        with pytest.raises(Exception):
            registry.create("nonexistent")

    def test_has(self):
        registry = NotificationRegistry()
        registry.register("test", InMemoryNotificationProvider)
        assert registry.has("test") is True
        assert registry.has("nonexistent") is False

    def test_names(self):
        registry = NotificationRegistry()
        registry.register("alpha", InMemoryNotificationProvider)
        registry.register("beta", InMemoryNotificationProvider)
        names = registry.names()
        assert "alpha" in names
        assert "beta" in names

    def test_count(self):
        registry = NotificationRegistry()
        assert registry.count == 0
        registry.register("a", InMemoryNotificationProvider)
        assert registry.count == 1

    def test_unregister(self):
        registry = NotificationRegistry()
        registry.register("test", InMemoryNotificationProvider)
        assert registry.unregister("test") is True
        assert registry.has("test") is False
        assert registry.unregister("test") is False

    def test_default_registry_has_providers(self):
        assert notification_registry.has("memory")
        assert notification_registry.has("null")

    def test_default_registry_create_memory(self):
        provider = notification_registry.create("memory")
        assert isinstance(provider, InMemoryNotificationProvider)

    def test_default_registry_create_null(self):
        provider = notification_registry.create("null")
        assert isinstance(provider, NullNotificationProvider)

    def test_decorator(self):
        @register_notification_provider("custom_test_provider", override=True)
        class CustomProvider:
            def create(self, n):
                return n
            def get(self, nid):
                return None
            def query(self, flt=None):
                return []
            def update(self, nid, changes):
                return None
            def mark_read(self, nid, read=True):
                return None
            def mark_all_read(self, uid):
                return 0
            def delete(self, nid):
                return False
            def stats(self):
                return NotificationStats()
            def close(self):
                pass

        assert notification_registry.has("custom_test_provider")
        provider = notification_registry.create("custom_test_provider")
        assert isinstance(provider, CustomProvider)


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------

class TestNotificationManager:
    """Tests for the NotificationManager facade."""

    def test_disabled_manager(self):
        mgr = NotificationManager(enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullNotificationProvider)

    def test_enabled_manager(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, InMemoryNotificationProvider)

    def test_create_notification(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(
            user_id="u1",
            title="Hello",
            message="World",
            category="system",
            severity="info",
        )
        assert n.id
        assert n.user_id == "u1"
        assert n.title == "Hello"
        assert n.message == "World"

    def test_create_notification_missing_user_id(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        with pytest.raises(Exception):
            mgr.create_notification(user_id="", title="A")

    def test_create_notification_missing_title(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        with pytest.raises(Exception):
            mgr.create_notification(user_id="u1", title="")

    def test_broadcast(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.broadcast(title="System Message", message="Hello all")
        assert n.user_id == "*"
        assert n.title == "System Message"

    def test_broadcast_missing_title(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        with pytest.raises(Exception):
            mgr.broadcast(title="")

    def test_get_notification(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        result = mgr.get_notification(n.id)
        assert result is not None
        assert result.id == n.id

    def test_get_notification_not_found(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        assert mgr.get_notification("nonexistent") is None

    def test_list_notifications(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n1 = mgr.create_notification(user_id="u1", title="A")
        n2 = mgr.create_notification(user_id="u2", title="B")
        results = mgr.list_notifications(user_id="u1")
        ids = {r.id for r in results}
        assert n1.id in ids
        assert n2.id not in ids

    def test_list_notifications_disabled(self):
        mgr = NotificationManager(enabled=False)
        assert mgr.list_notifications() == []

    def test_update_notification(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        result = mgr.update_notification(n.id, title="Updated", severity="error")
        assert result is not None
        assert result.title == "Updated"
        assert result.severity == "error"

    def test_update_notification_not_found(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        assert mgr.update_notification("nonexistent", title="X") is None

    def test_update_notification_no_changes(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        result = mgr.update_notification(n.id)
        assert result is not None
        assert result.id == n.id

    def test_mark_read(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        result = mgr.mark_read(n.id)
        assert result is not None
        assert result.read is True

    def test_mark_unread(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        mgr.mark_read(n.id)
        result = mgr.mark_unread(n.id)
        assert result is not None
        assert result.read is False

    def test_mark_read_not_found(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        assert mgr.mark_read("nonexistent") is None

    def test_mark_all_read(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n1 = mgr.create_notification(user_id="u1", title="A")
        n2 = mgr.create_notification(user_id="u1", title="B")
        n3 = mgr.broadcast(title="C")
        count = mgr.mark_all_read("u1")
        assert count == 3
        assert mgr.get_notification(n1.id).read is True
        assert mgr.get_notification(n2.id).read is True
        assert mgr.get_notification(n3.id).read is True

    def test_delete_notification(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n = mgr.create_notification(user_id="u1", title="A")
        assert mgr.delete_notification(n.id) is True
        assert mgr.get_notification(n.id) is None

    def test_delete_notification_not_found(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        assert mgr.delete_notification("nonexistent") is False

    def test_get_stats(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        mgr.create_notification(user_id="u1", title="A", category="system", severity="info")
        mgr.create_notification(user_id="u1", title="B", category="security", severity="warning")
        mgr.broadcast(title="C", category="system", severity="error")
        s = mgr.get_stats()
        assert s.total == 3
        assert s.by_category["system"] == 2
        assert s.by_severity["warning"] == 1

    def test_get_stats_filtered(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        mgr.create_notification(user_id="u1", title="A", category="system")
        mgr.create_notification(user_id="u2", title="B", category="security")
        s = mgr.get_stats(user_id="u1")
        assert s.total == 1

    def test_get_stats_disabled(self):
        mgr = NotificationManager(enabled=False)
        s = mgr.get_stats()
        assert s.total == 0

    def test_get_unread_count(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        n1 = mgr.create_notification(user_id="u1", title="A")
        mgr.create_notification(user_id="u1", title="B")
        mgr.broadcast(title="C")
        mgr.mark_read(n1.id)
        count = mgr.get_unread_count("u1")
        # n2 + n3 (broadcast) = 2
        assert count == 2

    def test_get_unread_count_disabled(self):
        mgr = NotificationManager(enabled=False)
        assert mgr.get_unread_count("u1") == 0

    def test_clear_all(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        mgr.create_notification(user_id="u1", title="A")
        mgr.create_notification(user_id="u2", title="B")
        count = mgr.clear_all()
        assert count == 2
        assert mgr.list_notifications() == []

    def test_close(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        mgr.create_notification(user_id="u1", title="A")
        mgr.close()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

class TestSingleton:
    """Tests for singleton management."""

    def test_set_and_get(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        set_notification_manager(mgr)
        assert get_notification_manager() is mgr
        reset_notification_manager()

    def test_get_not_set(self):
        reset_notification_manager()
        with pytest.raises(RuntimeError):
            get_notification_manager()

    def test_reset(self):
        mgr = NotificationManager(provider="memory", enabled=True)
        set_notification_manager(mgr)
        reset_notification_manager()
        with pytest.raises(RuntimeError):
            get_notification_manager()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_concurrent_create(self):
        provider = InMemoryNotificationProvider()
        results: list[Notification] = []
        errors: list[Exception] = []

        def create_notification(idx: int) -> None:
            try:
                n = Notification(user_id=f"u{idx}", title=f"N{idx}")
                provider.create(n)
                results.append(n)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create_notification, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20
        assert len(provider.query()) == 20

    def test_concurrent_mark_read(self):
        provider = InMemoryNotificationProvider()
        notifications = [
            Notification(user_id="u1", title=f"N{i}") for i in range(20)
        ]
        for n in notifications:
            provider.create(n)

        def mark_read(idx: int) -> None:
            provider.mark_read(notifications[idx].id)

        threads = [threading.Thread(target=mark_read, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        s = provider.stats()
        assert s.read == 20
        assert s.unread == 0
