"""Unit tests for extensions/auth.py — JWTAuth, Role, Permission.

Tests cover:
- JWTAuth init (with/without secret, custom expiry, custom role_permissions)
- create_token (roles, extra_claims, default roles)
- verify_token (valid, invalid signature, expired, revoked, malformed)
- create_refresh_token
- create_token_pair
- refresh_token (success, wrong type, invalid)
- revoke_token (success, malformed, no jti)
- cleanup_revoked
- has_permission (admin wildcard, user, readonly, None payload, unknown role)
- check_path_permission (matching, non-matching GET, non-matching non-GET)
- _b64_encode / _b64_decode roundtrip
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from agentbase.extensions.auth import (
    DEFAULT_ROLE_PERMISSIONS,
    PATH_PERMISSIONS,
    JWTAuth,
    Permission,
    Role,
)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestJWTAuthInit:
    def test_with_secret(self):
        auth = JWTAuth(secret="my-secret")
        assert auth._secret == b"my-secret"

    def test_empty_secret_generates_random(self):
        auth = JWTAuth(secret="")
        assert len(auth._secret) > 0
        # Should be different each time
        auth2 = JWTAuth(secret="")
        assert auth._secret != auth2._secret

    def test_custom_expiry(self):
        auth = JWTAuth(secret="s", token_expiry_hours=2, refresh_expiry_hours=48)
        assert auth._expiry == 2 * 3600
        assert auth._refresh_expiry == 48 * 3600

    def test_custom_role_permissions(self):
        custom = {"custom_role": ["read", "write"]}
        auth = JWTAuth(secret="s", role_permissions=custom)
        assert auth._role_permissions is custom


# ---------------------------------------------------------------------------
# create_token & verify_token
# ---------------------------------------------------------------------------


class TestCreateAndVerifyToken:
    def test_create_and_verify_roundtrip(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1", roles=[Role.USER])
        payload = auth.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"
        assert "user" in payload["roles"]
        assert "jti" in payload
        assert "exp" in payload

    def test_create_token_with_string_roles(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1", roles=["admin"])
        payload = auth.verify_token(token)
        assert payload is not None
        assert "admin" in payload["roles"]

    def test_create_token_default_role(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        payload = auth.verify_token(token)
        assert payload is not None
        assert "user" in payload["roles"]

    def test_create_token_with_extra_claims(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1", extra_claims={"company": "acme"})
        payload = auth.verify_token(token)
        assert payload is not None
        assert payload["company"] == "acme"

    def test_verify_invalid_token_returns_none(self):
        auth = JWTAuth(secret="secret")
        assert auth.verify_token("not.a.valid.token") is None

    def test_verify_wrong_number_of_parts(self):
        auth = JWTAuth(secret="secret")
        assert auth.verify_token("onlyonepart") is None
        assert auth.verify_token("two.parts") is None
        assert auth.verify_token("four.parts.here.now") is None

    def test_verify_tampered_signature(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        parts = token.split(".")
        parts[2] = "AAAA"  # Tamper signature
        tampered = ".".join(parts)
        assert auth.verify_token(tampered) is None

    def test_verify_expired_token(self):
        auth = JWTAuth(secret="secret", token_expiry_hours=0)
        # Token expires immediately
        token = auth.create_token(user_id="u1")
        # Wait a tiny bit to ensure expiry
        with patch("time.time", return_value=time.time() + 1):
            payload = auth.verify_token(token)
            assert payload is None

    def test_verify_revoked_token(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        assert auth.revoke_token(token) is True
        assert auth.verify_token(token) is None

    def test_verify_token_wrong_secret(self):
        auth1 = JWTAuth(secret="secret1")
        auth2 = JWTAuth(secret="secret2")
        token = auth1.create_token(user_id="u1")
        assert auth2.verify_token(token) is None

    def test_verify_token_payload_decode_error(self):
        """Token with valid structure but invalid payload base64 should return None."""
        auth = JWTAuth(secret="secret")
        # Create a token with valid header but corrupted payload
        import hmac as _hmac
        import hashlib as _hashlib
        header_b64 = auth._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}))
        # Invalid payload that will fail JSON decode
        payload_b64 = auth._b64_encode(b"!!!not valid json!!!")
        signing_input = f"{header_b64}.{payload_b64}"
        sig = _hmac.new(auth._secret, signing_input.encode(), _hashlib.sha256).digest()
        sig_b64 = auth._b64_encode(sig)
        token = f"{signing_input}.{sig_b64}"
        # Should hit the except Exception branch in verify_token
        assert auth.verify_token(token) is None


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


class TestRefreshToken:
    def test_create_and_refresh(self):
        auth = JWTAuth(secret="secret")
        access, refresh = auth.create_token_pair(user_id="u1", roles=[Role.USER])
        assert access is not None
        assert refresh is not None

        new_access = auth.refresh_token(refresh)
        assert new_access is not None
        payload = auth.verify_token(new_access)
        assert payload is not None
        assert payload["sub"] == "u1"

    def test_refresh_with_access_token_fails(self):
        auth = JWTAuth(secret="secret")
        access, _ = auth.create_token_pair(user_id="u1")
        # Using access token (not refresh) should fail
        result = auth.refresh_token(access)
        assert result is None

    def test_refresh_invalid_token(self):
        auth = JWTAuth(secret="secret")
        assert auth.refresh_token("invalid") is None

    def test_create_refresh_token_directly(self):
        auth = JWTAuth(secret="secret")
        refresh = auth.create_refresh_token(user_id="u1", roles=[Role.ADMIN])
        payload = auth.verify_token(refresh)
        assert payload is not None
        assert payload["typ"] == "refresh"

    def test_create_refresh_token_with_extra_claims(self):
        auth = JWTAuth(secret="secret")
        refresh = auth.create_refresh_token(
            user_id="u1", extra_claims={"session": "abc"}
        )
        payload = auth.verify_token(refresh)
        assert payload is not None
        assert payload["session"] == "abc"


# ---------------------------------------------------------------------------
# revoke_token
# ---------------------------------------------------------------------------


class TestRevokeToken:
    def test_revoke_valid_token(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        assert auth.revoke_token(token) is True
        # Revoking again should still return True (idempotent)
        assert auth.revoke_token(token) is True

    def test_revoke_malformed_token(self):
        auth = JWTAuth(secret="secret")
        assert auth.revoke_token("not.valid") is False

    def test_revoke_token_without_jti(self):
        auth = JWTAuth(secret="secret")
        # Create a fake token without jti
        header = auth._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}))
        payload = auth._b64_encode(json.dumps({"sub": "u1"}))
        sig = auth._b64_encode(b"fakesig")
        token = f"{header}.{payload}.{sig}"
        assert auth.revoke_token(token) is False

    def test_revoke_token_payload_decode_error(self):
        """Token with invalid payload base64 should return False via except branch."""
        auth = JWTAuth(secret="secret")
        header = auth._b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}))
        # Invalid payload that will fail JSON decode
        payload_b64 = auth._b64_encode(b"!!!not valid json!!!")
        sig = auth._b64_encode(b"fakesig")
        token = f"{header}.{payload_b64}.{sig}"
        # Should hit the except Exception branch in revoke_token
        assert auth.revoke_token(token) is False


# ---------------------------------------------------------------------------
# cleanup_revoked
# ---------------------------------------------------------------------------


class TestCleanupRevoked:
    def test_cleanup_under_limit(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        auth.revoke_token(token)
        auth.cleanup_revoked(max_entries=10000)
        # Token should still be revoked
        assert auth.verify_token(token) is None

    def test_cleanup_over_limit_clears(self):
        auth = JWTAuth(secret="secret")
        token = auth.create_token(user_id="u1")
        auth.revoke_token(token)
        auth.cleanup_revoked(max_entries=0)  # 1 > 0, triggers clear
        # Token should now be valid again
        assert auth.verify_token(token) is not None


# ---------------------------------------------------------------------------
# has_permission
# ---------------------------------------------------------------------------


class TestHasPermission:
    def test_admin_has_all(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["admin"]}
        assert auth.has_permission(payload, Permission.READ)
        assert auth.has_permission(payload, Permission.WRITE)
        assert auth.has_permission(payload, Permission.DELETE)
        assert auth.has_permission(payload, Permission.ADMIN)

    def test_user_permissions(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert auth.has_permission(payload, Permission.READ)
        assert auth.has_permission(payload, Permission.WRITE)
        assert auth.has_permission(payload, Permission.INVOKE)
        assert not auth.has_permission(payload, Permission.DELETE)
        assert not auth.has_permission(payload, Permission.ADMIN)

    def test_readonly_permissions(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["readonly"]}
        assert auth.has_permission(payload, Permission.READ)
        assert not auth.has_permission(payload, Permission.WRITE)
        assert not auth.has_permission(payload, Permission.DELETE)

    def test_none_payload(self):
        auth = JWTAuth(secret="secret")
        assert not auth.has_permission(None, Permission.READ)

    def test_unknown_role(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["unknown_role"]}
        assert not auth.has_permission(payload, Permission.READ)

    def test_custom_role_permissions(self):
        custom = {"custom": ["read", "invoke"]}
        auth = JWTAuth(secret="s", role_permissions=custom)
        payload = {"roles": ["custom"]}
        assert auth.has_permission(payload, Permission.READ)
        assert auth.has_permission(payload, Permission.INVOKE)
        assert not auth.has_permission(payload, Permission.WRITE)


# ---------------------------------------------------------------------------
# check_path_permission
# ---------------------------------------------------------------------------


class TestCheckPathPermission:
    def test_admin_can_access_anything(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["admin"]}
        assert auth.check_path_permission(payload, "GET", "/agents")
        assert auth.check_path_permission(payload, "POST", "/agents/chat")
        assert auth.check_path_permission(payload, "DELETE", "/agents/123")

    def test_user_can_read_agents(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert auth.check_path_permission(payload, "GET", "/agents")
        assert auth.check_path_permission(payload, "GET", "/agents/123")

    def test_user_can_invoke(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert auth.check_path_permission(payload, "POST", "/agents/chat")

    def test_user_cannot_delete(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert not auth.check_path_permission(payload, "DELETE", "/agents/123")

    def test_readonly_cannot_write(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["readonly"]}
        assert not auth.check_path_permission(payload, "POST", "/documents/upload")

    def test_readonly_can_read(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["readonly"]}
        assert auth.check_path_permission(payload, "GET", "/documents")

    def test_unknown_path_get_allows_read(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert auth.check_path_permission(payload, "GET", "/unknown/path")

    def test_unknown_path_non_get_requires_admin(self):
        auth = JWTAuth(secret="secret")
        payload = {"roles": ["user"]}
        assert not auth.check_path_permission(payload, "PUT", "/unknown/path")

    def test_none_payload_denies(self):
        auth = JWTAuth(secret="secret")
        assert not auth.check_path_permission(None, "GET", "/agents")


# ---------------------------------------------------------------------------
# b64 encode/decode
# ---------------------------------------------------------------------------


class TestB64:
    def test_encode_string(self):
        result = JWTAuth._b64_encode("hello")
        assert isinstance(result, str)
        assert JWTAuth._b64_decode(result) == b"hello"

    def test_encode_bytes(self):
        result = JWTAuth._b64_encode(b"hello")
        assert isinstance(result, str)
        assert JWTAuth._b64_decode(result) == b"hello"

    def test_roundtrip_binary(self):
        data = b"\x00\xff\xfe\x01"
        encoded = JWTAuth._b64_encode(data)
        assert JWTAuth._b64_decode(encoded) == data

    def test_decode_with_padding(self):
        # Test that decode adds correct padding
        encoded = JWTAuth._b64_encode(b"test")
        # _b64_encode strips padding, _b64_decode should add it back
        decoded = JWTAuth._b64_decode(encoded)
        assert decoded == b"test"
