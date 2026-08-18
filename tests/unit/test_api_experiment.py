"""Tests for experiment API endpoints — covers create, list, get, assign, record, stats, delete."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import create_app, reset_runtime
from agentbase.bootstrap import RuntimeContext
from agentbase.config.schema import AgentConfig, AppConfig, ExperimentConfig


@pytest.fixture
def mock_runtime(tmp_path):
    """Create a mock RuntimeContext with experiment manager enabled."""
    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"

    # Enable experiments
    app_config.experiment = ExperimentConfig(enabled=True, provider="memory")

    # Disable rate limiting for tests (otherwise full-suite runs hit 429)
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


class TestExperimentAPI:
    def test_list_empty(self, client):
        resp = client.get("/experiments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["enabled"] is True

    def test_create_and_get(self, client):
        # Create
        resp = client.post("/experiments", json={
            "name": "api_test",
            "description": "API test experiment",
            "strategy": "round_robin",
            "variants": [
                {"name": "control", "weight": 1},
                {"name": "treatment", "weight": 1, "model_override": {"name": "gpt-4.1"}},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "api_test"
        assert len(data["variants"]) == 2

        # Get
        resp = client.get("/experiments/api_test")
        assert resp.status_code == 200
        assert resp.json()["name"] == "api_test"

    def test_get_nonexistent(self, client):
        resp = client.get("/experiments/nonexistent")
        assert resp.status_code == 404

    def test_create_no_variants(self, client):
        resp = client.post("/experiments", json={
            "name": "bad",
            "variants": [],
        })
        assert resp.status_code == 400

    def test_assign(self, client):
        client.post("/experiments", json={
            "name": "assign_test",
            "variants": [{"name": "a"}, {"name": "b"}],
        })
        resp = client.post("/experiments/assign_test/assign", json={"request_id": "req-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["variant_name"] in ("a", "b")
        assert data["request_id"] == "req-1"

    def test_assign_no_body(self, client):
        client.post("/experiments", json={
            "name": "assign_nobody",
            "variants": [{"name": "a"}],
        })
        resp = client.post("/experiments/assign_nobody/assign")
        assert resp.status_code == 200
        assert resp.json()["variant_name"] == "a"

    def test_record_result(self, client):
        client.post("/experiments", json={
            "name": "result_test",
            "variants": [{"name": "a"}],
        })
        resp = client.post("/experiments/result_test/results", json={
            "variant_name": "a",
            "success": True,
            "duration_ms": 123.4,
            "output_text": "hello",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["variant_name"] == "a"
        assert data["success"] is True
        assert data["duration_ms"] == 123.4
        assert data["id"] is not None

    def test_stats(self, client):
        client.post("/experiments", json={
            "name": "stats_test",
            "variants": [{"name": "a"}, {"name": "b"}],
        })
        # Record some results
        client.post("/experiments/stats_test/results", json={
            "variant_name": "a", "success": True, "duration_ms": 100.0,
        })
        client.post("/experiments/stats_test/results", json={
            "variant_name": "a", "success": False, "duration_ms": 50.0,
        })
        client.post("/experiments/stats_test/results", json={
            "variant_name": "b", "success": True, "duration_ms": 200.0,
        })

        resp = client.get("/experiments/stats_test/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_results"] == 3
        a_stats = next(v for v in data["variant_stats"] if v["variant_name"] == "a")
        assert a_stats["total"] == 2
        assert a_stats["successes"] == 1

    def test_delete(self, client):
        client.post("/experiments", json={
            "name": "delete_test",
            "variants": [{"name": "a"}],
        })
        resp = client.delete("/experiments/delete_test")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        resp = client.get("/experiments/delete_test")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client):
        resp = client.delete("/experiments/nonexistent")
        assert resp.status_code == 404

    def test_list_after_create(self, client):
        client.post("/experiments", json={
            "name": "list1",
            "variants": [{"name": "a"}],
        })
        client.post("/experiments", json={
            "name": "list2",
            "variants": [{"name": "a"}],
        })
        resp = client.get("/experiments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

    def test_create_duplicate_returns_error(self, client):
        client.post("/experiments", json={
            "name": "dup",
            "variants": [{"name": "a"}],
        })
        resp = client.post("/experiments", json={
            "name": "dup",
            "variants": [{"name": "a"}],
        })
        # Should return 500 (RegistryError) due to global exception handler
        assert resp.status_code >= 400
