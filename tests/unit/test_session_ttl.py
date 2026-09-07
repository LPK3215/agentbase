"""Tests for session TTL enhancement — expiration, cleanup, touch, concurrency."""
from __future__ import annotations

import threading
import time

import pytest

from agentbase.runtime.session import (
    Session,
    SessionRegistry,
    SessionStatus,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    """Create a fresh SessionRegistry for each test."""
    return SessionRegistry(max_history=100)


# ---------------------------------------------------------------------------
# Session TTL tests
# ---------------------------------------------------------------------------

class TestSessionTTL:
    def test_session_with_ttl_not_expired(self):
        """Session with TTL should not be expired immediately."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=10,
        )
        assert session.ttl_seconds == 10
        assert session.is_expired() is False

    def test_session_without_ttl_never_expires(self):
        """Session without TTL should never expire."""
        session = Session.create(agent_name="test")
        assert session.ttl_seconds is None
        assert session.is_expired() is False

    def test_session_expired_after_ttl(self):
        """Session should be expired after TTL seconds of inactivity."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,  # 100ms TTL
        )
        assert session.is_expired() is False
        time.sleep(0.15)
        assert session.is_expired() is True

    def test_touch_prevents_expiration(self):
        """Touch should reset the last_accessed_at and prevent expiration."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.2,
        )
        # Halfway through TTL, touch it
        time.sleep(0.1)
        session.touch()
        assert session.is_expired() is False
        # Wait another 0.15s — still not expired because we touched
        time.sleep(0.15)
        assert session.is_expired() is False
        # But after another 0.25s without touching, it should be expired
        time.sleep(0.25)
        assert session.is_expired() is True

    def test_touch_updates_last_accessed(self):
        """Touch should update last_accessed_at timestamp."""
        session = Session.create(agent_name="test", ttl_seconds=60)
        old_timestamp = session.last_accessed_at
        time.sleep(0.05)
        session.touch()
        assert session.last_accessed_at != old_timestamp
        assert session.last_accessed_at > old_timestamp

    def test_completed_session_not_expired_by_ttl(self):
        """Completed sessions should not be subject to TTL expiration."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,
        )
        session.mark_completed()
        time.sleep(0.15)
        assert session.is_expired() is False  # Completed — not subject to TTL

    def test_failed_session_not_expired_by_ttl(self):
        """Failed sessions should not be subject to TTL expiration."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,
        )
        session.mark_failed()
        time.sleep(0.15)
        assert session.is_expired() is False


# ---------------------------------------------------------------------------
# SessionRegistry cleanup_expired tests
# ---------------------------------------------------------------------------

class TestCleanupExpired:
    def test_cleanup_expired_removes_expired_sessions(self, registry):
        """cleanup_expired should mark expired sessions as FAILED."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,
        )
        registry.register(session)
        # Wait for TTL to expire
        time.sleep(0.15)
        cleaned = registry.cleanup_expired()
        assert cleaned == 1
        assert session.status == SessionStatus.FAILED
        assert session.metadata.get("ttl_expired") is True

    def test_cleanup_expired_skips_non_ttl_sessions(self, registry):
        """Sessions without TTL should not be cleaned up."""
        session = Session.create(agent_name="test")
        registry.register(session)
        cleaned = registry.cleanup_expired()
        assert cleaned == 0
        assert session.status == SessionStatus.PENDING

    def test_cleanup_expired_skips_active_sessions(self, registry):
        """Sessions within TTL should not be cleaned up."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=60,  # 1 minute — won't expire
        )
        registry.register(session)
        cleaned = registry.cleanup_expired()
        assert cleaned == 0

    def test_cleanup_expired_skips_completed_sessions(self, registry):
        """Completed sessions should not be cleaned up by TTL."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,
        )
        session.mark_completed()
        registry.register(session)
        time.sleep(0.15)
        cleaned = registry.cleanup_expired()
        assert cleaned == 0  # Already completed — not subject to TTL

    def test_cleanup_expired_multiple_sessions(self, registry):
        """Should clean up multiple expired sessions at once."""
        # Register expired sessions
        for i in range(5):
            s = Session.create(
                agent_name=f"agent_{i}",
                ttl_seconds=0.1,
            )
            registry.register(s)
        # Register non-expired sessions
        for i in range(3):
            s = Session.create(
                agent_name=f"active_{i}",
                ttl_seconds=60,
            )
            registry.register(s)
        time.sleep(0.15)
        cleaned = registry.cleanup_expired()
        assert cleaned == 5

    def test_list_expired(self, registry):
        """list_expired should return expired sessions without modifying them."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=0.1,
        )
        registry.register(session)
        time.sleep(0.15)
        expired = registry.list_expired()
        assert len(expired) == 1
        assert expired[0].thread_id == session.thread_id
        # Session should not be modified
        assert session.status == SessionStatus.PENDING


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class TestConcurrentTouch:
    def test_concurrent_touch_no_race(self, registry):
        """Concurrent touch calls should not cause race conditions."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=1.0,
        )
        registry.register(session)
        errors: list[Exception] = []

        def touch_worker():
            try:
                for _ in range(50):
                    session.touch()
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=touch_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert session.last_accessed_at is not None

    def test_concurrent_cleanup_and_touch(self, registry):
        """Concurrent cleanup + touch should not cause issues."""
        sessions = []
        for i in range(10):
            s = Session.create(
                agent_name=f"concurrent_{i}",
                ttl_seconds=0.5,
            )
            registry.register(s)
            sessions.append(s)

        errors: list[Exception] = []

        def toucher():
            try:
                for s in sessions:
                    s.touch()
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        def cleaner():
            try:
                registry.cleanup_expired()
            except Exception as exc:
                errors.append(exc)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=toucher))
        for _ in range(2):
            threads.append(threading.Thread(target=cleaner))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Session.create with TTL
# ---------------------------------------------------------------------------

class TestSessionCreateWithTTL:
    def test_create_with_ttl(self):
        """Session.create should accept ttl_seconds parameter."""
        session = Session.create(
            agent_name="test",
            ttl_seconds=30,
        )
        assert session.ttl_seconds == 30

    def test_create_without_ttl(self):
        """Session.create without ttl_seconds should have ttl=None."""
        session = Session.create(agent_name="test")
        assert session.ttl_seconds is None

    def test_create_with_last_accessed(self):
        """Session should have last_accessed_at set to creation time."""
        session = Session.create(agent_name="test")
        assert session.last_accessed_at is not None
        assert session.last_accessed_at == session.started_at
