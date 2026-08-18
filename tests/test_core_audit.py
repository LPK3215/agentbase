"""Tests for audit log service — covers normal / boundary / error paths."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agentbase.core.audit import (
    AuditEvent,
    AuditFilter,
    AuditManager,
    AuditRegistry,
    NullAuditProvider,
    SQLiteAuditProvider,
    audit_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_provider(tmp_path):
    """Create a SQLite audit provider with a temp database."""
    db_path = tmp_path / "audit.db"
    provider = SQLiteAuditProvider(db_path=db_path)
    yield provider
    provider.close()


# ---------------------------------------------------------------------------
# AuditEvent data class
# ---------------------------------------------------------------------------

class TestAuditEvent:
    def test_to_dict_roundtrip(self):
        """AuditEvent.to_dict() should include all fields."""
        event = AuditEvent(
            actor="admin",
            action="config.update",
            resource="agent:default",
            result="success",
            detail={"key": "value"},
        )
        d = event.to_dict()
        assert d["actor"] == "admin"
        assert d["action"] == "config.update"
        assert d["resource"] == "agent:default"
        assert d["result"] == "success"
        assert d["detail"] == {"key": "value"}
        assert d["timestamp"]  # auto-set
        assert d["id"] is None  # not yet persisted

    def test_defaults(self):
        """Default result should be 'success'."""
        event = AuditEvent(actor="user", action="test")
        assert event.result == "success"
        assert event.resource == ""
        assert event.detail == {}
        assert event.timestamp  # auto-set


# ---------------------------------------------------------------------------
# NullAuditProvider
# ---------------------------------------------------------------------------

class TestNullAuditProvider:
    def test_record_is_noop(self):
        """NullAuditProvider should not persist anything."""
        provider = NullAuditProvider()
        event = provider.record(AuditEvent(actor="user", action="test"))
        assert event.id is None  # no ID assigned

    def test_query_returns_empty(self):
        provider = NullAuditProvider()
        assert provider.query() == []
        assert provider.query(AuditFilter(actor="x")) == []

    def test_count_returns_zero(self):
        provider = NullAuditProvider()
        assert provider.count() == 0

    def test_export_returns_zero(self, tmp_path):
        provider = NullAuditProvider()
        path = str(tmp_path / "export.json")
        assert provider.export(path) == 0


# ---------------------------------------------------------------------------
# SQLiteAuditProvider — normal path
# ---------------------------------------------------------------------------

class TestSQLiteAuditProvider:
    def test_record_and_query(self, audit_provider):
        """Record an event and query it back."""
        event = audit_provider.record(AuditEvent(
            actor="admin@example.com",
            action="agent.invoke",
            resource="agent:default",
            result="success",
            detail={"thread_id": "abc123"},
        ))
        assert event.id is not None

        results = audit_provider.query()
        assert len(results) == 1
        assert results[0].actor == "admin@example.com"
        assert results[0].action == "agent.invoke"
        assert results[0].detail == {"thread_id": "abc123"}

    def test_query_by_actor(self, audit_provider):
        """Filter by actor."""
        audit_provider.record(AuditEvent(actor="alice", action="login"))
        audit_provider.record(AuditEvent(actor="bob", action="login"))
        audit_provider.record(AuditEvent(actor="alice", action="logout"))

        results = audit_provider.query(AuditFilter(actor="alice"))
        assert len(results) == 2
        assert all(r.actor == "alice" for r in results)

    def test_query_by_action(self, audit_provider):
        """Filter by action."""
        audit_provider.record(AuditEvent(actor="a", action="login"))
        audit_provider.record(AuditEvent(actor="b", action="login"))
        audit_provider.record(AuditEvent(actor="c", action="delete"))

        results = audit_provider.query(AuditFilter(action="login"))
        assert len(results) == 2

    def test_query_by_result(self, audit_provider):
        """Filter by result."""
        audit_provider.record(AuditEvent(actor="a", action="x", result="success"))
        audit_provider.record(AuditEvent(actor="b", action="y", result="failure"))

        results = audit_provider.query(AuditFilter(result="failure"))
        assert len(results) == 1
        assert results[0].result == "failure"

    def test_count(self, audit_provider):
        """Count events."""
        audit_provider.record(AuditEvent(actor="a", action="x"))
        audit_provider.record(AuditEvent(actor="b", action="y"))
        audit_provider.record(AuditEvent(actor="a", action="z"))

        assert audit_provider.count() == 3
        assert audit_provider.count(AuditFilter(actor="a")) == 2

    def test_export_json(self, audit_provider, tmp_path):
        """Export to JSON file."""
        audit_provider.record(AuditEvent(actor="admin", action="config.update"))
        audit_provider.record(AuditEvent(actor="user", action="login"))

        export_path = str(tmp_path / "audit_export.json")
        count = audit_provider.export(export_path, format="json")
        assert count == 2

        data = json.loads(Path(export_path).read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["actor"] in ("admin", "user")

    def test_export_yaml(self, audit_provider, tmp_path):
        """Export to YAML file."""
        audit_provider.record(AuditEvent(actor="admin", action="test"))

        export_path = str(tmp_path / "audit_export.yaml")
        count = audit_provider.export(export_path, format="yaml")
        assert count == 1
        assert Path(export_path).exists()


# ---------------------------------------------------------------------------
# SQLiteAuditProvider — boundary / error path
# ---------------------------------------------------------------------------

class TestSQLiteAuditProviderBoundary:
    def test_empty_detail(self, audit_provider):
        """Record with empty detail dict."""
        event = audit_provider.record(AuditEvent(actor="a", action="b", detail={}))
        assert event.id is not None
        results = audit_provider.query()
        assert results[0].detail == {}

    def test_special_chars_in_detail(self, audit_provider):
        """Detail with special characters and unicode."""
        detail = {"key": "value with 'quotes'", "unicode": "日本語"}
        audit_provider.record(AuditEvent(actor="a", action="b", detail=detail))
        results = audit_provider.query()
        assert results[0].detail["key"] == "value with 'quotes'"
        assert results[0].detail["unicode"] == "日本語"

    def test_query_empty_database(self, audit_provider):
        """Query on empty database returns empty list."""
        assert audit_provider.query() == []
        assert audit_provider.count() == 0

    def test_query_limit(self, audit_provider):
        """Limit is respected."""
        for i in range(10):
            audit_provider.record(AuditEvent(actor=f"user{i}", action="test"))
        results = audit_provider.query(AuditFilter(limit=5))
        assert len(results) == 5

    def test_query_offset(self, audit_provider):
        """Offset is respected."""
        for i in range(10):
            audit_provider.record(AuditEvent(actor=f"user{i}", action="test"))
        results = audit_provider.query(AuditFilter(limit=5, offset=5))
        assert len(results) == 5

    def test_concurrent_writes(self, audit_provider):
        """Thread-safe concurrent writes."""
        def writer(start: int, count: int) -> None:
            for i in range(start, start + count):
                audit_provider.record(AuditEvent(
                    actor=f"thread-{threading.current_thread().name}",
                    action=f"action-{i}",
                ))

        threads = []
        for i in range(4):
            t = threading.Thread(target=writer, args=(i * 25, 25))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert audit_provider.count() == 100


# ---------------------------------------------------------------------------
# AuditRegistry
# ---------------------------------------------------------------------------

class TestAuditRegistry:
    def test_register_and_create(self):
        """Register and create a provider."""
        reg = AuditRegistry()
        reg.register("test_provider", SQLiteAuditProvider)
        assert reg.has("test_provider")
        provider = reg.create("test_provider", db_path=Path(":memory:"))
        assert isinstance(provider, SQLiteAuditProvider)
        provider.close()

    def test_register_duplicate_raises(self):
        """Duplicate registration without override raises."""
        reg = AuditRegistry()
        reg.register("dup", SQLiteAuditProvider)
        with pytest.raises(Exception, match="already registered"):
            reg.register("dup", SQLiteAuditProvider)

    def test_register_override(self):
        """Override allows re-registration."""
        reg = AuditRegistry()
        reg.register("over", SQLiteAuditProvider)
        reg.register("over", SQLiteAuditProvider, override=True)
        assert reg.has("over")

    def test_unregister(self):
        """Unregister removes a provider."""
        reg = AuditRegistry()
        reg.register("tmp", SQLiteAuditProvider)
        assert reg.unregister("tmp") is True
        assert not reg.has("tmp")
        assert reg.unregister("tmp") is False  # already removed

    def test_create_unknown_raises(self):
        """Creating unknown provider raises RegistryError."""
        reg = AuditRegistry()
        with pytest.raises(Exception, match="Unknown audit provider"):
            reg.create("nonexistent")

    def test_count_property(self):
        reg = AuditRegistry()
        assert reg.count == 0
        reg.register("a", SQLiteAuditProvider)
        assert reg.count == 1
        reg.register("b", SQLiteAuditProvider)
        assert reg.count == 2

    def test_global_registry_has_defaults(self):
        """Global registry should have null and sqlite registered."""
        assert audit_registry.has("null")
        assert audit_registry.has("sqlite")


# ---------------------------------------------------------------------------
# AuditManager
# ---------------------------------------------------------------------------

class TestAuditManager:
    def test_disabled_manager_is_noop(self):
        """When disabled, manager uses NullAuditProvider."""
        mgr = AuditManager(provider="sqlite", enabled=False)
        assert mgr.enabled is False
        mgr.record_event(actor="user", action="test")
        assert mgr.count_events() == 0
        assert mgr.query_events() == []

    def test_enabled_manager_records(self, tmp_path):
        """When enabled, manager persists events."""
        db_path = tmp_path / "audit.db"
        mgr = AuditManager(
            provider="sqlite",
            enabled=True,
            db_path=db_path,
        )
        mgr.record_event(
            actor="admin",
            action="config.update",
            resource="agent:default",
            result="success",
            detail={"key": "val"},
        )
        assert mgr.count_events() == 1
        events = mgr.query_events()
        assert events[0].actor == "admin"
        assert events[0].action == "config.update"
        mgr.close()

    def test_manager_export(self, tmp_path):
        """Manager export writes file."""
        db_path = tmp_path / "audit.db"
        mgr = AuditManager(provider="sqlite", enabled=True, db_path=db_path)
        mgr.record_event(actor="a", action="b")
        export_path = str(tmp_path / "export.json")
        count = mgr.export_events(export_path)
        assert count == 1
        mgr.close()


# ---------------------------------------------------------------------------
# AuditFilter
# ---------------------------------------------------------------------------

class TestAuditFilter:
    def test_defaults(self):
        """Default filter has no constraints."""
        f = AuditFilter()
        assert f.actor is None
        assert f.action is None
        assert f.limit == 100
        assert f.offset == 0

    def test_custom_values(self):
        f = AuditFilter(
            actor="admin",
            action="login",
            result="success",
            limit=50,
            offset=10,
        )
        assert f.actor == "admin"
        assert f.action == "login"
        assert f.result == "success"
        assert f.limit == 50
        assert f.offset == 10
