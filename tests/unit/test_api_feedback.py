"""Tests for user feedback collection API endpoints.

Covers:
- GET /feedback — list records (filterable, paginated)
- POST /feedback — submit feedback (rating, comment, tags, metadata)
- GET /feedback/stats — aggregate statistics
- GET /feedback/{record_id} — get record detail
- PATCH /feedback/{record_id} — update record fields
- DELETE /feedback/{record_id} — delete a record
- Disabled manager returns empty/zero values
- 400 for empty thread_id
- 404 for non-existent records
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_feedback_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with feedback enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.feedback.enabled = True
    app_config.feedback.provider = "memory"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )
    return runtime


@pytest.fixture
def client(mock_runtime):
    """Client with feedback enabled, no auth."""
    reset_runtime()
    _reset_feedback_manager()
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
        _reset_feedback_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with feedback disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.feedback.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )
    reset_runtime()
    _reset_feedback_manager()
    old_key = os.environ.get("AGENTBASE_API_KEY", "")
    os.environ.pop("AGENTBASE_API_KEY", None)
    try:
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        if old_key:
            os.environ["AGENTBASE_API_KEY"] = old_key
        reset_runtime()
        _reset_feedback_manager()


# ---------------------------------------------------------------------------
# GET /feedback
# ---------------------------------------------------------------------------

class TestListFeedback:
    """Test GET /feedback."""

    def test_list_empty(self, client):
        """Empty list when no feedback exists."""
        resp = client.get("/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_after_create(self, client):
        """List shows created feedback."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5})
        resp = client.get("/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["thread_id"] == "t1"
        assert data["items"][0]["rating"] == 5

    def test_list_filter_thread_id(self, client):
        """Filter by thread_id works."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5})
        client.post("/feedback", json={"thread_id": "t2", "rating": 3})
        resp = client.get("/feedback?thread_id=t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["thread_id"] == "t1"

    def test_list_filter_agent_name(self, client):
        """Filter by agent_name works."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5, "agent_name": "a1"})
        client.post("/feedback", json={"thread_id": "t1", "rating": 3, "agent_name": "a2"})
        resp = client.get("/feedback?agent_name=a1")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_filter_sentiment(self, client):
        """Filter by sentiment works."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5})
        client.post("/feedback", json={"thread_id": "t1", "rating": 2})
        resp = client.get("/feedback?sentiment=positive")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_filter_min_rating(self, client):
        """Filter by min_rating works."""
        for r in [1, 2, 3, 4, 5]:
            client.post("/feedback", json={"thread_id": "t1", "rating": r})
        resp = client.get("/feedback?min_rating=4")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_list_filter_tags(self, client):
        """Filter by tags (comma-separated) works."""
        client.post("/feedback", json={"thread_id": "t1", "tags": ["helpful", "fast"]})
        client.post("/feedback", json={"thread_id": "t1", "tags": ["slow"]})
        resp = client.get("/feedback?tags=helpful")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_pagination(self, client):
        """Pagination works."""
        for i in range(5):
            client.post("/feedback", json={"thread_id": "t1", "rating": i + 1})
        resp = client.get("/feedback?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_list_disabled_returns_empty(self, client_disabled):
        """Disabled manager returns empty list."""
        resp = client_disabled.get("/feedback")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------

class TestCreateFeedback:
    """Test POST /feedback."""

    def test_create_with_rating(self, client):
        """Create feedback with a star rating."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "rating": 5,
            "comment": "Excellent!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == "t1"
        assert data["rating"] == 5
        assert data["comment"] == "Excellent!"
        assert data["sentiment"] == "positive"
        assert data["id"]

    def test_create_thumbs_up(self, client):
        """Create feedback with thumbs up (+1)."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "rating": 1,
        })
        assert resp.status_code == 200
        assert resp.json()["sentiment"] == "positive"

    def test_create_thumbs_down(self, client):
        """Create feedback with thumbs down (-1)."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "rating": -1,
        })
        assert resp.status_code == 200
        assert resp.json()["sentiment"] == "negative"

    def test_create_comment_only(self, client):
        """Create feedback without rating (comment-only)."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "comment": "Just a comment",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["rating"] is None
        assert data["sentiment"] == "unknown"

    def test_create_with_tags(self, client):
        """Create feedback with tags."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "rating": 5,
            "tags": ["helpful", "fast"],
        })
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["helpful", "fast"]

    def test_create_with_metadata(self, client):
        """Create feedback with metadata."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "metadata": {"source": "web", "version": "1.0"},
        })
        assert resp.status_code == 200
        assert resp.json()["metadata"] == {"source": "web", "version": "1.0"}

    def test_create_with_agent_name(self, client):
        """Create feedback linked to an agent."""
        resp = client.post("/feedback", json={
            "thread_id": "t1",
            "rating": 4,
            "agent_name": "assistant",
        })
        assert resp.status_code == 200
        assert resp.json()["agent_name"] == "assistant"

    def test_create_empty_thread_id_400(self, client):
        """Empty thread_id returns 400."""
        resp = client.post("/feedback", json={
            "thread_id": "",
            "rating": 5,
        })
        assert resp.status_code == 400

    def test_create_disabled_no_error(self, client_disabled):
        """Creating feedback when disabled doesn't raise (stored in NullProvider)."""
        resp = client_disabled.post("/feedback", json={
            "thread_id": "t1",
            "rating": 5,
        })
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "t1"


# ---------------------------------------------------------------------------
# GET /feedback/stats
# ---------------------------------------------------------------------------

class TestFeedbackStats:
    """Test GET /feedback/stats."""

    def test_stats_empty(self, client):
        """Empty stats when no feedback exists."""
        resp = client.get("/feedback/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["average_rating"] == 0.0

    def test_stats_after_feedback(self, client):
        """Stats reflect submitted feedback."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5, "agent_name": "a1"})
        client.post("/feedback", json={"thread_id": "t1", "rating": 3, "agent_name": "a1"})
        client.post("/feedback", json={"thread_id": "t2", "rating": 1, "agent_name": "a2"})
        resp = client.get("/feedback/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["average_rating"] == 3.0
        assert "a1" in data["by_agent"]
        assert "a2" in data["by_agent"]

    def test_stats_filtered_by_agent(self, client):
        """Stats filtered by agent_name."""
        client.post("/feedback", json={"thread_id": "t1", "rating": 5, "agent_name": "a1"})
        client.post("/feedback", json={"thread_id": "t1", "rating": 1, "agent_name": "a2"})
        resp = client.get("/feedback/stats?agent_name=a1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["average_rating"] == 5.0

    def test_stats_disabled(self, client_disabled):
        """Disabled returns zero stats."""
        resp = client_disabled.get("/feedback/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# GET /feedback/{record_id}
# ---------------------------------------------------------------------------

class TestGetFeedback:
    """Test GET /feedback/{record_id}."""

    def test_get_existing(self, client):
        """Get an existing feedback record."""
        create_resp = client.post("/feedback", json={"thread_id": "t1", "rating": 5})
        record_id = create_resp.json()["id"]
        resp = client.get(f"/feedback/{record_id}")
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "t1"
        assert resp.json()["rating"] == 5

    def test_get_not_found_404(self, client):
        """Get returns 404 for unknown ID."""
        resp = client.get("/feedback/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /feedback/{record_id}
# ---------------------------------------------------------------------------

class TestUpdateFeedback:
    """Test PATCH /feedback/{record_id}."""

    def test_update_rating(self, client):
        """Update the rating field."""
        create_resp = client.post("/feedback", json={"thread_id": "t1", "rating": 3})
        record_id = create_resp.json()["id"]
        resp = client.patch(f"/feedback/{record_id}", json={"rating": 5})
        assert resp.status_code == 200
        assert resp.json()["rating"] == 5
        assert resp.json()["sentiment"] == "positive"

    def test_update_comment(self, client):
        """Update the comment field."""
        create_resp = client.post("/feedback", json={"thread_id": "t1", "comment": "old"})
        record_id = create_resp.json()["id"]
        resp = client.patch(f"/feedback/{record_id}", json={"comment": "new"})
        assert resp.status_code == 200
        assert resp.json()["comment"] == "new"

    def test_update_tags(self, client):
        """Update the tags field."""
        create_resp = client.post("/feedback", json={"thread_id": "t1"})
        record_id = create_resp.json()["id"]
        resp = client.patch(f"/feedback/{record_id}", json={"tags": ["helpful"]})
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["helpful"]

    def test_update_not_found_404(self, client):
        """Update returns 404 for unknown ID."""
        resp = client.patch("/feedback/nonexistent", json={"rating": 5})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /feedback/{record_id}
# ---------------------------------------------------------------------------

class TestDeleteFeedback:
    """Test DELETE /feedback/{record_id}."""

    def test_delete_existing(self, client):
        """Delete an existing record."""
        create_resp = client.post("/feedback", json={"thread_id": "t1"})
        record_id = create_resp.json()["id"]
        resp = client.delete(f"/feedback/{record_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Verify it's gone
        resp2 = client.get(f"/feedback/{record_id}")
        assert resp2.status_code == 404

    def test_delete_not_found_404(self, client):
        """Delete returns 404 for unknown ID."""
        resp = client.delete("/feedback/nonexistent")
        assert resp.status_code == 404
