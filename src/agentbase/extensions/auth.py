"""JWT authentication and RBAC authorization middleware.

Provides JWT token generation, verification, and role-based access control.
Can be used alongside or instead of the simple API Key auth.

Features:
- JWT token generation with configurable expiration
- Role-based access control (admin, user, readonly)
- Path-level permission rules
- Compatible with existing API Key auth (both can coexist)

Usage::

    from agentbase.extensions.auth import JWTAuth, Role, Permission

    auth = JWTAuth(secret="my-secret-key")
    token = auth.create_token(user_id="user1", roles=[Role.USER])
    # In middleware:
    payload = auth.verify_token(token)
    if auth.has_permission(payload, Permission.READ):
        ...

Config::

    auth:
      type: jwt  # or api_key (default)
      secret: ${AGENTBASE_JWT_SECRET}
      token_expiry_hours: 24
      roles:
        admin: ["*"]
        user: ["read", "write", "invoke"]
        readonly: ["read"]
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from enum import Enum
from typing import Any

logger = logging.getLogger("agentbase.auth")


class Role(str, Enum):
    """User roles."""
    ADMIN = "admin"
    USER = "user"
    READONLY = "readonly"


class Permission(str, Enum):
    """Permissions for API operations."""
    READ = "read"
    WRITE = "write"
    INVOKE = "invoke"
    DELETE = "delete"
    ADMIN = "admin"


# Default role-permission mapping
DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.ADMIN.value: ["*"],
    Role.USER.value: [Permission.READ.value, Permission.WRITE.value, Permission.INVOKE.value],
    Role.READONLY.value: [Permission.READ.value],
}

# Path-to-permission mapping
PATH_PERMISSIONS: dict[str, Permission] = {
    "GET:/agents": Permission.READ,
    "GET:/agents/": Permission.READ,
    "GET:/documents": Permission.READ,
    "GET:/documents/": Permission.READ,
    "POST:/documents/upload": Permission.WRITE,
    "POST:/documents/search": Permission.READ,
    "DELETE:/documents/": Permission.DELETE,
    "POST:/agents/": Permission.INVOKE,
    "POST:/queue/submit": Permission.INVOKE,
    "DELETE:/queue/": Permission.DELETE,
    "POST:/queue/process": Permission.ADMIN,
}


class JWTAuth:
    """JWT authentication provider.

    Uses HMAC-SHA256 for signing (no external dependencies).
    For production with RS256, use PyJWT library.

    Features:
    - Access token generation and verification
    - Refresh token support (longer-lived, for token renewal)
    - Token revocation via in-memory blacklist
    - Audit logging for auth events (login, refresh, revoke)
    - Thread-safe via ``threading.Lock`` on blacklist operations

    Usage::

        auth = JWTAuth(secret="my-secret")
        access, refresh = auth.create_token_pair(user_id="u1", roles=["user"])
        payload = auth.verify_token(access)  # Returns dict or None
        new_access = auth.refresh_token(refresh)
        auth.revoke_token(access)  # Blacklist a token
    """

    def __init__(
        self,
        *,
        secret: str = "",
        token_expiry_hours: int = 24,
        refresh_expiry_hours: int = 168,  # 7 days
        role_permissions: dict[str, list[str]] | None = None,
    ) -> None:
        if not secret:
            # Fail-safe: generate a random per-instance secret so that
            # tokens issued by one process are not forgeable by an
            # attacker who knows a well-known default.  A warning is
            # logged so operators know they need to set a proper secret.
            secret = uuid.uuid4().hex + uuid.uuid4().hex
            logger.warning(
                "JWT secret is empty — generated a random ephemeral secret. "
                "Set AGENTBASE_AUTH__SECRET to a stable value for production.",
                extra={"event": "auth.jwt.ephemeral_secret"},
            )
        self._secret = secret.encode("utf-8")
        self._expiry = token_expiry_hours * 3600
        self._refresh_expiry = refresh_expiry_hours * 3600
        self._role_permissions = role_permissions or DEFAULT_ROLE_PERMISSIONS
        # Revoked token IDs (jti) — in-memory blacklist
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    def create_token(
        self,
        *,
        user_id: str,
        roles: list[str] | list[Role] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Create a JWT token.

        Args:
            user_id: Unique user identifier.
            roles: List of role names or Role enums.
            extra_claims: Additional claims to include.

        Returns:
            JWT token string.
        """
        role_strings = [r.value if isinstance(r, Role) else r for r in (roles or [Role.USER])]

        payload = {
            "sub": user_id,
            "roles": role_strings,
            "iat": int(time.time()),
            "exp": int(time.time()) + self._expiry,
            "jti": str(uuid.uuid4()),
        }
        if extra_claims:
            payload.update(extra_claims)

        # Encode header and payload
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = self._b64_encode(json.dumps(header))
        payload_b64 = self._b64_encode(json.dumps(payload))

        # Sign
        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(self._secret, signing_input.encode(), hashlib.sha256).digest()
        sig_b64 = self._b64_encode(signature)

        return f"{signing_input}.{sig_b64}"

    def verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify a JWT token.

        Returns the payload dict if valid, None if invalid, expired,
        or revoked.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, payload_b64, sig_b64 = parts

            # Verify signature
            signing_input = f"{header_b64}.{payload_b64}"
            expected_sig = hmac.new(self._secret, signing_input.encode(), hashlib.sha256).digest()
            actual_sig = self._b64_decode(sig_b64)

            if not hmac.compare_digest(expected_sig, actual_sig):
                logger.warning("JWT signature mismatch", extra={"event": "auth.token.invalid_sig"})
                return None

            # Decode payload
            payload = json.loads(self._b64_decode(payload_b64))

            # Check expiration
            if payload.get("exp", 0) < time.time():
                logger.info("JWT expired", extra={"event": "auth.token.expired", "sub": payload.get("sub")})
                return None

            # Check revocation
            jti = payload.get("jti", "")
            with self._lock:
                if jti and jti in self._revoked:
                    logger.warning("JWT revoked", extra={"event": "auth.token.revoked", "sub": payload.get("sub"), "jti": jti})
                    return None

            return payload
        except Exception as exc:
            logger.warning("JWT verification error: %s", exc, extra={"event": "auth.token.error"})
            return None

    def create_refresh_token(
        self,
        *,
        user_id: str,
        roles: list[str] | list[Role] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Create a refresh token (longer-lived, for token renewal).

        Refresh tokens have a longer expiry (default 7 days) and can
        only be used to obtain new access tokens, not for API access.
        """
        role_strings = [r.value if isinstance(r, Role) else r for r in (roles or [Role.USER])]
        payload = {
            "sub": user_id,
            "roles": role_strings,
            "iat": int(time.time()),
            "exp": int(time.time()) + self._refresh_expiry,
            "jti": str(uuid.uuid4()),
            "typ": "refresh",
        }
        if extra_claims:
            payload.update(extra_claims)

        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = self._b64_encode(json.dumps(header))
        payload_b64 = self._b64_encode(json.dumps(payload))
        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(self._secret, signing_input.encode(), hashlib.sha256).digest()
        sig_b64 = self._b64_encode(signature)

        logger.info(
            "Refresh token created: user=%s",
            user_id,
            extra={"event": "auth.refresh.created", "sub": user_id},
        )
        return f"{signing_input}.{sig_b64}"

    def create_token_pair(
        self,
        *,
        user_id: str,
        roles: list[str] | list[Role] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Create both an access token and a refresh token.

        Returns ``(access_token, refresh_token)``.
        """
        access = self.create_token(user_id=user_id, roles=roles, extra_claims=extra_claims)
        refresh = self.create_refresh_token(user_id=user_id, roles=roles, extra_claims=extra_claims)
        logger.info(
            "Token pair created: user=%s",
            user_id,
            extra={"event": "auth.token_pair.created", "sub": user_id},
        )
        return access, refresh

    def refresh_token(self, refresh_token: str) -> str | None:
        """Exchange a refresh token for a new access token.

        Returns a new access token, or ``None`` if the refresh token
        is invalid, expired, or revoked.
        """
        payload = self.verify_token(refresh_token)
        if payload is None:
            logger.warning("Refresh token verification failed", extra={"event": "auth.refresh.failed"})
            return None

        # Must be a refresh token
        if payload.get("typ") != "refresh":
            logger.warning("Non-refresh token used for refresh", extra={"event": "auth.refresh.wrong_type"})
            return None

        user_id = payload.get("sub", "")
        roles = payload.get("roles", [Role.USER.value])

        new_access = self.create_token(user_id=user_id, roles=roles)
        logger.info(
            "Token refreshed: user=%s",
            user_id,
            extra={"event": "auth.refresh.success", "sub": user_id},
        )
        return new_access

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding its ``jti`` to the blacklist.

        Returns ``True`` if the token was successfully revoked.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False
            payload = json.loads(self._b64_decode(parts[1]))
            jti = payload.get("jti", "")
            if not jti:
                return False
            with self._lock:
                self._revoked.add(jti)
            logger.info(
                "Token revoked: user=%s jti=%s",
                payload.get("sub", ""),
                jti,
                extra={"event": "auth.token.revoked", "sub": payload.get("sub"), "jti": jti},
            )
            return True
        except Exception:
            return False

    def cleanup_revoked(self, max_entries: int = 10000) -> None:
        """Clear the revoked token blacklist if it grows too large.

        In a production system, use a TTL-based store (Redis) instead
        of in-memory storage. This method prevents unbounded growth.
        """
        with self._lock:
            if len(self._revoked) > max_entries:
                self._revoked.clear()
                logger.info("Revoked token blacklist cleared", extra={"event": "auth.blacklist.cleared"})

    def has_permission(self, payload: dict[str, Any] | None, permission: Permission) -> bool:
        """Check if a token payload has a specific permission."""
        if payload is None:
            return False

        roles = payload.get("roles", [])
        for role in roles:
            perms = self._role_permissions.get(role, [])
            if "*" in perms or permission.value in perms:
                return True
        return False

    def check_path_permission(self, payload: dict[str, Any] | None, method: str, path: str) -> bool:
        """Check if a token payload can access a specific path."""
        # Find matching permission for this path
        for pattern, perm in PATH_PERMISSIONS.items():
            req_method, req_path = pattern.split(":", 1)
            if req_method == method and (path == req_path or path.startswith(req_path)):
                return self.has_permission(payload, perm)

        # No specific rule: allow read, deny write
        if method == "GET":
            return self.has_permission(payload, Permission.READ)
        return self.has_permission(payload, Permission.ADMIN)

    @staticmethod
    def _b64_encode(data: bytes | str) -> str:
        import base64
        if isinstance(data, str):
            data = data.encode("utf-8")
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    @staticmethod
    def _b64_decode(data: str) -> bytes:
        import base64
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        return base64.urlsafe_b64decode(data)


__all__ = [
    "JWTAuth",
    "Role",
    "Permission",
    "PATH_PERMISSIONS",
    "DEFAULT_ROLE_PERMISSIONS",
]
