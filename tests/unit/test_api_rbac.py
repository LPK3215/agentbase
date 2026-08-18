"""Tests for the RBAC API endpoints.

Covers:
- GET /rbac/roles — list (system + custom)
- POST /rbac/roles — create (400 on invalid input)
- GET /rbac/roles/stats — aggregate statistics
- GET/PATCH/DELETE /rbac/roles/{name} — detail / update / delete (404, system protected)
- GET /rbac/roles/{name}/users — assigned users
- POST/DELETE /rbac/users/{username}/roles/{role_name} — assign / revoke
- GET /rbac/users/{username}/roles — user roles + effective permissions
- POST /rbac/check — permission check
- Disabled manager returns empty values, denies checks
- Route ordering: /stats not captured by /{name}
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import _reset_rbac_manager, create_app, reset_runtime
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Runtime with the RBAC service enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.rbac.enabled = True
    app_config.rbac.provider = "memory"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    return runtime


def _make_client(runtime):
    reset_runtime()
    _reset_rbac_manager()
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
        _reset_rbac_manager()


@pytest.fixture
def client(mock_runtime):
    yield from _make_client(mock_runtime)


@pytest.fixture
def client_disabled(tmp_path):
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    # rbac.enabled defaults to False
    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    yield from _make_client(runtime)


def _create_role(client, name="editor", permissions=("agents:invoke",)) -> dict:
    resp = client.post("/rbac/roles", json={"name": name, "permissions": list(permissions)})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role list / create / stats
# ---------------------------------------------------------------------------


def test_list_roles_includes_system(client):
    body = client.get("/rbac/roles").json()
    names = [r["name"] for r in body["items"]]
    assert names == ["admin", "readonly", "user"]
    assert body["total"] == 3


def test_create_role(client):
    body = _create_role(client, "editor", ("agents:invoke", "kb:write"))
    assert body["name"] == "editor"
    assert body["permissions"] == ["agents:invoke", "kb:write"]
    assert body["is_system"] is False


def test_create_role_duplicate_400(client):
    _create_role(client, "editor")
    resp = client.post("/rbac/roles", json={"name": "editor", "permissions": ["a:b"]})
    assert resp.status_code == 400


def test_create_role_missing_fields_400(client):
    assert client.post("/rbac/roles", json={"permissions": ["a:b"]}).status_code == 400
    assert client.post("/rbac/roles", json={"name": "x"}).status_code == 400
    assert client.post("/rbac/roles", json={}).status_code == 400


def test_create_role_invalid_permission_400(client):
    resp = client.post("/rbac/roles", json={"name": "x", "permissions": ["garbage"]})
    assert resp.status_code == 400


def test_stats(client):
    _create_role(client, "editor")
    client.post("/rbac/users/alice/roles/editor")
    body = client.get("/rbac/roles/stats").json()
    assert body["total_roles"] == 4
    assert body["system_roles"] == 3
    assert body["custom_roles"] == 1
    assert body["assigned_users"] == 1
    assert body["total_assignments"] == 1


# ---------------------------------------------------------------------------
# Role detail / update / delete
# ---------------------------------------------------------------------------


def test_get_role(client):
    _create_role(client, "editor")
    body = client.get("/rbac/roles/editor").json()
    assert body["name"] == "editor"


def test_get_role_missing_404(client):
    assert client.get("/rbac/roles/nope").status_code == 404


def test_update_role(client):
    _create_role(client, "editor", ("a:b",))
    resp = client.patch("/rbac/roles/editor", json={"permissions": ["c:d"], "description": "upd"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["permissions"] == ["c:d"]
    assert body["description"] == "upd"


def test_update_role_missing_404(client):
    resp = client.patch("/rbac/roles/nope", json={"permissions": ["c:d"]})
    assert resp.status_code == 404


def test_update_role_invalid_permissions_400(client):
    _create_role(client, "editor")
    resp = client.patch("/rbac/roles/editor", json={"permissions": []})
    assert resp.status_code == 400


def test_delete_role(client):
    _create_role(client, "editor")
    assert client.delete("/rbac/roles/editor").status_code == 200
    assert client.get("/rbac/roles/editor").status_code == 404
    assert client.delete("/rbac/roles/editor").status_code == 404


def test_delete_system_role_400(client):
    resp = client.delete("/rbac/roles/admin")
    assert resp.status_code == 400
    assert "system role" in resp.json()["detail"]


def test_role_users(client):
    _create_role(client, "editor")
    client.post("/rbac/users/alice/roles/editor")
    client.post("/rbac/users/bob/roles/editor")
    body = client.get("/rbac/roles/editor/users").json()
    assert body["items"] == ["alice", "bob"]
    assert body["total"] == 2


def test_role_users_missing_role_404(client):
    assert client.get("/rbac/roles/nope/users").status_code == 404


# ---------------------------------------------------------------------------
# User assignment
# ---------------------------------------------------------------------------


def test_assign_and_revoke(client):
    _create_role(client, "editor")
    resp = client.post("/rbac/users/alice/roles/editor")
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["editor"]

    resp = client.delete("/rbac/users/alice/roles/editor")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    # revoking again → 404
    assert client.delete("/rbac/users/alice/roles/editor").status_code == 404


def test_assign_unknown_role_400(client):
    assert client.post("/rbac/users/alice/roles/nope").status_code == 400


def test_user_roles_with_permissions(client):
    _create_role(client, "editor", ("agents:invoke", "kb:write"))
    client.post("/rbac/users/alice/roles/editor")
    client.post("/rbac/users/alice/roles/readonly")
    body = client.get("/rbac/users/alice/roles").json()
    assert body["roles"] == ["editor", "readonly"]
    assert body["permissions"] == ["*:read", "agents:invoke", "kb:write"]


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------


def test_check_permission(client):
    _create_role(client, "editor", ("agents:invoke",))
    client.post("/rbac/users/alice/roles/editor")
    ok = client.post("/rbac/check", json={"username": "alice", "resource": "agents", "action": "invoke"})
    assert ok.json()["allowed"] is True
    no = client.post("/rbac/check", json={"username": "alice", "resource": "users", "action": "delete"})
    assert no.json()["allowed"] is False


def test_check_permission_admin_wildcard(client):
    client.post("/rbac/users/root/roles/admin")
    resp = client.post("/rbac/check", json={"username": "root", "resource": "x", "action": "y"})
    assert resp.json()["allowed"] is True


def test_check_permission_validation_400(client):
    assert client.post("/rbac/check", json={"username": "a"}).status_code == 400
    assert client.post("/rbac/check", json={}).status_code == 400


# ---------------------------------------------------------------------------
# Disabled service
# ---------------------------------------------------------------------------


def test_disabled_empty_and_deny(client_disabled):
    assert client_disabled.get("/rbac/roles").json()["items"] == []
    assert client_disabled.get("/rbac/roles/stats").json()["total_roles"] == 0
    resp = client_disabled.post("/rbac/check", json={"username": "a", "resource": "b", "action": "c"})
    assert resp.json()["allowed"] is False


# ---------------------------------------------------------------------------
# Route ordering
# ---------------------------------------------------------------------------


def test_stats_not_captured_by_role_name(client):
    _create_role(client, "stats")
    # /rbac/roles/stats must hit the stats handler even though a role named "stats" exists
    body = client.get("/rbac/roles/stats").json()
    assert "total_roles" in body
