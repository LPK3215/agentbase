"""Tests for API key management API endpoints — CRUD, verify, revoke.

Covers:
- POST /apikeys — create key (returns raw key)
- GET /apikeys — list keys
- GET /apikeys/{key_id} — get key detail
- PATCH /apikeys/{key_id} — update key
- DELETE /apikeys/{key_id} — delete key
- POST /apikeys/{key_id}/revoke — revoke key
- POST /apikeys/verify — verify key
- Disabled manager returns 503
- Key hash never exposed in responses
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_apikey_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with API key manager enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.apikey_manager.enabled = True
    app_config.apikey_manager.provider = "memory"

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
    """Client with API key manager enabled, no global API key."""
    reset_runtime()
    _reset_apikey_manager()
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
        _reset_apikey_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with API key manager disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.apikey_manager.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]

    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )

    reset_runtime()
    _reset_apikey_manager()
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
        _reset_apikey_manager()


# ---------------------------------------------------------------------------
# POST /apikeys — create
# ---------------------------------------------------------------------------

class TestCreateApiKey:
    def test_create_returns_entry_and_raw_key(self, client):
        resp = client.post("/apikeys", json={
            "name": "test-key",
            "roles": ["user"],
            "description": "Test key",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-key"
        assert data["roles"] == ["user"]
        assert data["description"] == "Test key"
        assert data["raw_key"].startswith("agk_")
        assert data["key_prefix"] == data["raw_key"][:12]
        assert "key_hash" not in data

    def test_create_anonymous_key(self, client):
        resp = client.post("/apikeys", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["raw_key"].startswith("agk_")

    def test_create_with_expires_at(self, client):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        resp = client.post("/apikeys", json={
            "name": "temp-key",
            "expires_at": future,
        })
        assert resp.status_code == 200
        assert resp.json()["expires_at"] == future

    def test_create_duplicate_name_returns_400(self, client):
        client.post("/apikeys", json={"name": "dup"})
        resp = client.post("/apikeys", json={"name": "dup"})
        assert resp.status_code == 400

    def test_create_with_admin_role(self, client):
        resp = client.post("/apikeys", json={
            "name": "admin-key",
            "roles": ["admin"],
        })
        assert resp.status_code == 200
        assert resp.json()["roles"] == ["admin"]


# ---------------------------------------------------------------------------
# GET /apikeys — list
# ---------------------------------------------------------------------------

class TestListApiKeys:
    def test_list_empty(self, client):
        resp = client.get("/apikeys")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_keys(self, client):
        client.post("/apikeys", json={"name": "key1"})
        client.post("/apikeys", json={"name": "key2"})
        resp = client.get("/apikeys")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_list_excludes_hash(self, client):
        client.post("/apikeys", json={"name": "test"})
        resp = client.get("/apikeys")
        for item in resp.json()["items"]:
            assert "key_hash" not in item
            assert "raw_key" not in item

    def test_list_disabled_returns_empty(self, client_disabled):
        resp = client_disabled.get("/apikeys")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False


# ---------------------------------------------------------------------------
# GET /apikeys/{key_id} — get detail
# ---------------------------------------------------------------------------

class TestGetApiKey:
    def test_get_existing(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        resp = client.get(f"/apikeys/{key_id}")
        assert resp.status_code == 200
        assert resp.json()["key_id"] == key_id
        assert "key_hash" not in resp.json()

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/apikeys/nonexistent")
        assert resp.status_code == 404

    def test_get_disabled_returns_503(self, client_disabled):
        resp = client_disabled.get("/apikeys/test")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /apikeys/{key_id} — update
# ---------------------------------------------------------------------------

class TestUpdateApiKey:
    def test_update_description(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        resp = client.patch(f"/apikeys/{key_id}", json={"description": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated"

    def test_update_roles(self, client):
        create_resp = client.post("/apikeys", json={"name": "test", "roles": ["user"]})
        key_id = create_resp.json()["key_id"]
        resp = client.patch(f"/apikeys/{key_id}", json={"roles": ["admin"]})
        assert resp.status_code == 200
        assert resp.json()["roles"] == ["admin"]

    def test_update_nonexistent_returns_404(self, client):
        resp = client.patch("/apikeys/nonexistent", json={"description": "test"})
        assert resp.status_code == 404

    def test_update_no_fields_returns_400(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        resp = client.patch(f"/apikeys/{key_id}", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /apikeys/{key_id} — delete
# ---------------------------------------------------------------------------

class TestDeleteApiKey:
    def test_delete_existing(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        resp = client.delete(f"/apikeys/{key_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # Verify it's gone
        get_resp = client.get(f"/apikeys/{key_id}")
        assert get_resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/apikeys/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /apikeys/{key_id}/revoke — revoke
# ---------------------------------------------------------------------------

class TestRevokeApiKey:
    def test_revoke_existing(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        raw_key = create_resp.json()["raw_key"]
        resp = client.post(f"/apikeys/{key_id}/revoke")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True
        # Verify key no longer works
        verify_resp = client.post("/apikeys/verify", json={"key": raw_key})
        assert verify_resp.json()["valid"] is False

    def test_revoke_nonexistent_returns_404(self, client):
        resp = client.post("/apikeys/nonexistent/revoke")
        assert resp.status_code == 404

    def test_revoked_key_shows_disabled(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        key_id = create_resp.json()["key_id"]
        client.post(f"/apikeys/{key_id}/revoke")
        get_resp = client.get(f"/apikeys/{key_id}")
        assert get_resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# POST /apikeys/verify — verify
# ---------------------------------------------------------------------------

class TestVerifyApiKey:
    def test_verify_valid_key(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        raw_key = create_resp.json()["raw_key"]
        resp = client.post("/apikeys/verify", json={"key": raw_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["key"]["name"] == "test"

    def test_verify_invalid_key(self, client):
        resp = client.post("/apikeys/verify", json={"key": "agk_invalid"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_verify_revoked_key(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        raw_key = create_resp.json()["raw_key"]
        key_id = create_resp.json()["key_id"]
        client.post(f"/apikeys/{key_id}/revoke")
        resp = client.post("/apikeys/verify", json={"key": raw_key})
        assert resp.json()["valid"] is False

    def test_verify_empty_key(self, client):
        resp = client.post("/apikeys/verify", json={"key": ""})
        assert resp.json()["valid"] is False

    def test_verify_updates_call_count(self, client):
        create_resp = client.post("/apikeys", json={"name": "test"})
        raw_key = create_resp.json()["raw_key"]
        key_id = create_resp.json()["key_id"]
        client.post("/apikeys/verify", json={"key": raw_key})
        client.post("/apikeys/verify", json={"key": raw_key})
        get_resp = client.get(f"/apikeys/{key_id}")
        assert get_resp.json()["call_count"] == 2
        assert get_resp.json()["last_used_at"] != ""
