"""Agent invocation session management.

A :class:`Session` carries thread ID, agent name, metadata, and lifecycle
state. The :class:`SessionRegistry` tracks all active sessions for
observability, timeout enforcement, and cleanup.

Lifecycle states:
    PENDING → RUNNING → COMPLETED
                    ↘ FAILED
                    ↘ CANCELLED
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    """Lifecycle status of a session."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    """Represents a single agent invocation session.

    A session carries:
    - ``thread_id``: unique conversation thread identifier (for checkpointing)
    - ``agent_name``: the agent that was invoked
    - ``metadata``: arbitrary session-level metadata (request_id, user_id, etc.)
    - ``started_at``: ISO timestamp for duration tracking
    - ``status``: lifecycle status (pending/running/completed/failed/cancelled)
    """

    thread_id: str
    agent_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_now)
    status: SessionStatus = SessionStatus.PENDING
    finished_at: str | None = None

    @staticmethod
    def create(
        agent_name: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        session = Session(
            thread_id=thread_id or str(uuid.uuid4()),
            agent_name=agent_name,
            metadata=metadata or {},
        )
        # Auto-register with the global registry
        _registry.register(session)
        return session

    def runnable_config(self, recursion_limit: int = 50) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": self.thread_id,
            },
            "recursion_limit": recursion_limit,
        }

    @property
    def request_id(self) -> str | None:
        """Extract request_id from metadata if present."""
        return self.metadata.get("request_id")

    def mark_running(self) -> None:
        """Mark the session as running."""
        self.status = SessionStatus.RUNNING
        _registry.update(self)

    def mark_completed(self) -> None:
        """Mark the session as completed."""
        self.status = SessionStatus.COMPLETED
        self.finished_at = _now()
        _registry.update(self)

    def mark_failed(self) -> None:
        """Mark the session as failed."""
        self.status = SessionStatus.FAILED
        self.finished_at = _now()
        _registry.update(self)

    def mark_cancelled(self) -> None:
        """Mark the session as cancelled."""
        self.status = SessionStatus.CANCELLED
        self.finished_at = _now()
        _registry.update(self)

    @property
    def duration_ms(self) -> float | None:
        """Return the session duration in milliseconds, or None if not finished."""
        if self.finished_at is None:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.finished_at)
            return (end - start).total_seconds() * 1000
        except Exception:
            return None


class SessionRegistry:
    """Thread-safe registry of all active and recently completed sessions.

    Used for:
    - Observability — track how many sessions are active
    - Timeout enforcement — find sessions that have been running too long
    - Cleanup — remove old completed sessions from memory
    """

    def __init__(self, *, max_history: int = 1000) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

    def register(self, session: Session) -> None:
        """Register a new session."""
        with self._lock:
            self._sessions[session.thread_id] = session
            self._evict_if_needed()

    def update(self, session: Session) -> None:
        """Update a session in the registry (status change, etc.)."""
        with self._lock:
            self._sessions[session.thread_id] = session

    def get(self, thread_id: str) -> Session | None:
        """Get a session by thread ID."""
        with self._lock:
            return self._sessions.get(thread_id)

    def list_active(self) -> list[Session]:
        """Return all sessions that are currently running."""
        with self._lock:
            return [
                s for s in self._sessions.values()
                if s.status == SessionStatus.RUNNING
            ]

    def list_by_agent(self, agent_name: str) -> list[Session]:
        """Return all sessions for a given agent."""
        with self._lock:
            return [
                s for s in self._sessions.values()
                if s.agent_name == agent_name
            ]

    def count_by_status(self) -> dict[str, int]:
        """Return counts of sessions by status."""
        with self._lock:
            counts: dict[str, int] = {}
            for session in self._sessions.values():
                status = session.status.value
                counts[status] = counts.get(status, 0) + 1
            counts["total"] = len(self._sessions)
            return counts

    def clear(self, *, keep_active: bool = True) -> int:
        """Remove completed/failed sessions. Returns count of removed."""
        with self._lock:
            to_remove = []
            keep_statuses = {SessionStatus.PENDING, SessionStatus.RUNNING} if keep_active else set()
            for tid, session in self._sessions.items():
                if session.status not in keep_statuses:
                    to_remove.append(tid)
            for tid in to_remove:
                self._sessions.pop(tid, None)
            return len(to_remove)

    def cleanup_stale(self, *, timeout_seconds: float = 300) -> int:
        """Mark sessions that have been running longer than ``timeout_seconds``
        as failed and remove them.

        This is the timeout enforcement mechanism — sessions stuck in
        ``RUNNING`` state (e.g. due to a crashed worker) are cleaned up.

        Args:
            timeout_seconds: Maximum allowed duration for a running session.
                Default 300 (5 minutes).

        Returns:
            Number of stale sessions cleaned up.
        """
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        cleaned = 0
        with self._lock:
            stale_tids: list[str] = []
            for tid, session in self._sessions.items():
                if session.status not in {SessionStatus.PENDING, SessionStatus.RUNNING}:
                    continue
                try:
                    started = datetime.fromisoformat(session.started_at)
                    if started < cutoff:
                        stale_tids.append(tid)
                except Exception:
                    continue
            for tid in stale_tids:
                session = self._sessions.get(tid)
                if session:
                    session.status = SessionStatus.FAILED
                    session.finished_at = _now()
                    session.metadata["stale_cleanup"] = True
                    cleaned += 1
        return cleaned

    def _evict_if_needed(self) -> None:
        """Evict oldest completed sessions if over capacity."""
        if len(self._sessions) <= self._max_history:
            return
        # Sort by started_at, evict oldest non-active sessions
        completed = [
            (tid, s) for tid, s in self._sessions.items()
            if s.status not in {SessionStatus.PENDING, SessionStatus.RUNNING}
        ]
        completed.sort(key=lambda x: x[1].started_at)
        to_evict = len(self._sessions) - self._max_history
        for tid, _ in completed[:to_evict]:
            self._sessions.pop(tid, None)


# Global singleton
_registry = SessionRegistry()


def get_session_registry() -> SessionRegistry:
    """Return the global session registry singleton."""
    return _registry
