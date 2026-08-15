"""Tests for webhook management API endpoints.

Covers:
- GET /webhooks — list endpoints (all / active_only)
- POST /webhooks — register endpoint
- GET /webhooks/{endpoint_id} — get endpoint detail
- PATCH /webhooks/{endpoint_id} — update endpoint
- DELETE /webhooks/{endpoint_id} — delete endpoint
- POST /webhooks/{endpoint_id}/test — send test event
- GET /webhooks/deliveries — list delivery records
- GET /webhooks/stats — aggregate statistics
- Disabled manager returns empty/zero values
- Invalid URL validation (400)
- 404 for non-existent endpoints
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_webhook_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with webhook enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.webhook.enabled = True
    app_config.webhook.provider = "memory"

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
    """Client with webhook enabled, no auth."""
    reset_runtime()
    _reset_webhook_manager()
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
        _reset_webhook_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with webhook disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.webhook.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )
    reset_runtime()
    _reset_webhook_manager()
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
        _reset_webhook_manager()


# ---------------------------------------------------------------------------
# GET /webhooks
# ---------------------------------------------------------------------------

class TestListWebhooks:
    """Test GET /webhooks."""

    def test_list_empty(self, client):
        resp = client.get("/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_after_create(self, client):
        client.post("/webhooks", json={"url": "https://example.com/hook"})
        resp = client.get("/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["url"] == "https://example.com/hook"

    def test_list_active_only(self, client):
        client.post("/webhooks", json={
            "url": "https://example.com/hook1", "active": True
        })
        client.post("/webhooks", json={
            "url": "https://example.com/hook2", "active": False
        })
        resp = client.get("/webhooks?active_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["url"] == "https://example.com/hook1"

    def test_list_disabled(self, client_disabled):
        resp = client_disabled.get("/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# POST /webhooks
# ---------------------------------------------------------------------------

class TestCreateWebhook:
    """Test POST /webhooks."""

    def test_create_basic(self, client):
        resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == ["*"]
        assert data["active"] is True
        assert data["id"] != ""

    def test_create_with_events(self, client):
        resp = client.post("/webhooks", json={
            "url": "https://example.com/hook",
            "events": ["agent.invoke.completed", "agent.stream.completed"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 2

    def test_create_with_secret(self, client):
        resp = client.post("/webhooks", json={
            "url": "https://example.com/hook",
            "secret": "my-signing-secret",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["secret"] == "***"

    def test_create_with_description(self, client):
        resp = client.post("/webhooks", json={
            "url": "https://example.com/hook",
            "description": "Production webhook",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Production webhook"

    def test_create_invalid_url(self, client):
        resp = client.post("/webhooks", json={"url": "ftp://bad-url"})
        assert resp.status_code == 400

    def test_create_no_scheme(self, client):
        resp = client.post("/webhooks", json={"url": "not-a-url"})
        assert resp.status_code == 400

    def test_create_http_url(self, client):
        resp = client.post("/webhooks", json={"url": "http://localhost:9090/hook"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "http://localhost:9090/hook"


# ---------------------------------------------------------------------------
# GET /webhooks/{endpoint_id}
# ---------------------------------------------------------------------------

class TestGetWebhook:
    """Test GET /webhooks/{endpoint_id}."""

    def test_get_existing(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.get(f"/webhooks/{endpoint_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com/hook"

    def test_get_not_found(self, client):
        resp = client.get("/webhooks/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /webhooks/{endpoint_id}
# ---------------------------------------------------------------------------

class TestUpdateWebhook:
    """Test PATCH /webhooks/{endpoint_id}."""

    def test_update_description(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.patch(f"/webhooks/{endpoint_id}", json={"description": "Updated"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Updated"

    def test_update_active(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.patch(f"/webhooks/{endpoint_id}", json={"active": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_update_events(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.patch(f"/webhooks/{endpoint_id}", json={
            "events": ["agent.invoke.completed"]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == ["agent.invoke.completed"]

    def test_update_url(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.patch(f"/webhooks/{endpoint_id}", json={
            "url": "https://new.example.com/hook"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://new.example.com/hook"

    def test_update_not_found(self, client):
        resp = client.patch("/webhooks/nonexistent", json={"description": "test"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /webhooks/{endpoint_id}
# ---------------------------------------------------------------------------

class TestDeleteWebhook:
    """Test DELETE /webhooks/{endpoint_id}."""

    def test_delete_existing(self, client):
        create_resp = client.post("/webhooks", json={"url": "https://example.com/hook"})
        endpoint_id = create_resp.json()["id"]
        resp = client.delete(f"/webhooks/{endpoint_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        # Verify it's gone
        get_resp = client.get(f"/webhooks/{endpoint_id}")
        assert get_resp.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/webhooks/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /webhooks/{endpoint_id}/test
# ---------------------------------------------------------------------------

class TestTestWebhook:
    """Test POST /webhooks/{endpoint_id}/test."""

    def test_test_disabled(self, client_disabled):
        resp = client_disabled.post("/webhooks/any/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "disabled"

    def test_test_not_found(self, client):
        resp = client.post("/webhooks/nonexistent/test")
        assert resp.status_code == 404

    def test_test_existing_endpoint(self, client):
        """Test that test endpoint returns a delivery record.

        The delivery will fail (no real server), but we verify the
        delivery record structure is correct.
        """
        create_resp = client.post("/webhooks", json={
            "url": "https://nonexistent.example.com/hook",
        })
        endpoint_id = create_resp.json()["id"]
        resp = client.post(f"/webhooks/{endpoint_id}/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint_id"] == endpoint_id
        assert data["event"] == "webhook.test"
        assert data["status"] in ("success", "failed")
        assert data["attempts"] >= 1


# ---------------------------------------------------------------------------
# GET /webhooks/deliveries
# ---------------------------------------------------------------------------

class TestListDeliveries:
    """Test GET /webhooks/deliveries."""

    def test_list_empty(self, client):
        resp = client.get("/webhooks/deliveries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_after_test(self, client):
        """Create endpoint, test it (creates delivery), then list."""
        create_resp = client.post("/webhooks", json={
            "url": "https://nonexistent.example.com/hook",
        })
        endpoint_id = create_resp.json()["id"]
        # Trigger a test delivery
        client.post(f"/webhooks/{endpoint_id}/test")
        resp = client.get("/webhooks/deliveries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_list_with_filter(self, client):
        create_resp = client.post("/webhooks", json={
            "url": "https://nonexistent.example.com/hook",
        })
        endpoint_id = create_resp.json()["id"]
        client.post(f"/webhooks/{endpoint_id}/test")
        # Filter by endpoint_id
        resp = client.get(f"/webhooks/deliveries?endpoint_id={endpoint_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["endpoint_id"] == endpoint_id for item in data["items"])

    def test_list_disabled(self, client_disabled):
        resp = client_disabled.get("/webhooks/deliveries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /webhooks/stats
# ---------------------------------------------------------------------------

class TestWebhookStats:
    """Test GET /webhooks/stats."""

    def test_stats_empty(self, client):
        resp = client.get("/webhooks/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_endpoints"] == 0
        assert data["total_deliveries"] == 0
        assert data["success_rate"] == 0.0

    def test_stats_with_data(self, client):
        client.post("/webhooks", json={"url": "https://example.com/hook1"})
        client.post("/webhooks", json={
            "url": "https://example.com/hook2", "active": False
        })
        resp = client.get("/webhooks/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_endpoints"] == 2
        assert data["active_endpoints"] == 1

    def test_stats_disabled(self, client_disabled):
        resp = client_disabled.get("/webhooks/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_endpoints"] == 0
