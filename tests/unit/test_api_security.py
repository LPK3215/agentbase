"""Tests for API security: authentication, rate limiting, CORS, error handling."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentbase.api import RateLimiter, _reset_rate_limiter, create_app, reset_runtime
from agentbase.config.schema import AgentConfig, AppConfig
from agentbase.runtime.events import EventType, RuntimeEvent


@pytest.fixture
def mock_runtime(tmp_path):
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"

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

    def fake_invoke(**kwargs):
        return {
            "thread_id": "test-thread",
            "agent": kwargs.get("agent_name", "default"),
            "output_text": "Response",
            "result": {"messages": []},
        }

    runtime.runner.invoke = fake_invoke
    runtime.runner.stream = lambda **kwargs: iter([
        RuntimeEvent(type=EventType.RUN_STARTED, data={}),
        RuntimeEvent(type=EventType.RUN_FINISHED, data={"output_text": "done"}),
    ])
    runtime.runner.resume = fake_invoke
    return runtime


@pytest.fixture
def client_no_auth(mock_runtime):
    """Client with auth disabled (dev mode)."""
    reset_runtime()
    _reset_rate_limiter()
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        app = create_app(runtime=mock_runtime)
        # raise_server_exceptions=False surfaces errors handled by the app's
        # global_exception_handler (e.g. 404) as HTTP responses, matching
        # behaviour under a real uvicorn server.
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    reset_runtime()
    _reset_rate_limiter()


@pytest.fixture
def client_with_auth(mock_runtime):
    """Client with API key auth enabled."""
    reset_runtime()
    _reset_rate_limiter()
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": "secret-key-123"}, clear=False):
        app = create_app(runtime=mock_runtime)
        # raise_server_exceptions=False surfaces errors handled by the app's
        # global_exception_handler (e.g. 404) as HTTP responses, matching
        # behaviour under a real uvicorn server.
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    reset_runtime()
    _reset_rate_limiter()


class TestHealthPublic:
    def test_health_no_auth_needed(self, client_with_auth):
        """Health endpoint should work without API key."""
        response = client_with_auth.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["auth_enabled"] is True


class TestApiKeyAuth:
    def test_no_auth_header_returns_401(self, client_with_auth):
        response = client_with_auth.get("/agents")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "AGENTBASE_AUTH_001"

    def test_wrong_api_key_returns_401(self, client_with_auth):
        response = client_with_auth.get(
            "/agents",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_correct_bearer_auth(self, client_with_auth):
        response = client_with_auth.get(
            "/agents",
            headers={"Authorization": "Bearer secret-key-123"},
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1

    def test_correct_x_api_key_header(self, client_with_auth):
        response = client_with_auth.get(
            "/agents",
            headers={"X-API-Key": "secret-key-123"},
        )
        assert response.status_code == 200

    def test_auth_disabled_no_header_needed(self, client_no_auth):
        response = client_no_auth.get("/agents")
        assert response.status_code == 200

    def test_invoke_requires_auth(self, client_with_auth):
        response = client_with_auth.post(
            "/agents/default/invoke",
            json={"message": "hello"},
        )
        assert response.status_code == 401

    def test_invoke_with_auth_works(self, client_with_auth):
        response = client_with_auth.post(
            "/agents/default/invoke",
            json={"message": "hello"},
            headers={"Authorization": "Bearer secret-key-123"},
        )
        assert response.status_code == 200

    def test_health_shows_auth_status(self, client_no_auth):
        response = client_no_auth.get("/health")
        assert response.json()["auth_enabled"] is False


class TestRateLimiting:
    def test_rate_limiter_allows_under_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for i in range(5):
            assert limiter.check("1.2.3.4") is True

    def test_rate_limiter_blocks_over_limit(self):
        # burst=0 makes max_requests a hard cap.
        limiter = RateLimiter(max_requests=3, window_seconds=60, burst=0)
        for i in range(3):
            assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is False

    def test_rate_limiter_separate_ips(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60, burst=0)
        assert limiter.check("1.1.1.1") is True
        assert limiter.check("1.1.1.1") is True
        assert limiter.check("1.1.1.1") is False
        # Different IP should still be allowed
        assert limiter.check("2.2.2.2") is True

    def test_rate_limiter_reset(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60, burst=0)
        limiter.check("1.1.1.1")
        limiter.check("1.1.1.1")
        assert limiter.check("1.1.1.1") is False
        limiter.reset()
        assert limiter.check("1.1.1.1") is True

    def test_rate_limiter_burst_allows_extra(self):
        """burst>0 lets the limiter admit more than max_requests in a burst."""
        limiter = RateLimiter(max_requests=2, window_seconds=60, burst=2)
        for _ in range(4):  # max_requests + burst == 4
            assert limiter.check("9.9.9.9") is True
        assert limiter.check("9.9.9.9") is False

    def test_rate_limit_returns_429(self, mock_runtime):
        """When rate limit is exceeded, should return 429."""
        reset_runtime()
        with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
            # Override the global rate limiter with a tiny one
            import agentbase.api as api_module
            original_limiter = api_module._rate_limiter
            api_module._rate_limiter = RateLimiter(max_requests=2, window_seconds=60, burst=0)

            app = create_app(runtime=mock_runtime)
            with TestClient(app) as c:
                # First 2 requests should work
                assert c.get("/agents").status_code == 200
                assert c.get("/agents").status_code == 200
                # Third should be rate limited
                response = c.get("/agents")
                assert response.status_code == 429
                data = response.json()
                assert data["code"] == "AGENTBASE_RATE_001"

            api_module._rate_limiter = original_limiter
        reset_runtime()


class TestCors:
    def test_cors_header_present(self, client_no_auth):
        """CORS headers should be present."""
        response = client_no_auth.options(
            "/agents",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_cors_allows_all_origins_by_default(self, client_no_auth):
        response = client_no_auth.get(
            "/agents",
            headers={"Origin": "http://example.com"},
        )
        assert response.status_code == 200


class TestErrorHandling:
    def test_agent_not_found_returns_404(self, client_no_auth):
        from agentbase.api import get_runtime

        original = get_runtime().get_agent

        def raise_error(name=None):
            raise Exception("not found")

        get_runtime().get_agent = raise_error
        try:
            response = client_no_auth.post(
                "/agents/nonexistent/invoke",
                json={"message": "test"},
            )
            assert response.status_code == 404
        finally:
            get_runtime().get_agent = original

    def test_health_endpoint_works_even_if_runtime_fails(self, mock_runtime):
        """Health should return 200 even if runtime has issues."""
        reset_runtime()
        with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
            app = create_app(runtime=mock_runtime)
            with TestClient(app) as c:
                response = c.get("/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
        reset_runtime()

    def test_global_exception_handler(self, mock_runtime):
        """Unhandled exceptions should return structured error response."""
        reset_runtime()
        with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
            # Override get_agent_config to raise
            original = mock_runtime.get_agent_config

            def raise_error(name=None):
                raise RuntimeError("unexpected error")

            mock_runtime.get_agent_config = raise_error
            try:
                app = create_app(runtime=mock_runtime)
                with TestClient(app, raise_server_exceptions=False) as c:
                    response = c.get("/agents")
                    assert response.status_code == 500
                    data = response.json()
                    assert "error" in data
            finally:
                mock_runtime.get_agent_config = original
        reset_runtime()


class TestQueueWithAuth:
    def test_queue_submit_requires_auth(self, client_with_auth):
        response = client_with_auth.post("/queue/submit", json={
            "agent_name": "default",
            "message": "test",
        })
        assert response.status_code == 401

    def test_queue_submit_with_auth(self, client_with_auth):
        response = client_with_auth.post(
            "/queue/submit",
            json={"agent_name": "default", "message": "test"},
            headers={"Authorization": "Bearer secret-key-123"},
        )
        assert response.status_code == 200
