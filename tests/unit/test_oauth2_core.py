"""Unit tests for OAuth2 third-party login service (core.oauth2).

Covers:
- OAuth2ProviderConfig / OAuth2UserInfo data models
- GoogleOAuth2Provider — authorize URL, token exchange (mocked), user info (mocked)
- GitHubOAuth2Provider — authorize URL, token exchange (mocked), user info (mocked)
- NullOAuth2Provider — no-op behavior
- StateStore — generate, validate (one-time use), expiry, cleanup
- OAuth2Manager — disabled (no-op), enabled, provider config, state management
- Singleton — get/set/reset
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agentbase.core.oauth2 import (
    GitHubOAuth2Provider,
    GoogleOAuth2Provider,
    NullOAuth2Provider,
    OAuth2Manager,
    OAuth2ProviderConfig,
    OAuth2UserInfo,
    StateStore,
    get_oauth2_manager,
    reset_oauth2_manager,
    set_oauth2_manager,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TestOAuth2ProviderConfig:
    def test_defaults(self):
        cfg = OAuth2ProviderConfig(name="google")
        assert cfg.name == "google"
        assert cfg.client_id == ""
        assert cfg.client_secret == ""
        assert cfg.redirect_uri == ""
        assert cfg.scopes == []
        assert cfg.default_roles == ["user"]

    def test_custom(self):
        cfg = OAuth2ProviderConfig(
            name="github",
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            scopes=["user:email"],
            default_roles=["admin"],
        )
        assert cfg.client_id == "abc"
        assert cfg.client_secret == "secret"
        assert cfg.redirect_uri == "http://localhost/cb"
        assert cfg.scopes == ["user:email"]
        assert cfg.default_roles == ["admin"]

    def test_to_dict_hides_secret(self):
        cfg = OAuth2ProviderConfig(name="google", client_secret="topsecret")
        d = cfg.to_dict()
        assert d["client_secret"] == "***"


class TestOAuth2UserInfo:
    def test_defaults(self):
        info = OAuth2UserInfo(provider="google", provider_user_id="123")
        assert info.provider == "google"
        assert info.provider_user_id == "123"
        assert info.email == ""
        assert info.name == ""
        assert info.avatar_url == ""
        assert info.raw == {}

    def test_custom(self):
        info = OAuth2UserInfo(
            provider="github",
            provider_user_id="42",
            email="user@example.com",
            name="Test User",
            avatar_url="https://example.com/avatar.png",
            raw={"login": "testuser"},
        )
        d = info.to_dict()
        assert d["provider"] == "github"
        assert d["email"] == "user@example.com"
        assert d["name"] == "Test User"
        assert d["avatar_url"] == "https://example.com/avatar.png"
        assert d["raw"]["login"] == "testuser"


# ---------------------------------------------------------------------------
# GoogleOAuth2Provider
# ---------------------------------------------------------------------------

class TestGoogleProvider:
    def test_name(self):
        p = GoogleOAuth2Provider()
        assert p.name == "google"

    def test_authorize_url(self):
        p = GoogleOAuth2Provider()
        url = p.get_authorize_url(
            client_id="test-client-id",
            redirect_uri="http://localhost:8000/callback",
            scopes=["openid", "email"],
            state="random-state",
        )
        assert "accounts.google.com" in url
        assert "client_id=test-client-id" in url
        assert "state=random-state" in url
        assert "response_type=code" in url
        assert "scope=openid+email" in url

    def test_authorize_url_default_scope(self):
        p = GoogleOAuth2Provider()
        url = p.get_authorize_url(
            client_id="cid",
            redirect_uri="http://localhost/cb",
            scopes=[],
            state="s",
        )
        assert "scope=openid+email+profile" in url

    @patch("agentbase.core.oauth2._http_post_json")
    def test_exchange_code(self, mock_post):
        mock_post.return_value = {"access_token": "tok123", "token_type": "Bearer"}
        p = GoogleOAuth2Provider()
        result = p.exchange_code(
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            code="authcode",
        )
        assert result["access_token"] == "tok123"
        assert mock_post.call_count == 1
        call_args = mock_post.call_args
        assert "oauth2.googleapis.com/token" in call_args[0][0]

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info(self, mock_get):
        mock_get.return_value = {
            "id": "12345",
            "email": "user@gmail.com",
            "name": "Test User",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }
        p = GoogleOAuth2Provider()
        info = p.get_user_info(access_token="tok123")
        assert info.provider == "google"
        assert info.provider_user_id == "12345"
        assert info.email == "user@gmail.com"
        assert info.name == "Test User"
        assert info.avatar_url == "https://lh3.googleusercontent.com/photo.jpg"


# ---------------------------------------------------------------------------
# GitHubOAuth2Provider
# ---------------------------------------------------------------------------

class TestGitHubProvider:
    def test_name(self):
        p = GitHubOAuth2Provider()
        assert p.name == "github"

    def test_authorize_url(self):
        p = GitHubOAuth2Provider()
        url = p.get_authorize_url(
            client_id="gh-client-id",
            redirect_uri="http://localhost:8000/callback",
            scopes=["user:email"],
            state="gh-state",
        )
        assert "github.com/login/oauth/authorize" in url
        assert "client_id=gh-client-id" in url
        assert "state=gh-state" in url
        assert "scope=user%3Aemail" in url or "scope=user:email" in url

    def test_authorize_url_default_scope(self):
        p = GitHubOAuth2Provider()
        url = p.get_authorize_url(
            client_id="cid",
            redirect_uri="http://localhost/cb",
            scopes=[],
            state="s",
        )
        assert "scope=user%3Aemail" in url or "scope=user:email" in url

    @patch("agentbase.core.oauth2._http_post_json")
    def test_exchange_code(self, mock_post):
        mock_post.return_value = {"access_token": "gh_tok", "scope": "user:email"}
        p = GitHubOAuth2Provider()
        result = p.exchange_code(
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            code="gh_code",
        )
        assert result["access_token"] == "gh_tok"

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info_with_email(self, mock_get):
        mock_get.return_value = {
            "id": 67890,
            "email": "user@users.noreply.github.com",
            "name": "GH User",
            "login": "ghuser",
            "avatar_url": "https://avatars.githubusercontent.com/u/67890",
        }
        p = GitHubOAuth2Provider()
        info = p.get_user_info(access_token="gh_tok")
        assert info.provider == "github"
        assert info.provider_user_id == "67890"
        assert info.email == "user@users.noreply.github.com"
        assert info.name == "GH User"

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info_email_from_emails_api(self, mock_get):
        # First call: user info (no email)
        # Second call: emails API
        mock_get.side_effect = [
            {
                "id": 111,
                "login": "testuser",
                "name": "Test",
                "avatar_url": "https://avatars.githubusercontent.com/u/111",
            },
            [
                {"email": "primary@users.noreply.github.com", "primary": True},
                {"email": "secondary@users.noreply.github.com", "primary": False},
            ],
        ]
        p = GitHubOAuth2Provider()
        info = p.get_user_info(access_token="tok")
        assert info.email == "primary@users.noreply.github.com"
        assert info.provider_user_id == "111"

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info_email_fetch_fails(self, mock_get):
        from agentbase.runtime.errors import AgentbaseError

        mock_get.side_effect = [
            {"id": 222, "login": "test2", "name": "Test2"},
            AgentbaseError("fail"),
        ]
        p = GitHubOAuth2Provider()
        info = p.get_user_info(access_token="tok")
        assert info.email == ""
        assert info.provider_user_id == "222"


# ---------------------------------------------------------------------------
# NullOAuth2Provider
# ---------------------------------------------------------------------------

class TestNullProvider:
    def test_name(self):
        p = NullOAuth2Provider()
        assert p.name == "null"

    def test_authorize_url_empty(self):
        p = NullOAuth2Provider()
        assert p.get_authorize_url(client_id="", redirect_uri="", scopes=[], state="") == ""

    def test_exchange_code_empty(self):
        p = NullOAuth2Provider()
        assert p.exchange_code(client_id="", client_secret="", redirect_uri="", code="") == {}

    def test_get_user_info_empty(self):
        p = NullOAuth2Provider()
        info = p.get_user_info(access_token="")
        assert info.provider == "null"
        assert info.provider_user_id == ""


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------

class TestStateStore:
    def test_generate_and_validate(self):
        store = StateStore()
        state = store.generate()
        assert len(state) > 10
        assert store.validate(state) is True

    def test_validate_one_time_use(self):
        store = StateStore()
        state = store.generate()
        assert store.validate(state) is True
        assert store.validate(state) is False

    def test_validate_empty(self):
        store = StateStore()
        assert store.validate("") is False

    def test_validate_unknown(self):
        store = StateStore()
        assert store.validate("unknown-state") is False

    def test_expiry(self):
        store = StateStore(max_age_seconds=0)
        state = store.generate()
        time.sleep(0.1)
        assert store.validate(state) is False

    def test_multiple_states(self):
        store = StateStore()
        s1 = store.generate()
        s2 = store.generate()
        assert store.validate(s1) is True
        assert store.validate(s2) is True

    def test_cleanup_removes_expired(self):
        store = StateStore(max_age_seconds=0)
        s1 = store.generate()
        time.sleep(0.1)
        s2 = store.generate()
        assert store.validate(s2) is True
        assert store.validate(s1) is False


# ---------------------------------------------------------------------------
# OAuth2Manager — disabled
# ---------------------------------------------------------------------------

class TestOAuth2ManagerDisabled:
    def test_disabled_manager(self):
        mgr = OAuth2Manager(enabled=False)
        assert mgr.enabled is False
        assert mgr.providers == {}

    def test_disabled_get_authorize_url(self):
        mgr = OAuth2Manager(enabled=False)
        assert mgr.get_authorize_url("google", state="s") == ""

    def test_disabled_exchange_code(self):
        mgr = OAuth2Manager(enabled=False)
        assert mgr.exchange_code("google", code="c") == {}

    def test_disabled_get_user_info(self):
        mgr = OAuth2Manager(enabled=False)
        info = mgr.get_user_info("google", access_token="t")
        assert info.provider == "google"
        assert info.provider_user_id == ""

    def test_disabled_has_provider(self):
        mgr = OAuth2Manager(enabled=False)
        assert mgr.has_provider("google") is False

    def test_disabled_list_providers(self):
        mgr = OAuth2Manager(enabled=False)
        assert mgr.list_providers() == []


# ---------------------------------------------------------------------------
# OAuth2Manager — enabled
# ---------------------------------------------------------------------------

class TestOAuth2ManagerEnabled:
    @pytest.fixture
    def mgr(self):
        providers = {
            "google": OAuth2ProviderConfig(
                name="google",
                client_id="g-client",
                client_secret="g-secret",
                redirect_uri="http://localhost:8000/auth/oauth2/google/callback",
                scopes=["openid", "email"],
            ),
            "github": OAuth2ProviderConfig(
                name="github",
                client_id="gh-client",
                client_secret="gh-secret",
                redirect_uri="http://localhost:8000/auth/oauth2/github/callback",
                scopes=["user:email"],
            ),
        }
        return OAuth2Manager(providers=providers, enabled=True)

    def test_enabled(self, mgr):
        assert mgr.enabled is True

    def test_has_provider(self, mgr):
        assert mgr.has_provider("google") is True
        assert mgr.has_provider("github") is True
        assert mgr.has_provider("gitlab") is False

    def test_list_providers(self, mgr):
        names = mgr.list_providers()
        assert "google" in names
        assert "github" in names

    def test_get_provider_config(self, mgr):
        cfg = mgr.get_provider_config("google")
        assert cfg.client_id == "g-client"
        assert cfg.client_secret == "g-secret"

    def test_get_provider_config_unknown(self, mgr):
        from agentbase.runtime.errors import AgentbaseError

        with pytest.raises(AgentbaseError):
            mgr.get_provider_config("unknown")

    def test_get_authorize_url_google(self, mgr):
        state = mgr.generate_state()
        url = mgr.get_authorize_url("google", state=state)
        assert "accounts.google.com" in url
        assert "client_id=g-client" in url
        assert f"state={state}" in url

    def test_get_authorize_url_github(self, mgr):
        state = mgr.generate_state()
        url = mgr.get_authorize_url("github", state=state)
        assert "github.com/login/oauth/authorize" in url
        assert "client_id=gh-client" in url

    def test_generate_and_validate_state(self, mgr):
        state = mgr.generate_state()
        assert mgr.validate_state(state) is True
        assert mgr.validate_state(state) is False

    def test_validate_state_unknown(self, mgr):
        assert mgr.validate_state("bogus") is False

    @patch("agentbase.core.oauth2._http_post_json")
    def test_exchange_code_google(self, mock_post, mgr):
        mock_post.return_value = {"access_token": "g_tok"}
        result = mgr.exchange_code("google", code="authcode")
        assert result["access_token"] == "g_tok"

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info_google(self, mock_get, mgr):
        mock_get.return_value = {
            "id": "g-123",
            "email": "guser@gmail.com",
            "name": "G User",
        }
        info = mgr.get_user_info("google", access_token="g_tok")
        assert info.provider == "google"
        assert info.email == "guser@gmail.com"

    @patch("agentbase.core.oauth2._http_get_json")
    def test_get_user_info_github(self, mock_get, mgr):
        mock_get.return_value = {
            "id": 12345,
            "email": "ghuser@users.noreply.github.com",
            "name": "GH User",
            "login": "ghuser",
        }
        info = mgr.get_user_info("github", access_token="gh_tok")
        assert info.provider == "github"
        assert info.email == "ghuser@users.noreply.github.com"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_set_and_get(self):
        mgr = OAuth2Manager(enabled=True)
        set_oauth2_manager(mgr)
        assert get_oauth2_manager() is mgr
        reset_oauth2_manager()

    def test_reset(self):
        mgr = OAuth2Manager(enabled=True)
        set_oauth2_manager(mgr)
        reset_oauth2_manager()
        assert get_oauth2_manager() is None

    def test_default_is_none(self):
        reset_oauth2_manager()
        assert get_oauth2_manager() is None
