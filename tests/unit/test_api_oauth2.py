"""Tests for OAuth2 API endpoints.

Covers:
- GET /auth/oauth2/providers — list providers (enabled/disabled)
- GET /auth/oauth2/{provider}/authorize — redirect to provider
- GET /auth/oauth2/{provider}/callback — full callback flow (mocked)
- Error cases: disabled, unknown provider, invalid state, token exchange failure
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentbase.api import _reset_rate_limiter, _reset_user_manager, create_app, reset_runtime
from agentbase.config.schema import (
    AgentConfig,
    AppConfig,
    OAuth2Config,
    OAuth2ProviderConfigItem,
    UserManagerConfig,
)
from agentbase.core.oauth2 import reset_oauth2_manager
from agentbase.core.user_manager import UserManager
from agentbase.core.user_manager import set_user_manager as _set_core_user_manager


@pytest.fixture
def mock_runtime(tmp_path):
    from agentbase.bootstrap import RuntimeContext

    app_config = AppConfig()
    app_config.runtime.workspace_dir = str(tmp_path / "workspace")
    app_config.runtime.config_dir = "configs"
    app_config.runtime.default_agent = "default"
    app_config.auth.type = "none"

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
    runtime.runner.invoke = lambda **kw: {"output_text": "ok"}
    runtime.runner.stream = lambda **kw: iter([])
    runtime.runner.resume = lambda **kw: {"output_text": "ok"}
    return runtime


@pytest.fixture
def client_oauth2_disabled(mock_runtime):
    """Client with OAuth2 disabled (default)."""
    reset_runtime()
    reset_oauth2_manager()
    _reset_user_manager()
    _reset_rate_limiter()
    _set_core_user_manager(UserManager(enabled=False))
    app = create_app(runtime=mock_runtime)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_runtime()
    reset_oauth2_manager()
    _reset_user_manager()
    _reset_rate_limiter()
    _set_core_user_manager(UserManager(enabled=False))


@pytest.fixture
def client_oauth2_enabled(mock_runtime, tmp_path):
    """Client with OAuth2 enabled (Google + GitHub)."""
    reset_runtime()
    reset_oauth2_manager()
    _reset_user_manager()
    _reset_rate_limiter()

    mock_runtime.app_config.oauth2 = OAuth2Config(
        enabled=True,
        providers={
            "google": OAuth2ProviderConfigItem(
                client_id="g-test-client",
                client_secret="g-test-secret",
                redirect_uri="http://localhost:8000/auth/oauth2/google/callback",
                scopes=["openid", "email"],
                default_roles=["user"],
            ),
            "github": OAuth2ProviderConfigItem(
                client_id="gh-test-client",
                client_secret="gh-test-secret",
                redirect_uri="http://localhost:8000/auth/oauth2/github/callback",
                scopes=["user:email"],
                default_roles=["user"],
            ),
        },
    )
    # Enable user manager for auto-registration
    mock_runtime.app_config.user_manager = UserManagerConfig(
        enabled=True,
        provider="memory",
    )

    app = create_app(runtime=mock_runtime)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    reset_runtime()
    reset_oauth2_manager()
    _reset_user_manager()
    _reset_rate_limiter()
    _set_core_user_manager(UserManager(enabled=False))


# ---------------------------------------------------------------------------
# GET /auth/oauth2/providers
# ---------------------------------------------------------------------------

class TestListProviders:
    def test_disabled_returns_empty(self, client_oauth2_disabled):
        resp = client_oauth2_disabled.get("/auth/oauth2/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["providers"] == []

    def test_enabled_returns_providers(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get("/auth/oauth2/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        names = [p["name"] for p in data["providers"]]
        assert "google" in names
        assert "github" in names
        # Secrets should be hidden
        for p in data["providers"]:
            assert p["client_secret"] == "***"


# ---------------------------------------------------------------------------
# GET /auth/oauth2/{provider}/authorize
# ---------------------------------------------------------------------------

class TestAuthorizeEndpoint:
    def test_disabled_returns_503(self, client_oauth2_disabled):
        resp = client_oauth2_disabled.get("/auth/oauth2/google/authorize")
        assert resp.status_code == 503

    def test_unknown_provider_returns_404(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get("/auth/oauth2/gitlab/authorize")
        assert resp.status_code == 404

    def test_google_authorize_redirects(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/authorize",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "accounts.google.com" in location
        assert "client_id=g-test-client" in location
        assert "state=" in location

    def test_github_authorize_redirects(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/github/authorize",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=gh-test-client" in location


# ---------------------------------------------------------------------------
# GET /auth/oauth2/{provider}/callback
# ---------------------------------------------------------------------------

class TestCallbackEndpoint:
    def test_disabled_returns_503(self, client_oauth2_disabled):
        resp = client_oauth2_disabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "x", "state": "y"},
        )
        assert resp.status_code == 503

    def test_unknown_provider_returns_404(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/gitlab/callback",
            params={"code": "x", "state": "y"},
        )
        assert resp.status_code == 404

    def test_invalid_state_returns_400(self, client_oauth2_enabled):
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "somecode", "state": "bogus-state"},
        )
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()

    @patch("agentbase.core.oauth2._http_post_json")
    @patch("agentbase.core.oauth2._http_get_json")
    def test_full_google_callback_flow(self, mock_get, mock_post, client_oauth2_enabled):
        """Test the full OAuth2 callback flow with mocked HTTP calls."""
        # 1. Generate a valid state by calling authorize
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/authorize",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        # Extract state from the redirect URL
        from urllib.parse import parse_qs, urlparse
        params = parse_qs(urlparse(location).query)
        state = params["state"][0]

        # 2. Mock token exchange
        mock_post.return_value = {
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        # 3. Mock user info
        mock_get.return_value = {
            "id": "g-12345",
            "email": "oauthuser@gmail.com",
            "name": "OAuth User",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }

        # 4. Call callback
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "mock-auth-code", "state": state},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "google"
        assert data["token"] is None  # JWT auth not configured
        assert data["user"] is not None
        assert data["user"]["email"] == "oauthuser@gmail.com"
        assert data["user"]["username"] == "google_g-12345"
        assert data["user"]["roles"] == ["user"]
        assert data["user_info"]["email"] == "oauthuser@gmail.com"
        assert data["user_info"]["provider_user_id"] == "g-12345"

    @patch("agentbase.core.oauth2._http_post_json")
    @patch("agentbase.core.oauth2._http_get_json")
    def test_existing_user_matched_by_email(self, mock_get, mock_post, client_oauth2_enabled):
        """Test that an existing user with the same email is matched, not re-registered."""
        # 1. Pre-register a user with the same email via API
        register_resp = client_oauth2_enabled.post(
            "/auth/register",
            json={
                "username": "existing_user",
                "email": "existing@gmail.com",
                "password": "pass123",
            },
        )
        assert register_resp.status_code == 200

        # 2. Get a valid state
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/authorize",
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

        # 3. Mock token exchange
        mock_post.return_value = {"access_token": "tok", "token_type": "Bearer"}

        # 4. Mock user info — same email as existing user
        mock_get.return_value = {
            "id": "g-99999",
            "email": "existing@gmail.com",
            "name": "Existing User",
            "picture": "https://example.com/photo.jpg",
        }

        # 5. Call callback
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "code", "state": state},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "existing_user"
        assert data["user"]["email"] == "existing@gmail.com"
        # Metadata should be updated
        assert data["user"]["metadata"]["oauth2_provider"] == "google"

    @patch("agentbase.core.oauth2._http_post_json")
    def test_token_exchange_failure(self, mock_post, client_oauth2_enabled):
        """Test that a token exchange failure returns 502."""
        from agentbase.runtime.errors import AgentbaseError

        # 1. Get a valid state
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/authorize",
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

        # 2. Mock token exchange failure
        mock_post.side_effect = AgentbaseError("Exchange failed")

        # 3. Call callback
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "bad-code", "state": state},
        )
        assert resp.status_code == 502

    @patch("agentbase.core.oauth2._http_post_json")
    @patch("agentbase.core.oauth2._http_get_json")
    def test_no_access_token_in_response(self, mock_get, mock_post, client_oauth2_enabled):
        """Test that missing access_token in token response returns 502."""
        # 1. Get a valid state
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/authorize",
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

        # 2. Mock token response without access_token
        mock_post.return_value = {"error": "invalid_grant"}

        # 3. Call callback
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/google/callback",
            params={"code": "code", "state": state},
        )
        assert resp.status_code == 502

    @patch("agentbase.core.oauth2._http_post_json")
    @patch("agentbase.core.oauth2._http_get_json")
    def test_github_callback_flow(self, mock_get, mock_post, client_oauth2_enabled):
        """Test GitHub OAuth2 callback flow."""
        # 1. Get a valid state
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/github/authorize",
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

        # 2. Mock token exchange
        mock_post.return_value = {"access_token": "gh-tok", "scope": "user:email"}

        # 3. Mock user info
        mock_get.return_value = {
            "id": 12345,
            "email": "ghuser@users.noreply.github.com",
            "name": "GH User",
            "login": "ghuser",
            "avatar_url": "https://avatars.githubusercontent.com/u/12345",
        }

        # 4. Call callback
        resp = client_oauth2_enabled.get(
            "/auth/oauth2/github/callback",
            params={"code": "gh-code", "state": state},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "github"
        assert data["user"]["email"] == "ghuser@users.noreply.github.com"
        assert data["user"]["username"] == "github_12345"
