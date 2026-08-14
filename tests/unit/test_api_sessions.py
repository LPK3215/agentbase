"""Tests for session management API endpoints — covers list, stats, get, cancel, cleanup."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import create_app, reset_runtime
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.runtime.session import (
    Session,
    get_session_registry,
)


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext for testing."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    def fake_get_agent_config(name=None):
        return AgentConfig(
            name=name or "default",
            description="Test agent",
            system_prompt="You are a test agent.",
            tools=["echo"],
        )

    runtime.get_agent_config = fake_get_agent_config

    return runtime


@pytest.fixture
def client(mock_runtime):
    """Client with auth disabled (dev mode)."""
    reset_runtime()
    import os

    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ.pop("AGENTBASE_API_KEY", None)
    try:
        app = create_app(runtime=mock_runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        reset_runtime()


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the global session registry before and after each test."""
    registry = get_session_registry()
    with registry._lock:
        registry._sessions.clear()
    yield
    with registry._lock:
        registry._sessions.clear()


class TestSessionListAPI:
    """Test GET /sessions endpoint."""

    def test_list_empty(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_sessions(self, client):
        Session.create(agent_name="default", thread_id="thread-1")
        Session.create(agent_name="default", thread_id="thread-2")
        resp = client.get("/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        thread_ids = {s["thread_id"] for s in data["items"]}
        assert thread_ids == {"thread-1", "thread-2"}

    def test_list_filter_by_agent(self, client):
        Session.create(agent_name="default", thread_id="t1")
        Session.create(agent_name="researcher", thread_id="t2")
        resp = client.get("/sessions?agent=default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["agent_name"] == "default"

    def test_list_filter_by_status(self, client):
        s1 = Session.create(agent_name="default", thread_id="t1")
        s1.mark_running()
        Session.create(agent_name="default", thread_id="t2")
        resp = client.get("/sessions?status=running")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "running"

    def test_list_filter_by_invalid_status(self, client):
        resp = client.get("/sessions?status=invalid")
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_list_filter_by_agent_and_status(self, client):
        s1 = Session.create(agent_name="default", thread_id="t1")
        s1.mark_running()
        s2 = Session.create(agent_name="researcher", thread_id="t2")
        s2.mark_running()
        Session.create(agent_name="default", thread_id="t3")
        resp = client.get("/sessions?agent=default&status=running")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["thread_id"] == "t1"

    def test_list_returns_correct_fields(self, client):
        Session.create(agent_name="default", thread_id="t1")
        resp = client.get("/sessions")
        data = resp.json()
        item = data["items"][0]
        assert "thread_id" in item
        assert "agent_name" in item
        assert "status" in item
        assert "started_at" in item
        assert "last_accessed_at" in item
        assert "metadata" in item
        assert "duration_ms" in item


class TestSessionStatsAPI:
    """Test GET /sessions/stats endpoint."""

    def test_stats_empty(self, client):
        resp = client.get("/sessions/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_stats_with_sessions(self, client):
        s1 = Session.create(agent_name="default", thread_id="t1")
        s1.mark_running()
        s2 = Session.create(agent_name="default", thread_id="t2")
        s2.mark_completed()
        s3 = Session.create(agent_name="default", thread_id="t3")
        s3.mark_failed()
        resp = client.get("/sessions/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["running"] == 1
        assert data["completed"] == 1
        assert data["failed"] == 1


class TestSessionGetAPI:
    """Test GET /sessions/{thread_id} endpoint."""

    def test_get_existing(self, client):
        Session.create(agent_name="default", thread_id="t1")
        resp = client.get("/sessions/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == "t1"
        assert data["agent_name"] == "default"
        assert data["status"] == "pending"

    def test_get_not_found(self, client):
        resp = client.get("/sessions/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_after_status_change(self, client):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_running()
        s.mark_completed()
        resp = client.get("/sessions/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["finished_at"] is not None
        assert data["duration_ms"] is not None


class TestSessionCancelAPI:
    """Test DELETE /sessions/{thread_id} endpoint."""

    def test_cancel_pending(self, client):
        Session.create(agent_name="default", thread_id="t1")
        resp = client.delete("/sessions/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True
        assert data["thread_id"] == "t1"

    def test_cancel_running(self, client):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_running()
        resp = client.delete("/sessions/t1")
        assert resp.status_code == 200
        # Verify status changed
        resp2 = client.get("/sessions/t1")
        assert resp2.json()["status"] == "cancelled"

    def test_cancel_not_found(self, client):
        resp = client.delete("/sessions/nonexistent")
        assert resp.status_code == 404

    def test_cancel_already_completed(self, client):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_completed()
        resp = client.delete("/sessions/t1")
        assert resp.status_code == 409
        assert "terminal" in resp.json()["detail"].lower()

    def test_cancel_already_cancelled(self, client):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_cancelled()
        resp = client.delete("/sessions/t1")
        assert resp.status_code == 409

    def test_cancel_already_failed(self, client):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_failed()
        resp = client.delete("/sessions/t1")
        assert resp.status_code == 409


class TestSessionCleanupAPI:
    """Test POST /sessions/cleanup endpoint."""

    def test_cleanup_expired_mode(self, client):
        # Create a session with a very short TTL
        Session.create(
            agent_name="default",
            thread_id="t1",
            ttl_seconds=0.01,
        )
        # Wait for it to expire
        time.sleep(0.05)
        resp = client.post("/sessions/cleanup?mode=expired")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "expired"
        assert data["cleaned"] == 1

    def test_cleanup_completed_mode(self, client):
        s1 = Session.create(agent_name="default", thread_id="t1")
        s1.mark_completed()
        s2 = Session.create(agent_name="default", thread_id="t2")
        s2.mark_failed()
        Session.create(agent_name="default", thread_id="t3")
        # s3 is pending, should NOT be removed
        resp = client.post("/sessions/cleanup?mode=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 2
        # Verify s3 still exists
        resp2 = client.get("/sessions/t3")
        assert resp2.status_code == 200

    def test_cleanup_stale_mode(self, client):
        # Create a session and mark it running
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_running()
        # Wait briefly so started_at is before the cutoff (timeout_seconds=0)
        time.sleep(0.05)
        resp = client.post("/sessions/cleanup?mode=stale&timeout_seconds=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "stale"
        assert data["cleaned"] == 1
        assert data["timeout_seconds"] == 0

    def test_cleanup_invalid_mode(self, client):
        resp = client.post("/sessions/cleanup?mode=invalid")
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    def test_cleanup_default_mode_is_expired(self, client):
        """Default mode should be 'expired'."""
        # Create a session with a very short TTL
        Session.create(
            agent_name="default",
            thread_id="t1",
            ttl_seconds=0.01,
        )
        time.sleep(0.05)
        resp = client.post("/sessions/cleanup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "expired"

    def test_cleanup_expired_no_expired_sessions(self, client):
        """If no sessions are expired, cleaned should be 0."""
        Session.create(agent_name="default", thread_id="t1")
        resp = client.post("/sessions/cleanup?mode=expired")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cleaned"] == 0

    def test_cleanup_completed_no_completed_sessions(self, client):
        """If no sessions are completed, removed should be 0."""
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_running()
        resp = client.post("/sessions/cleanup?mode=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 0


class TestSessionToDict:
    """Test Session.to_dict() method directly."""

    def test_to_dict_pending(self):
        s = Session.create(agent_name="default", thread_id="t1")
        d = s.to_dict()
        assert d["thread_id"] == "t1"
        assert d["agent_name"] == "default"
        assert d["status"] == "pending"
        assert d["started_at"] != ""
        assert d["last_accessed_at"] != ""
        assert d["finished_at"] is None
        assert d["duration_ms"] is None
        assert d["metadata"] == {}
        assert d["ttl_seconds"] is None

    def test_to_dict_completed(self):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_completed()
        d = s.to_dict()
        assert d["status"] == "completed"
        assert d["finished_at"] is not None
        assert d["duration_ms"] is not None

    def test_to_dict_with_metadata(self):
        s = Session.create(
            agent_name="default",
            thread_id="t1",
            metadata={"request_id": "req-123", "user_id": "user-1"},
        )
        d = s.to_dict()
        assert d["metadata"]["request_id"] == "req-123"
        assert d["metadata"]["user_id"] == "user-1"

    def test_to_dict_with_ttl(self):
        s = Session.create(
            agent_name="default",
            thread_id="t1",
            ttl_seconds=3600,
        )
        d = s.to_dict()
        assert d["ttl_seconds"] == 3600

    def test_to_dict_cancelled(self):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_cancelled()
        d = s.to_dict()
        assert d["status"] == "cancelled"
        assert d["finished_at"] is not None

    def test_to_dict_failed(self):
        s = Session.create(agent_name="default", thread_id="t1")
        s.mark_failed()
        d = s.to_dict()
        assert d["status"] == "failed"
        assert d["finished_at"] is not None
