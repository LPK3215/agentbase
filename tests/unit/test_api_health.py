"""Tests for the enhanced /health endpoint (D1: component-level health checks)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentbase.api import (
    ComponentHealth,
    HealthResponse,
    create_app,
    reset_runtime,
)
from agentbase.config.schema import AppConfig, HealthCheckConfig
from agentbase.core.queue import MemoryRequestQueue


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

@pytest.fixture
def health_runtime(tmp_path):
    """Create a RuntimeContext with controllable health_check config."""
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    # Enable all checks by default
    app_config.health_check = HealthCheckConfig(
        check_storage=True,
        check_queue=True,
        check_embedding=True,
        check_search=True,
        check_tracer=True,
    )

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: MagicMock()

    return runtime


@pytest.fixture
def health_client(health_runtime):
    """Test client with all health checks enabled."""
    reset_runtime()
    app = create_app(runtime=health_runtime)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_runtime()


@pytest.fixture
def default_client(tmp_path):
    """Test client with default HealthCheckConfig (storage + queue only)."""
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"

    runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
    runtime.list_agents = lambda: ["default"]
    runtime.get_agent_config = lambda name=None: MagicMock()

    reset_runtime()
    app = create_app(runtime=runtime)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_runtime()


# --------------------------------------------------------------------------- #
# ComponentHealth model tests                                                 #
# --------------------------------------------------------------------------- #

class TestComponentHealthModel:
    def test_component_health_creation(self):
        ch = ComponentHealth(name="storage", healthy=True, detail="ok")
        assert ch.name == "storage"
        assert ch.healthy is True
        assert ch.detail == "ok"

    def test_component_health_default_detail(self):
        ch = ComponentHealth(name="storage", healthy=False)
        assert ch.detail == ""

    def test_component_health_serialization(self):
        ch = ComponentHealth(name="queue", healthy=True, detail="ok")
        d = ch.model_dump()
        assert d == {"name": "queue", "healthy": True, "detail": "ok"}


# --------------------------------------------------------------------------- #
# HealthResponse model tests                                                  #
# --------------------------------------------------------------------------- #

class TestHealthResponseModel:
    def test_health_response_new_fields(self):
        resp = HealthResponse()
        assert resp.embedding_connected is True
        assert resp.search_connected is True
        assert resp.tracer_connected is True
        assert resp.components == []

    def test_health_response_with_components(self):
        resp = HealthResponse(
            status="degraded",
            components=[
                ComponentHealth(name="storage", healthy=True, detail="ok"),
                ComponentHealth(name="queue", healthy=False, detail="connection refused"),
            ],
        )
        assert resp.status == "degraded"
        assert len(resp.components) == 2
        assert resp.components[0].name == "storage"
        assert resp.components[1].healthy is False


# --------------------------------------------------------------------------- #
# Default config (backward compatibility)                                     #
# --------------------------------------------------------------------------- #

class TestHealthDefaultConfig:
    def test_default_config_only_checks_storage_and_queue(self, default_client):
        """Default HealthCheckConfig should only check storage + queue."""
        response = default_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # Only storage and queue should appear in components
        component_names = [c["name"] for c in data["components"]]
        assert "storage" in component_names
        assert "queue" in component_names
        assert "embedding" not in component_names
        assert "search" not in component_names
        assert "tracer" not in component_names

    def test_default_config_embedding_search_pass(self, default_client):
        """When embedding/search checks are disabled, their connected flags default True."""
        response = default_client.get("/health")
        data = response.json()
        assert data["embedding_connected"] is True
        assert data["search_connected"] is True
        assert data["tracer_connected"] is True


# --------------------------------------------------------------------------- #
# All checks enabled — normal path                                            #
# --------------------------------------------------------------------------- #

class TestHealthAllChecksNormal:
    def test_all_components_healthy(self, health_client):
        """All components healthy → status=ok, 200."""
        response = health_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["storage_connected"] is True
        assert data["queue_connected"] is True
        assert data["embedding_connected"] is True
        assert data["search_connected"] is True
        assert data["tracer_connected"] is True

    def test_components_list_complete(self, health_client):
        """All 5 components should be present when all checks enabled."""
        response = health_client.get("/health")
        data = response.json()
        names = {c["name"] for c in data["components"]}
        assert names == {"storage", "queue", "embedding", "search", "tracer"}

    def test_each_component_has_required_fields(self, health_client):
        response = health_client.get("/health")
        data = response.json()
        for comp in data["components"]:
            assert "name" in comp
            assert "healthy" in comp
            assert "detail" in comp
            assert isinstance(comp["healthy"], bool)
            assert isinstance(comp["detail"], str)

    def test_response_has_version_and_agents(self, health_client):
        response = health_client.get("/health")
        data = response.json()
        assert "version" in data
        assert data["version"] != ""
        assert "agents" in data
        assert "default_agent" in data
        assert data["default_agent"] == "default"


# --------------------------------------------------------------------------- #
# Storage failure                                                             #
# --------------------------------------------------------------------------- #

class TestHealthStorageFailure:
    def test_storage_failure_degrades(self, health_runtime, health_client):
        """Storage failure → status=degraded, storage_connected=False."""
        # Inject a failing storage
        mock_storage = MagicMock()
        mock_storage.health_check.return_value = False
        health_runtime.factory._storage = mock_storage

        response = health_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["storage_connected"] is False

        storage_comp = next(c for c in data["components"] if c["name"] == "storage")
        assert storage_comp["healthy"] is False

    def test_storage_exception_caught(self, health_runtime, health_client):
        """Storage raising exception → caught, healthy=False."""
        mock_storage = MagicMock()
        mock_storage.health_check.side_effect = RuntimeError("DB down")
        health_runtime.factory._storage = mock_storage

        response = health_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["storage_connected"] is False
        storage_comp = next(c for c in data["components"] if c["name"] == "storage")
        assert "DB down" in storage_comp["detail"]

    def test_storage_no_health_check_method(self, health_runtime, health_client):
        """Storage without health_check() → treated as healthy (access succeeds)."""
        mock_storage = MagicMock(spec=[])  # no methods at all
        health_runtime.factory._storage = mock_storage

        response = health_client.get("/health")
        data = response.json()
        assert data["storage_connected"] is True


# --------------------------------------------------------------------------- #
# Queue failure                                                               #
# --------------------------------------------------------------------------- #

class TestHealthQueueFailure:
    def test_queue_failure_degrades(self, health_runtime, health_client):
        """Queue failure → status=degraded, queue_connected=False."""
        mock_queue = MagicMock(spec=MemoryRequestQueue)
        mock_queue.stats.side_effect = RuntimeError("queue crashed")
        health_runtime.factory._queue = mock_queue

        response = health_client.get("/health")
        data = response.json()
        assert data["status"] == "degraded"
        assert data["queue_connected"] is False

    def test_queue_none_is_healthy(self, health_runtime, health_client):
        """Queue=None (not configured) → healthy."""
        health_runtime.factory._queue = None

        response = health_client.get("/health")
        data = response.json()
        assert data["queue_connected"] is True

    def test_memory_queue_stats_probe(self, health_runtime, health_client):
        """MemoryRequestQueue → stats() is used as liveness probe."""
        health_runtime.factory._queue = MemoryRequestQueue()

        response = health_client.get("/health")
        data = response.json()
        assert data["queue_connected"] is True
        queue_comp = next(c for c in data["components"] if c["name"] == "queue")
        assert queue_comp["healthy"] is True


# --------------------------------------------------------------------------- #
# Embedding check                                                             #
# --------------------------------------------------------------------------- #

class TestHealthEmbeddingCheck:
    def test_embedding_not_configured_is_healthy(self, health_runtime, health_client):
        """Embedding provider='none' → healthy."""
        health_runtime.app_config.embedding.provider = "none"

        response = health_client.get("/health")
        data = response.json()
        assert data["embedding_connected"] is True
        emb_comp = next(c for c in data["components"] if c["name"] == "embedding")
        assert "not configured" in emb_comp["detail"]

    def test_embedding_not_registered_fails(self, health_runtime, health_client):
        """Embedding provider configured but not registered → unhealthy."""
        health_runtime.app_config.embedding.provider = "nonexistent-embedding"

        response = health_client.get("/health")
        data = response.json()
        assert data["embedding_connected"] is False
        emb_comp = next(c for c in data["components"] if c["name"] == "embedding")
        assert "not registered" in emb_comp["detail"]

    def test_embedding_hash_provider_healthy(self, health_runtime, health_client):
        """HashEmbedding provider → dimension probe succeeds."""
        health_runtime.app_config.embedding.provider = "hash"

        response = health_client.get("/health")
        data = response.json()
        assert data["embedding_connected"] is True
        emb_comp = next(c for c in data["components"] if c["name"] == "embedding")
        assert "hash" in emb_comp["detail"]


# --------------------------------------------------------------------------- #
# Search check                                                                #
# --------------------------------------------------------------------------- #

class TestHealthSearchCheck:
    def test_search_not_configured_is_healthy(self, health_runtime, health_client):
        """Search provider='none' → healthy."""
        health_runtime.app_config.web_search.provider = "none"

        response = health_client.get("/health")
        data = response.json()
        assert data["search_connected"] is True

    def test_search_not_registered_fails(self, health_runtime, health_client):
        """Search provider configured but not registered → unhealthy."""
        health_runtime.app_config.web_search.provider = "nonexistent-search"

        response = health_client.get("/health")
        data = response.json()
        assert data["search_connected"] is False
        search_comp = next(c for c in data["components"] if c["name"] == "search")
        assert "not registered" in search_comp["detail"]

    def test_search_duckduckgo_healthy(self, health_runtime, health_client):
        """DuckDuckGo search provider → healthy."""
        health_runtime.app_config.web_search.provider = "duckduckgo"

        response = health_client.get("/health")
        data = response.json()
        assert data["search_connected"] is True
        search_comp = next(c for c in data["components"] if c["name"] == "search")
        assert "duckduckgo" in search_comp["detail"]


# --------------------------------------------------------------------------- #
# Tracer check                                                                #
# --------------------------------------------------------------------------- #

class TestHealthTracerCheck:
    def test_tracer_healthy(self, health_runtime, health_client):
        """Tracer (NullTracer) → healthy."""
        # The default tracer is NullTracer
        from agentbase.core.tracer import NullTracer
        health_runtime.factory._tracer = NullTracer()

        response = health_client.get("/health")
        data = response.json()
        assert data["tracer_connected"] is True

    def test_tracer_none_is_healthy(self, health_runtime, health_client):
        """Tracer=None → healthy."""
        health_runtime.factory._tracer = None

        response = health_client.get("/health")
        data = response.json()
        assert data["tracer_connected"] is True


# --------------------------------------------------------------------------- #
# Aggregate status logic                                                      #
# --------------------------------------------------------------------------- #

class TestHealthAggregateStatus:
    def test_all_fail_is_unhealthy(self, health_runtime):
        """All components fail → status=unhealthy."""
        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(
            __import__("pathlib").Path(health_runtime.root_dir) / "workspace"
        )
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.health_check = HealthCheckConfig(
            check_storage=True,
            check_queue=True,
            check_embedding=False,
            check_search=False,
            check_tracer=False,
        )
        runtime = __import__("agentbase.bootstrap", fromlist=["RuntimeContext"]).RuntimeContext(
            root_dir=health_runtime.root_dir, app_config=app_config,
        )
        runtime.list_agents = lambda: ["default"]
        runtime.get_agent_config = lambda name=None: MagicMock()

        # Make storage fail
        mock_storage = MagicMock()
        mock_storage.health_check.side_effect = RuntimeError("storage down")
        runtime.factory._storage = mock_storage

        # Make queue fail (spec=MemoryRequestQueue so _get_client is not present)
        mock_queue = MagicMock(spec=MemoryRequestQueue)
        mock_queue.stats.side_effect = RuntimeError("queue down")
        runtime.factory._queue = mock_queue

        reset_runtime()
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/health")
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["storage_connected"] is False
        assert data["queue_connected"] is False
        reset_runtime()

    def test_partial_failure_is_degraded(self, health_runtime, health_client):
        """One component fails, others pass → status=degraded."""
        mock_storage = MagicMock()
        mock_storage.health_check.return_value = False
        health_runtime.factory._storage = mock_storage

        response = health_client.get("/health")
        data = response.json()
        assert data["status"] == "degraded"
        assert data["storage_connected"] is False
        assert data["queue_connected"] is True

    def test_no_checks_enabled_is_ok(self, tmp_path):
        """All checks disabled → status=ok, empty components list."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.health_check = HealthCheckConfig(
            check_storage=False,
            check_queue=False,
            check_embedding=False,
            check_search=False,
            check_tracer=False,
        )
        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
        runtime.list_agents = lambda: ["default"]
        runtime.get_agent_config = lambda name=None: MagicMock()

        reset_runtime()
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/health")
        data = response.json()
        assert data["status"] == "ok"
        assert data["components"] == []
        reset_runtime()


# --------------------------------------------------------------------------- #
# Config toggle tests                                                         #
# --------------------------------------------------------------------------- #

class TestHealthConfigToggles:
    def test_check_storage_false_skips_storage(self, tmp_path):
        """check_storage=False → storage not in components."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.health_check = HealthCheckConfig(
            check_storage=False,
            check_queue=True,
            check_embedding=False,
            check_search=False,
            check_tracer=False,
        )
        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
        runtime.list_agents = lambda: ["default"]
        runtime.get_agent_config = lambda name=None: MagicMock()

        reset_runtime()
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/health")
        data = response.json()
        names = [c["name"] for c in data["components"]]
        assert "storage" not in names
        assert "queue" in names
        # storage_connected defaults to True when not checked
        assert data["storage_connected"] is True
        reset_runtime()

    def test_check_embedding_true_adds_component(self, tmp_path):
        """check_embedding=True → embedding component present."""
        from agentbase.bootstrap import RuntimeContext

        app_config = AppConfig()
        app_config.runtime.workspace_dir = str(tmp_path / "workspace")
        app_config.runtime.config_dir = "configs"
        app_config.runtime.default_agent = "default"
        app_config.health_check = HealthCheckConfig(
            check_storage=False,
            check_queue=False,
            check_embedding=True,
            check_search=False,
            check_tracer=False,
        )
        runtime = RuntimeContext(root_dir=tmp_path, app_config=app_config)
        runtime.list_agents = lambda: ["default"]
        runtime.get_agent_config = lambda name=None: MagicMock()

        reset_runtime()
        app = create_app(runtime=runtime)
        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/health")
        data = response.json()
        names = [c["name"] for c in data["components"]]
        assert "embedding" in names
        assert len(names) == 1
        reset_runtime()


# --------------------------------------------------------------------------- #
# Backward compatibility with existing health tests                           #
# --------------------------------------------------------------------------- #

class TestHealthBackwardCompat:
    def test_health_returns_200(self, default_client):
        """Health endpoint always returns 200 (even degraded)."""
        response = default_client.get("/health")
        assert response.status_code == 200

    def test_health_has_status_field(self, default_client):
        response = default_client.get("/health")
        data = response.json()
        assert "status" in data

    def test_health_has_storage_and_queue_fields(self, default_client):
        """Legacy fields storage_connected and queue_connected still present."""
        response = default_client.get("/health")
        data = response.json()
        assert "storage_connected" in data
        assert "queue_connected" in data

    def test_health_has_auth_fields(self, default_client):
        response = default_client.get("/health")
        data = response.json()
        assert "auth_enabled" in data
        assert "auth_type" in data


# --------------------------------------------------------------------------- #
# HealthCheckConfig schema tests                                              #
# --------------------------------------------------------------------------- #

class TestHealthCheckConfigSchema:
    def test_default_config(self):
        cfg = HealthCheckConfig()
        assert cfg.check_storage is True
        assert cfg.check_queue is True
        assert cfg.check_embedding is False
        assert cfg.check_search is False
        assert cfg.check_tracer is False

    def test_all_enabled(self):
        cfg = HealthCheckConfig(
            check_storage=True,
            check_queue=True,
            check_embedding=True,
            check_search=True,
            check_tracer=True,
        )
        assert cfg.check_embedding is True
        assert cfg.check_search is True
