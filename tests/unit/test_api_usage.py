"""Tests for usage tracking API endpoints.

Covers:
- GET /usage/stats — aggregated statistics (filterable)
- GET /usage/records — paginated list (filterable)
- GET /usage/summary — high-level totals
- DELETE /usage/records — clear all records
- Disabled manager returns empty/zero values
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    _reset_usage_manager,
    create_app,
    reset_runtime,
)
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with usage tracking enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.usage.enabled = True
    app_config.usage.provider = "memory"

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
    """Client with usage tracking enabled, no auth."""
    reset_runtime()
    _reset_usage_manager()
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
        _reset_usage_manager()


@pytest.fixture
def client_disabled(tmp_path):
    """Client with usage tracking disabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.rate_limit.enabled = False
    app_config.usage.enabled = False

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)

    fake_agent = MagicMock()
    runtime.get_agent = lambda name=None: fake_agent
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: AgentConfig(
        name=name or "default",
        description="Test agent",
    )

    reset_runtime()
    _reset_usage_manager()
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
        _reset_usage_manager()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _seed_records(client):
    """Seed some usage records by directly accessing the manager."""
    from agentbase.api import _get_usage_manager
    mgr = _get_usage_manager()
    mgr.record(
        agent="default",
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        thread_id="t1",
        user="user1",
        duration_ms=100,
    )
    mgr.record(
        agent="default",
        model="gpt-4",
        prompt_tokens=200,
        completion_tokens=100,
        thread_id="t2",
        user="user2",
        duration_ms=200,
    )
    mgr.record(
        agent="researcher",
        model="gpt-4o-mini",
        prompt_tokens=150,
        completion_tokens=75,
        thread_id="t3",
        user="user1",
        duration_ms=150,
    )


# ---------------------------------------------------------------------------
# GET /usage/summary
# ---------------------------------------------------------------------------

class TestUsageSummary:
    """Test GET /usage/summary."""

    def test_empty_summary(self, client):
        resp = client.get("/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["total_calls"] == 0
        assert data["total_prompt_tokens"] == 0
        assert data["total_completion_tokens"] == 0
        assert data["total_tokens"] == 0
        assert data["total_cost_usd"] == 0

    def test_with_records(self, client):
        _seed_records(client)
        resp = client.get("/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 3
        assert data["total_prompt_tokens"] == 450
        assert data["total_completion_tokens"] == 225
        assert data["total_tokens"] == 675
        assert data["total_cost_usd"] > 0

    def test_disabled_returns_zeros(self, client_disabled):
        resp = client_disabled.get("/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["total_calls"] == 0


# ---------------------------------------------------------------------------
# GET /usage/stats
# ---------------------------------------------------------------------------

class TestUsageStats:
    """Test GET /usage/stats."""

    def test_empty_stats(self, client):
        resp = client.get("/usage/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 0
        assert data["total_prompt_tokens"] == 0
        assert "by_model" in data
        assert "by_agent" in data
        assert "by_user" in data

    def test_stats_with_records(self, client):
        _seed_records(client)
        resp = client.get("/usage/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 3
        assert data["total_prompt_tokens"] == 450
        assert "gpt-4o-mini" in data["by_model"]
        assert "gpt-4" in data["by_model"]
        assert "default" in data["by_agent"]
        assert "researcher" in data["by_agent"]

    def test_filter_by_agent(self, client):
        _seed_records(client)
        resp = client.get("/usage/stats?agent=default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 2
        assert all(a == "default" for a in data["by_agent"])

    def test_filter_by_model(self, client):
        _seed_records(client)
        resp = client.get("/usage/stats?model=gpt-4o-mini")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 2
        assert all(m == "gpt-4o-mini" for m in data["by_model"])

    def test_filter_by_user(self, client):
        _seed_records(client)
        resp = client.get("/usage/stats?user=user1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 2
        assert "user1" in data["by_user"]

    def test_disabled_returns_empty(self, client_disabled):
        resp = client_disabled.get("/usage/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 0


# ---------------------------------------------------------------------------
# GET /usage/records
# ---------------------------------------------------------------------------

class TestUsageRecords:
    """Test GET /usage/records."""

    def test_empty_records(self, client):
        resp = client.get("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_list_records(self, client):
        _seed_records(client)
        resp = client.get("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["total"] == 3

    def test_pagination(self, client):
        _seed_records(client)
        # Also add more records
        from agentbase.api import _get_usage_manager
        mgr = _get_usage_manager()
        for i in range(10):
            mgr.record(agent="agent", model="m", prompt_tokens=1, completion_tokens=1)

        resp = client.get("/usage/records?page=1&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 5
        assert data["total"] == 13
        assert data["page"] == 1
        assert data["page_size"] == 5

        resp = client.get("/usage/records?page=2&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 5
        assert data["page"] == 2

    def test_filter_by_agent(self, client):
        _seed_records(client)
        resp = client.get("/usage/records?agent=researcher")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["agent"] == "researcher"

    def test_filter_by_model(self, client):
        _seed_records(client)
        resp = client.get("/usage/records?model=gpt-4")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["model"] == "gpt-4"

    def test_filter_by_thread_id(self, client):
        _seed_records(client)
        resp = client.get("/usage/records?thread_id=t2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["thread_id"] == "t2"

    def test_records_newest_first(self, client):
        from agentbase.api import _get_usage_manager
        mgr = _get_usage_manager()
        mgr.record(agent="first", model="m", prompt_tokens=1, completion_tokens=1)
        mgr.record(agent="second", model="m", prompt_tokens=1, completion_tokens=1)

        resp = client.get("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"][0]["agent"] == "second"  # newest first

    def test_disabled_returns_empty(self, client_disabled):
        resp = client_disabled.get("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# DELETE /usage/records
# ---------------------------------------------------------------------------

class TestClearUsageRecords:
    """Test DELETE /usage/records."""

    def test_clear(self, client):
        _seed_records(client)
        resp = client.delete("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 3

        # Verify records are gone
        resp = client.get("/usage/records")
        assert resp.json()["total"] == 0

    def test_clear_empty(self, client):
        resp = client.delete("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 0

    def test_disabled_returns_zero(self, client_disabled):
        resp = client_disabled.delete("/usage/records")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 0
