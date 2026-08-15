"""Tests for the notification center API endpoints.

Covers:
- GET /notifications — list (filterable, paginated)
- POST /notifications — create
- GET /notifications/stats — aggregate statistics
- GET /notifications/unread-count — unread count
- POST /notifications/broadcast — broadcast to all
- POST /notifications/read-all — mark all as read
- GET /notifications/{id} — get detail
- PATCH /notifications/{id} — update fields
- POST /notifications/{id}/read — mark as read
- POST /notifications/{id}/unread — mark as unread
- DELETE /notifications/{id} — delete
- Disabled manager returns empty/zero values
- 400 for empty user_id / title
- 404 for non-existent notifications
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_notification_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with notifications enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.notification.enabled = True
    app_config.notification.provider = "memory"

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
    """Client with notifications enabled, no auth."""
    reset_runtime()
    _reset_notification_manager()
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
        _reset_notification_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with notifications disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    # notification.enabled defaults to False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test",
    )

    reset_runtime()
    _reset_notification_manager()
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
        _reset_notification_manager()


# ---------------------------------------------------------------------------
# GET /notifications
# ---------------------------------------------------------------------------

class TestListNotifications:
    """Tests for GET /notifications."""

    def test_empty(self, client):
        resp = client.get("/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_all(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u2", "title": "B"})
        resp = client.get("/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_filter_by_user(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u2", "title": "B"})
        client.post("/notifications", json={"user_id": "*", "title": "C"})
        resp = client.get("/notifications?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        # u1's notification + broadcast
        assert data["total"] == 2

    def test_filter_by_user_no_broadcast(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications/broadcast", json={"title": "B"})
        resp = client.get("/notifications?user_id=u1&include_broadcast=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "A"

    def test_filter_by_category(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A", "category": "system"})
        client.post("/notifications", json={"user_id": "u1", "title": "B", "category": "security"})
        resp = client.get("/notifications?category=security")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "B"

    def test_filter_by_severity(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A", "severity": "info"})
        client.post("/notifications", json={"user_id": "u1", "title": "B", "severity": "critical"})
        resp = client.get("/notifications?severity=critical")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_filter_unread_only(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u1", "title": "B"})
        notif_id = r.json()["id"]
        client.post(f"/notifications/{notif_id}/read")
        resp = client.get("/notifications?unread_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_pagination(self, client):
        for i in range(5):
            client.post("/notifications", json={"user_id": "u1", "title": f"N{i}"})
        resp = client.get("/notifications?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_disabled_returns_empty(self, client_disabled):
        resp = client_disabled.get("/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# POST /notifications
# ---------------------------------------------------------------------------

class TestCreateNotification:
    """Tests for POST /notifications."""

    def test_create_minimal(self, client):
        resp = client.post("/notifications", json={"user_id": "u1", "title": "Hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "u1"
        assert data["title"] == "Hello"
        assert data["category"] == "system"
        assert data["severity"] == "info"
        assert data["read"] is False
        assert "id" in data

    def test_create_full(self, client):
        resp = client.post("/notifications", json={
            "user_id": "u1",
            "title": "Quota Alert",
            "message": "You have used 90% of quota",
            "category": "quota_alert",
            "severity": "warning",
            "action_url": "/usage",
            "action_label": "View Usage",
            "metadata": {"threshold": 90},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "quota_alert"
        assert data["severity"] == "warning"
        assert data["action_url"] == "/usage"

    def test_create_missing_user_id(self, client):
        resp = client.post("/notifications", json={"title": "A"})
        assert resp.status_code == 422

    def test_create_missing_title(self, client):
        resp = client.post("/notifications", json={"user_id": "u1"})
        assert resp.status_code == 422

    def test_create_empty_user_id(self, client):
        resp = client.post("/notifications", json={"user_id": "", "title": "A"})
        assert resp.status_code == 400

    def test_create_empty_title(self, client):
        resp = client.post("/notifications", json={"user_id": "u1", "title": ""})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /notifications/broadcast
# ---------------------------------------------------------------------------

class TestBroadcastNotification:
    """Tests for POST /notifications/broadcast."""

    def test_broadcast(self, client):
        resp = client.post("/notifications/broadcast", json={
            "title": "System Maintenance",
            "message": "Down at 2 AM",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "*"
        assert data["title"] == "System Maintenance"

    def test_broadcast_missing_title(self, client):
        resp = client.post("/notifications/broadcast", json={"message": "A"})
        assert resp.status_code == 422

    def test_broadcast_in_user_list(self, client):
        client.post("/notifications/broadcast", json={"title": "Hello All"})
        resp = client.get("/notifications?user_id=u1")
        data = resp.json()
        assert data["total"] == 1


# ---------------------------------------------------------------------------
# GET /notifications/stats
# ---------------------------------------------------------------------------

class TestNotificationStats:
    """Tests for GET /notifications/stats."""

    def test_empty_stats(self, client):
        resp = client.get("/notifications/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["unread"] == 0

    def test_stats_with_data(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A", "category": "system", "severity": "info"})
        client.post("/notifications", json={"user_id": "u1", "title": "B", "category": "security", "severity": "error"})
        client.post("/notifications/broadcast", json={"title": "C", "category": "quota_alert", "severity": "warning"})
        resp = client.get("/notifications/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["unread"] == 3
        assert data["by_category"]["system"] == 1
        assert data["by_category"]["security"] == 1
        assert data["by_category"]["quota_alert"] == 1
        assert data["by_severity"]["info"] == 1
        assert data["by_severity"]["error"] == 1
        assert data["by_severity"]["warning"] == 1
        assert data["broadcasts"] == 1

    def test_stats_disabled(self, client_disabled):
        resp = client_disabled.get("/notifications/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /notifications/unread-count
# ---------------------------------------------------------------------------

class TestUnreadCount:
    """Tests for GET /notifications/unread-count."""

    def test_no_notifications(self, client):
        resp = client.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 0

    def test_with_unread(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u1", "title": "B"})
        resp = client.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 2

    def test_with_read(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u1", "title": "B"})
        client.post(f"/notifications/{r.json()['id']}/read")
        resp = client.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 1

    def test_includes_broadcast(self, client):
        client.post("/notifications/broadcast", json={"title": "Hello"})
        resp = client.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 1

    def test_disabled(self, client_disabled):
        resp = client_disabled.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 0


# ---------------------------------------------------------------------------
# POST /notifications/read-all
# ---------------------------------------------------------------------------

class TestMarkAllRead:
    """Tests for POST /notifications/read-all."""

    def test_mark_all(self, client):
        client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u1", "title": "B"})
        client.post("/notifications/broadcast", json={"title": "C"})
        resp = client.post("/notifications/read-all", json={"user_id": "u1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["marked_read"] == 3

    def test_mark_all_already_read(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        client.post("/notifications", json={"user_id": "u1", "title": "B"})
        client.post(f"/notifications/{r.json()['id']}/read")
        resp = client.post("/notifications/read-all", json={"user_id": "u1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["marked_read"] == 1

    def test_disabled(self, client_disabled):
        resp = client_disabled.post("/notifications/read-all", json={"user_id": "u1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["marked_read"] == 0


# ---------------------------------------------------------------------------
# GET /notifications/{id}
# ---------------------------------------------------------------------------

class TestGetNotification:
    """Tests for GET /notifications/{id}."""

    def test_get_existing(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        resp = client.get(f"/notifications/{notif_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == notif_id
        assert data["title"] == "A"

    def test_get_not_found(self, client):
        resp = client.get("/notifications/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /notifications/{id}
# ---------------------------------------------------------------------------

class TestUpdateNotification:
    """Tests for PATCH /notifications/{id}."""

    def test_update_title(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        resp = client.patch(f"/notifications/{notif_id}", json={"title": "Updated"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"

    def test_update_severity(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        resp = client.patch(f"/notifications/{notif_id}", json={"severity": "critical"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] == "critical"

    def test_update_not_found(self, client):
        resp = client.patch("/notifications/nonexistent", json={"title": "X"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /notifications/{id}/read
# ---------------------------------------------------------------------------

class TestMarkRead:
    """Tests for POST /notifications/{id}/read."""

    def test_mark_read(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        resp = client.post(f"/notifications/{notif_id}/read")
        assert resp.status_code == 200
        data = resp.json()
        assert data["read"] is True
        assert data["read_at"] != ""

    def test_mark_read_not_found(self, client):
        resp = client.post("/notifications/nonexistent/read")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /notifications/{id}/unread
# ---------------------------------------------------------------------------

class TestMarkUnread:
    """Tests for POST /notifications/{id}/unread."""

    def test_mark_unread(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        client.post(f"/notifications/{notif_id}/read")
        resp = client.post(f"/notifications/{notif_id}/unread")
        assert resp.status_code == 200
        data = resp.json()
        assert data["read"] is False
        assert data["read_at"] == ""

    def test_mark_unread_not_found(self, client):
        resp = client.post("/notifications/nonexistent/unread")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /notifications/{id}
# ---------------------------------------------------------------------------

class TestDeleteNotification:
    """Tests for DELETE /notifications/{id}."""

    def test_delete(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        resp = client.delete(f"/notifications/{notif_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    def test_delete_not_found(self, client):
        resp = client.delete("/notifications/nonexistent")
        assert resp.status_code == 404

    def test_deleted_not_in_list(self, client):
        r = client.post("/notifications", json={"user_id": "u1", "title": "A"})
        notif_id = r.json()["id"]
        client.delete(f"/notifications/{notif_id}")
        resp = client.get(f"/notifications/{notif_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Route ordering tests
# ---------------------------------------------------------------------------

class TestRouteOrdering:
    """Tests to ensure specific routes don't get captured by {id} parameter."""

    def test_stats_not_captured(self, client):
        resp = client.get("/notifications/stats")
        assert resp.status_code == 200

    def test_unread_count_not_captured(self, client):
        resp = client.get("/notifications/unread-count?user_id=u1")
        assert resp.status_code == 200

    def test_broadcast_not_captured(self, client):
        resp = client.post("/notifications/broadcast", json={"title": "A"})
        assert resp.status_code == 200

    def test_read_all_not_captured(self, client):
        resp = client.post("/notifications/read-all", json={"user_id": "u1"})
        assert resp.status_code == 200
