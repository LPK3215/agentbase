"""Security tests for JWT authentication — focuses on the default-secret fix.

Covers three paths:
- normal: JWTAuth with an explicit secret works correctly (create + verify roundtrip)
- boundary: empty secret generates an ephemeral random key (not a well-known default);
            tokens from one ephemeral instance cannot be forged by another
- error: _get_jwt_auth raises ConfigError when type=jwt but secret is empty
"""
from __future__ import annotations

import pytest

from agentbase.extensions.auth import JWTAuth, Role


# ── Normal ──────────────────────────────────────────────────────


class TestJWTExplicitSecret:
    """JWTAuth with a proper explicit secret should work end-to-end."""

    def test_create_and_verify_token(self):
        auth = JWTAuth(secret="my-strong-secret-12345")
        token = auth.create_token(user_id="user1", roles=[Role.USER])
        payload = auth.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user1"
        assert "user" in payload["roles"]
        assert payload["exp"] > payload["iat"]

    def test_verify_rejects_token_signed_with_different_secret(self):
        auth_a = JWTAuth(secret="secret-a")
        auth_b = JWTAuth(secret="secret-b")
        token = auth_a.create_token(user_id="user1")
        assert auth_b.verify_token(token) is None

    def test_revoked_token_is_rejected(self):
        auth = JWTAuth(secret="revoke-secret-999")
        token = auth.create_token(user_id="user1")
        assert auth.verify_token(token) is not None
        assert auth.revoke_token(token) is True
        assert auth.verify_token(token) is None

    def test_expired_token_is_rejected(self):
        auth = JWTAuth(secret="expiry-secret", token_expiry_hours=0)
        # token_expiry_hours=0 → exp ≈ iat → already expired
        token = auth.create_token(user_id="user1")
        assert auth.verify_token(token) is None


# ── Boundary: empty secret ─────────────────────────────────────


class TestJWTEmptySecret:
    """When no secret is provided, JWTAuth must NOT use a well-known default."""

    def test_no_well_known_default_secret(self):
        """The old default 'agentbase-default-secret' must not verify."""
        auth_empty = JWTAuth()  # no secret
        token = auth_empty.create_token(user_id="user1")
        # An attacker who knows the old default should NOT be able to forge
        auth_attacker = JWTAuth(secret="agentbase-default-secret")
        assert auth_attacker.verify_token(token) is None

    def test_ephemeral_secrets_differ_across_instances(self):
        """Two JWTAuth instances with no secret must get different keys."""
        auth_a = JWTAuth()
        auth_b = JWTAuth()
        token_a = auth_a.create_token(user_id="user1")
        # token from A should not verify on B
        assert auth_b.verify_token(token_a) is None

    def test_ephemeral_secret_still_works_for_own_tokens(self):
        """Even with ephemeral secret, create→verify on same instance works."""
        auth = JWTAuth()
        token = auth.create_token(user_id="user1")
        assert auth.verify_token(token) is not None


# ── Error: _get_jwt_auth fail-fast ──────────────────────────────


class TestGetJwtAuthFailFast:
    """_get_jwt_auth must raise ConfigError when type=jwt but secret is empty."""

    def test_raises_config_error_on_empty_secret(self):
        from agentbase.api import _get_jwt_auth
        from agentbase.runtime.errors import ConfigError

        class FakeConfig:
            class auth:
                type = "jwt"
                secret = ""
                token_expiry_hours = 24
                role_permissions = {}

        with pytest.raises(ConfigError) as exc_info:
            _get_jwt_auth(FakeConfig())
        assert "AGENTBASE_CONFIG_002" in exc_info.value.code
        assert "secret" in str(exc_info.value).lower()

    def test_returns_none_for_api_key_mode(self):
        from agentbase.api import _get_jwt_auth

        class FakeConfig:
            class auth:
                type = "api_key"
                secret = ""
                token_expiry_hours = 24
                role_permissions = {}

        assert _get_jwt_auth(FakeConfig()) is None

    def test_returns_none_for_none_mode(self):
        from agentbase.api import _get_jwt_auth

        class FakeConfig:
            auth = None

        assert _get_jwt_auth(FakeConfig()) is None

    def test_returns_jwt_auth_with_explicit_secret(self):
        from agentbase.api import _get_jwt_auth
        from agentbase.extensions.auth import JWTAuth

        class FakeConfig:
            class auth:
                type = "jwt"
                secret = "explicit-strong-secret"
                token_expiry_hours = 12
                role_permissions = {}

        result = _get_jwt_auth(FakeConfig())
        assert isinstance(result, JWTAuth)
