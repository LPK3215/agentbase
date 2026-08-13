"""Tests for session lifecycle and SessionRegistry — complements test_session_ttl.py.

Covers:
1. Session.create — auto thread_id, auto-register, metadata, ttl
2. Session.runnable_config — configurable thread_id + recursion_limit
3. Session.request_id — from metadata
4. Session lifecycle — mark_running/completed/failed/cancelled
5. Session.duration_ms — None when active, value when finished
6. SessionRegistry — register, update, get, list_active, list_by_agent, count_by_status
7. SessionRegistry.clear — keep_active True/False
8. SessionRegistry.cleanup_stale — timeout enforcement
9. SessionRegistry._evict_if_needed — max_history eviction
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionCreate:
    def test_create_auto_thread_id(self):
        from agentbase.runtime.session import Session

        session = Session.create(agent_name="test_agent")
        assert session.thread_id != ""
        assert len(session.thread_id) == 36  # UUID format
        assert session.agent_name == "test_agent"

    def test_create_with_explicit_thread_id(self):
        from agentbase.runtime.session import Session

        session = Session.create(agent_name="test_agent", thread_id="custom-tid")
        assert session.thread_id == "custom-tid"

    def test_create_with_metadata(self):
        from agentbase.runtime.session import Session

        session = Session.create(
            agent_name="test_agent",
            metadata={"request_id": "req-123", "user_id": "user-456"},
        )
        assert session.metadata["request_id"] == "req-123"
        assert session.metadata["user_id"] == "user-456"

    def test_create_with_ttl(self):
        from agentbase.runtime.session import Session

        session = Session.create(agent_name="test_agent", ttl_seconds=60)
        assert session.ttl_seconds == 60

    def test_create_default_status_pending(self):
        from agentbase.runtime.session import Session, SessionStatus

        session = Session.create(agent_name="test_agent")
        assert session.status == SessionStatus.PENDING

    def test_create_auto_registers(self):
        from agentbase.runtime.session import Session, _registry

        session = Session.create(agent_name="test_agent", thread_id="test-auto-reg")
        assert _registry.get("test-auto-reg") is not None


class TestSessionRunnableConfig:
    def test_default_recursion_limit(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1")
        config = session.runnable_config()
        assert config["configurable"]["thread_id"] == "t1"
        assert config["recursion_limit"] == 50

    def test_custom_recursion_limit(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1")
        config = session.runnable_config(recursion_limit=100)
        assert config["recursion_limit"] == 100


class TestSessionRequestId:
    def test_request_id_present(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1", metadata={"request_id": "req-123"})
        assert session.request_id == "req-123"

    def test_request_id_absent(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1")
        assert session.request_id is None


class TestSessionLifecycle:
    def test_mark_running(self):
        from agentbase.runtime.session import Session, SessionStatus

        session = Session(thread_id="t1", agent_name="a1")
        session.mark_running()
        assert session.status == SessionStatus.RUNNING

    def test_mark_completed(self):
        from agentbase.runtime.session import Session, SessionStatus

        session = Session(thread_id="t1", agent_name="a1")
        session.mark_completed()
        assert session.status == SessionStatus.COMPLETED
        assert session.finished_at is not None

    def test_mark_failed(self):
        from agentbase.runtime.session import Session, SessionStatus

        session = Session(thread_id="t1", agent_name="a1")
        session.mark_failed()
        assert session.status == SessionStatus.FAILED
        assert session.finished_at is not None

    def test_mark_cancelled(self):
        from agentbase.runtime.session import Session, SessionStatus

        session = Session(thread_id="t1", agent_name="a1")
        session.mark_cancelled()
        assert session.status == SessionStatus.CANCELLED
        assert session.finished_at is not None


class TestSessionDuration:
    def test_duration_none_when_active(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1")
        assert session.duration_ms is None

    def test_duration_when_finished(self):
        from agentbase.runtime.session import Session

        session = Session(thread_id="t1", agent_name="a1")
        time.sleep(0.01)
        session.mark_completed()
        assert session.duration_ms is not None
        assert session.duration_ms > 0

    def test_duration_with_invalid_timestamps(self):
        from agentbase.runtime.session import Session

        session = Session(
            thread_id="t1",
            agent_name="a1",
            started_at="invalid",
        )
        session.finished_at = "also-invalid"
        assert session.duration_ms is None


# ---------------------------------------------------------------------------
# SessionRegistry
# ---------------------------------------------------------------------------


class TestSessionRegistry:
    def test_register_and_get(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        session = Session(thread_id="t1", agent_name="a1")
        registry.register(session)
        assert registry.get("t1") is session

    def test_get_nonexistent(self):
        from agentbase.runtime.session import SessionRegistry

        registry = SessionRegistry()
        assert registry.get("nonexistent") is None

    def test_update(self):
        from agentbase.runtime.session import Session, SessionRegistry, SessionStatus

        registry = SessionRegistry()
        session = Session(thread_id="t1", agent_name="a1")
        registry.register(session)
        session.mark_running()
        registry.update(session)
        retrieved = registry.get("t1")
        assert retrieved.status == SessionStatus.RUNNING

    def test_list_active(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        s1 = Session(thread_id="t1", agent_name="a1")
        s2 = Session(thread_id="t2", agent_name="a2")
        registry.register(s1)
        registry.register(s2)
        s1.mark_running()
        registry.update(s1)
        active = registry.list_active()
        assert len(active) == 1
        assert active[0].thread_id == "t1"

    def test_list_active_empty(self):
        from agentbase.runtime.session import SessionRegistry

        registry = SessionRegistry()
        assert registry.list_active() == []

    def test_list_by_agent(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        registry.register(Session(thread_id="t1", agent_name="agent_a"))
        registry.register(Session(thread_id="t2", agent_name="agent_b"))
        registry.register(Session(thread_id="t3", agent_name="agent_a"))
        result = registry.list_by_agent("agent_a")
        assert len(result) == 2

    def test_list_by_agent_no_match(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        registry.register(Session(thread_id="t1", agent_name="agent_a"))
        result = registry.list_by_agent("nonexistent")
        assert result == []

    def test_count_by_status(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        s1 = Session(thread_id="t1", agent_name="a1")
        s2 = Session(thread_id="t2", agent_name="a2")
        registry.register(s1)
        registry.register(s2)
        s1.mark_running()
        s2.mark_completed()
        registry.update(s1)
        registry.update(s2)
        counts = registry.count_by_status()
        assert counts["total"] == 2
        assert counts.get("running", 0) == 1
        assert counts.get("completed", 0) == 1

    def test_count_by_status_empty(self):
        from agentbase.runtime.session import SessionRegistry

        registry = SessionRegistry()
        counts = registry.count_by_status()
        assert counts["total"] == 0


class TestSessionRegistryClear:
    def test_clear_keep_active(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        s1 = Session(thread_id="t1", agent_name="a1")
        s2 = Session(thread_id="t2", agent_name="a2")
        registry.register(s1)
        registry.register(s2)
        s1.mark_running()
        s2.mark_completed()
        registry.update(s1)
        registry.update(s2)
        removed = registry.clear(keep_active=True)
        assert removed == 1  # Only completed session removed
        assert registry.get("t1") is not None  # Running kept
        assert registry.get("t2") is None  # Completed removed

    def test_clear_all(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        s1 = Session(thread_id="t1", agent_name="a1")
        s2 = Session(thread_id="t2", agent_name="a2")
        registry.register(s1)
        registry.register(s2)
        s1.mark_running()
        s2.mark_completed()
        registry.update(s1)
        registry.update(s2)
        removed = registry.clear(keep_active=False)
        assert removed == 2
        assert registry.get("t1") is None
        assert registry.get("t2") is None

    def test_clear_empty(self):
        from agentbase.runtime.session import SessionRegistry

        registry = SessionRegistry()
        removed = registry.clear()
        assert removed == 0


class TestSessionRegistryCleanupStale:
    def test_cleanup_stale_removes_old_running(self):
        from agentbase.runtime.session import Session, SessionRegistry, SessionStatus

        registry = SessionRegistry()
        # Create a session with an old started_at
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        session = Session(
            thread_id="stale1",
            agent_name="a1",
            started_at=old_time,
        )
        session.mark_running()
        registry.register(session)

        cleaned = registry.cleanup_stale(timeout_seconds=300)
        assert cleaned == 1
        assert registry.get("stale1").status == SessionStatus.FAILED

    def test_cleanup_stale_skips_recent(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        session = Session(thread_id="recent1", agent_name="a1")
        session.mark_running()
        registry.register(session)

        cleaned = registry.cleanup_stale(timeout_seconds=300)
        assert cleaned == 0

    def test_cleanup_stale_skips_completed(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        session = Session(
            thread_id="old_completed",
            agent_name="a1",
            started_at=old_time,
        )
        session.mark_completed()
        registry.register(session)

        cleaned = registry.cleanup_stale(timeout_seconds=300)
        assert cleaned == 0

    def test_cleanup_stale_empty(self):
        from agentbase.runtime.session import SessionRegistry

        registry = SessionRegistry()
        cleaned = registry.cleanup_stale()
        assert cleaned == 0

    def test_cleanup_stale_marks_metadata(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry()
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        session = Session(
            thread_id="stale_meta",
            agent_name="a1",
            started_at=old_time,
        )
        session.mark_running()
        registry.register(session)

        registry.cleanup_stale(timeout_seconds=300)
        retrieved = registry.get("stale_meta")
        assert retrieved.metadata.get("stale_cleanup") is True


class TestSessionRegistryEviction:
    def test_evict_when_over_limit(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry(max_history=3)
        # Register 5 sessions, mark first 2 as completed so they can be evicted
        for i in range(5):
            s = Session(thread_id=f"t{i}", agent_name="a1")
            if i < 2:
                s.mark_completed()
            registry.register(s)
        # Eviction only removes completed sessions when over capacity
        # With max_history=3, 2 completed sessions should be evicted
        assert len(registry._sessions) <= 3

    def test_no_evict_when_under_limit(self):
        from agentbase.runtime.session import Session, SessionRegistry

        registry = SessionRegistry(max_history=10)
        for i in range(5):
            registry.register(Session(thread_id=f"t{i}", agent_name="a1"))
        assert len(registry._sessions) == 5


# ---------------------------------------------------------------------------
# SessionStatus enum
# ---------------------------------------------------------------------------


class TestSessionStatus:
    def test_enum_values(self):
        from agentbase.runtime.session import SessionStatus

        assert SessionStatus.PENDING.value == "pending"
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.FAILED.value == "failed"
        assert SessionStatus.CANCELLED.value == "cancelled"

    def test_is_str_enum(self):
        from agentbase.runtime.session import SessionStatus

        assert isinstance(SessionStatus.PENDING, str)
        assert SessionStatus.PENDING == "pending"
