"""Tests for the system config API endpoints.

Covers:
- GET /system-config — list (filters, pagination)
- GET /system-config/stats — aggregate statistics
- GET /system-config/public — public entries only
- POST /system-config/batch-get — batch value fetch
- PUT /system-config/{key} — upsert (400 on invalid key / missing value)
- GET /system-config/{key} — get detail (404)
- DELETE /system-config/{key} — delete (404)
- Disabled manager returns empty/zero values
- Route ordering: /stats, /public, /batch-get not captured by /{key}
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_system_config_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Runtime with the system config service enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.system_config.enabled = True
    app_config.system_config.provider = "memory"

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
    _reset_system_config_manager()
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
        _reset_system_config_manager()


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
    # system_config.enabled defaults to False
    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default", description="Test agent",
    )
    yield from _make_client(runtime)


def _set(client, key: str, value, **extra) -> dict:
    payload = {"value": value}
    payload.update(extra)
    resp = client.put(f"/system-config/{key}", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_empty(client):
    resp = client.get("/system-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_with_filters(client):
    _set(client, "feature.a", True, category="feature", is_public=True)
    _set(client, "feature.b", False, category="feature")
    _set(client, "quota.c", 10, category="quota")

    body = client.get("/system-config", params={"category": "feature"}).json()
    assert [i["key"] for i in body["items"]] == ["feature.a", "feature.b"]

    body = client.get("/system-config", params={"key_prefix": "quota."}).json()
    assert [i["key"] for i in body["items"]] == ["quota.c"]

    body = client.get("/system-config", params={"public_only": True}).json()
    assert [i["key"] for i in body["items"]] == ["feature.a"]


def test_list_pagination(client):
    for i in range(15):
        _set(client, f"k.{i:02d}", i)
    body = client.get("/system-config", params={"page": 3, "page_size": 5}).json()
    assert [i["key"] for i in body["items"]] == [f"k.{i:02d}" for i in range(10, 15)]
    assert body["page"] == 3


# ---------------------------------------------------------------------------
# Stats / public / batch-get
# ---------------------------------------------------------------------------


def test_stats(client):
    _set(client, "feature.a", True, category="feature", is_public=True)
    _set(client, "quota.b", 5, category="quota")
    body = client.get("/system-config/stats").json()
    assert body["total"] == 2
    assert body["public_count"] == 1
    assert body["by_category"] == {"feature": 1, "quota": 1}
    assert body["recently_updated"] == 2


def test_public_endpoint_filters_private(client):
    _set(client, "feature.a", True, is_public=True)
    _set(client, "secret.b", "s3cret")
    body = client.get("/system-config/public").json()
    assert body["total"] == 1
    assert body["items"][0]["key"] == "feature.a"
    # public listing exposes only key/value/category
    assert set(body["items"][0].keys()) == {"key", "value", "category"}


def test_batch_get(client):
    _set(client, "a.b", 1)
    _set(client, "c.d", "x")
    resp = client.post("/system-config/batch-get", json={"keys": ["a.b", "c.d", "missing.k"]})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items == {"a.b": 1, "c.d": "x", "missing.k": None}


def test_batch_get_validation(client):
    resp = client.post("/system-config/batch-get", json={})
    assert resp.status_code == 400
    resp = client.post("/system-config/batch-get", json={"keys": []})
    assert resp.status_code == 400
    resp = client.post("/system-config/batch-get", json={"keys": ["k"] * 101})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Set (PUT)
# ---------------------------------------------------------------------------


def test_set_creates_entry(client):
    body = _set(client, "feature.dark_mode", True, category="feature",
                description="Enable dark mode", is_public=True, updated_by="admin")
    assert body["key"] == "feature.dark_mode"
    assert body["value"] is True
    assert body["category"] == "feature"
    assert body["is_public"] is True
    assert body["version"] == 1


def test_set_upsert_bumps_version(client):
    _set(client, "a.b", 1)
    body = _set(client, "a.b", 2)
    assert body["version"] == 2


def test_set_missing_value_400(client):
    resp = client.put("/system-config/a.b", json={"category": "x"})
    assert resp.status_code == 400


def test_set_invalid_key_400(client):
    resp = client.put("/system-config/INVALID!KEY", json={"value": 1})
    assert resp.status_code == 400


def test_set_oversized_value_400(client):
    resp = client.put("/system-config/a.b", json={"value": "x" * 70_000})
    assert resp.status_code == 400


def test_set_accepts_complex_json(client):
    body = _set(client, "complex.cfg", {"nested": {"list": [1, 2, 3]}, "flag": None})
    assert body["value"] == {"nested": {"list": [1, 2, 3]}, "flag": None}


# ---------------------------------------------------------------------------
# Get / Delete
# ---------------------------------------------------------------------------


def test_get_entry(client):
    _set(client, "a.b", 42)
    body = client.get("/system-config/a.b").json()
    assert body["value"] == 42


def test_get_missing_404(client):
    assert client.get("/system-config/missing.key").status_code == 404


def test_delete_entry(client):
    _set(client, "a.b", 1)
    assert client.delete("/system-config/a.b").status_code == 200
    assert client.get("/system-config/a.b").status_code == 404
    assert client.delete("/system-config/a.b").status_code == 404


# ---------------------------------------------------------------------------
# Disabled service
# ---------------------------------------------------------------------------


def test_disabled_returns_empty(client_disabled):
    assert client_disabled.get("/system-config").json()["items"] == []
    assert client_disabled.get("/system-config/stats").json()["total"] == 0
    assert client_disabled.get("/system-config/public").json()["total"] == 0
    # writes silently drop, reads miss
    assert client_disabled.put("/system-config/a.b", json={"value": 1}).status_code == 200
    assert client_disabled.get("/system-config/a.b").status_code == 404


# ---------------------------------------------------------------------------
# Route ordering
# ---------------------------------------------------------------------------


def test_static_routes_not_captured_by_key(client):
    # These must resolve to their own handlers, not GET /system-config/{key}
    _set(client, "stats", 1)
    assert client.get("/system-config/stats").status_code == 200
    assert client.get("/system-config/public").status_code == 200
    assert client.post("/system-config/batch-get", json={"keys": ["stats"]}).status_code == 200
