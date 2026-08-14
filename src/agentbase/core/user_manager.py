"""User management service — CRUD, authentication, and password hashing.

Provides a pluggable user management system that allows users to:
- Register users with username/email and hashed passwords
- Query, update, and delete user accounts at runtime
- Authenticate users by verifying credentials
- List all registered users for admin UIs
- Manage user roles (admin / user / readonly)
- Enable / disable user accounts

Pluggable storage:
- ``InMemoryUserProvider`` (default) — zero-config, in-process
- ``NullUserProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_user_provider("name")``

Usage::

    from agentbase.core.user_manager import UserManager, UserEntry

    manager = UserManager(provider="memory", enabled=True)

    user = manager.register(UserEntry(
        username="alice",
        email="alice@example.com",
        password="secret123",
        roles=["user"],
    ))

    # Authenticate
    authed = manager.authenticate("alice", "secret123")
    # → UserEntry if success, None if failure

    # List all
    all_users = manager.list()
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, salted)
# ---------------------------------------------------------------------------

def hash_password(password: str, *, rounds: int = 100_000) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt.

    Returns a string in the format ``pbkdf2_sha256$<rounds>$<salt_hex>$<hash_hex>``
    which can be stored and later verified with ``verify_password()``.

    Args:
        password: The plaintext password to hash.
        rounds: PBKDF2 iteration count (default 100k).

    Returns:
        The hashed password string.
    """
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored hash.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        password: The plaintext password to check.
        hashed: The stored hash string from ``hash_password()``.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.
    """
    try:
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        rounds = int(parts[1])
        salt = parts[2]
        expected_hash = parts[3]
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
        return hmac.compare_digest(dk.hex(), expected_hash)
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class UserEntry:
    """A registered user account.

    Attributes:
        username: Unique username (identifier for login and reference).
        email: User's email address.
        password_hash: Hashed password (never store plaintext).
        roles: List of role names (e.g. ``["admin"]``, ``["user"]``).
        enabled: Whether this account can log in.
        created_at: ISO 8601 UTC timestamp (auto-set on register).
        updated_at: ISO 8601 UTC timestamp (auto-set on update).
        last_login_at: ISO 8601 UTC timestamp of last successful login ("" if never).
        metadata: Arbitrary metadata (display name, avatar URL, etc.).
    """

    username: str
    email: str = ""
    password_hash: str = ""
    roles: list[str] = field(default_factory=lambda: ["user"])
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_login_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_hash: bool = False) -> dict[str, Any]:
        """Serialise the user.  By default, the password hash is excluded."""
        d = {
            "username": self.username,
            "email": self.email,
            "roles": self.roles,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
            "metadata": self.metadata,
        }
        if include_hash:
            d["password_hash"] = self.password_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserEntry:
        """Create a UserEntry from a dict, ignoring unknown keys."""
        known_fields = {
            "username", "email", "password_hash", "roles",
            "enabled", "created_at", "updated_at", "last_login_at",
            "metadata",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class UserProvider(Protocol):
    """Protocol for user storage providers.

    Implementations must be thread-safe.
    """

    def register(self, user: UserEntry) -> UserEntry:
        """Register or replace a user. Returns the stored entry."""
        ...

    def get(self, username: str) -> UserEntry | None:
        """Get a user by username. Returns None if not found."""
        ...

    def get_by_email(self, email: str) -> UserEntry | None:
        """Get a user by email. Returns None if not found."""
        ...

    def list(self) -> list[UserEntry]:
        """List all registered users."""
        ...

    def update(self, username: str, changes: dict[str, Any]) -> UserEntry | None:
        """Update fields on an existing user. Returns the updated entry or None."""
        ...

    def delete(self, username: str) -> bool:
        """Delete a user. Returns True if deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullUserProvider:
    """No-op user provider — stores nothing.

    Used when user management is disabled (``user_manager.enabled=false``).
    """

    def register(self, user: UserEntry) -> UserEntry:
        return user

    def get(self, username: str) -> UserEntry | None:
        return None

    def get_by_email(self, email: str) -> UserEntry | None:
        return None

    def list(self) -> list[UserEntry]:
        return []

    def update(self, username: str, changes: dict[str, Any]) -> UserEntry | None:
        return None

    def delete(self, username: str) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryUserProvider:
    """In-memory user provider — zero-config, process-local, thread-safe.

    Users are stored in dicts and lost on process restart.  Suitable for
    development, testing, and single-instance deployments.

    For production multi-instance setups, implement a storage-backed
    provider (PostgreSQL, Redis, etc.) and register it with
    ``@register_user_provider("name")``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, UserEntry] = {}
        self._email_index: dict[str, str] = {}  # email → username

    def register(self, user: UserEntry) -> UserEntry:
        with self._lock:
            if not user.username:
                raise ValueError("Username cannot be empty")
            now = _now()
            existing = self._users.get(user.username)
            if existing is not None:
                user.created_at = existing.created_at
                # Remove old email index if email is changing
                if existing.email and existing.email != user.email:
                    self._email_index.pop(existing.email, None)
            user.updated_at = now
            self._users[user.username] = user
            if user.email:
                self._email_index[user.email] = user.username
            logger.info(
                "User registered: %s (email=%s, roles=%s)",
                user.username, user.email, user.roles,
                extra={
                    "event": "user.register",
                    "user": user.username,
                },
            )
            return user

    def get(self, username: str) -> UserEntry | None:
        with self._lock:
            return self._users.get(username)

    def get_by_email(self, email: str) -> UserEntry | None:
        with self._lock:
            username = self._email_index.get(email)
            if username is None:
                return None
            return self._users.get(username)

    def list(self) -> list[UserEntry]:
        with self._lock:
            return list(self._users.values())

    def update(self, username: str, changes: dict[str, Any]) -> UserEntry | None:
        with self._lock:
            existing = self._users.get(username)
            if existing is None:
                return None
            data = existing.to_dict(include_hash=True)
            old_email = existing.email
            for key, value in changes.items():
                if key in data and key != "username" and key != "created_at":
                    data[key] = value
            data["updated_at"] = _now()
            updated = UserEntry.from_dict(data)
            self._users[username] = updated
            # Update email index
            if old_email and old_email != updated.email:
                self._email_index.pop(old_email, None)
            if updated.email:
                self._email_index[updated.email] = username
            logger.info(
                "User updated: %s (fields: %s)",
                username, list(changes.keys()),
                extra={"event": "user.update", "user": username},
            )
            return updated

    def delete(self, username: str) -> bool:
        with self._lock:
            user = self._users.get(username)
            if user is None:
                return False
            if user.email:
                self._email_index.pop(user.email, None)
            del self._users[username]
            logger.info(
                "User deleted: %s", username,
                extra={"event": "user.delete", "user": username},
            )
            return True

    def close(self) -> None:
        with self._lock:
            count = len(self._users)
            self._users.clear()
            self._email_index.clear()
            if count:
                logger.info("User provider closed: %d entries cleared", count)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_user_registry: dict[str, Callable[..., UserProvider]] = {}
_user_registry_lock = threading.Lock()


def register_user_provider(name: str) -> Callable[[Callable], Callable]:
    """Decorator to register a user provider implementation.

    Usage::

        @register_user_provider("redis")
        class RedisUserProvider:
            def __init__(self, **kwargs):
                ...
    """

    def decorator(cls: Callable) -> Callable:
        with _user_registry_lock:
            _user_registry[name] = cls
        logger.debug("User provider registered: %s -> %s", name, cls.__name__)
        return cls

    return decorator


def get_user_provider(name: str, **kwargs: Any) -> UserProvider:
    """Get a user provider instance by name.

    Raises RegistryError if the provider is not found.
    """
    with _user_registry_lock:
        factory = _user_registry.get(name)
    if factory is None:
        raise RegistryError(
            f"Unknown user provider: '{name}'. "
            f"Available: {', '.join(sorted(_user_registry.keys())) or 'none'}",
            code="AGENTBASE_REG_001",
        )
    return factory(**kwargs)


def list_user_providers() -> list[str]:
    """List all registered user provider names."""
    with _user_registry_lock:
        return sorted(_user_registry.keys())


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class UserManager:
    """High-level user management service.

    Wraps a ``UserProvider`` instance and provides:
    - CRUD operations (register / get / list / update / delete)
    - Authentication (verify username + password)
    - Password hashing (PBKDF2-HMAC-SHA256)

    Configuration::

        user_manager:
          enabled: false  # default off
          provider: memory
    """

    def __init__(
        self,
        *,
        provider: str = "memory",
        enabled: bool = False,
        **kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: UserProvider = NullUserProvider()
            logger.info("User manager disabled (NullUserProvider)")
        else:
            try:
                self._provider = get_user_provider(provider, **kwargs)
            except RegistryError:
                logger.warning(
                    "User provider '%s' not found, falling back to NullUserProvider",
                    provider,
                )
                self._provider = NullUserProvider()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> UserProvider:
        return self._provider

    def register(
        self,
        *,
        username: str,
        email: str = "",
        password: str = "",
        roles: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserEntry:
        """Register a new user or replace an existing one.

        The password is hashed with PBKDF2 before storage.
        If a user with the same username exists, it is updated.

        Args:
            username: Unique username.
            email: User's email (optional).
            password: Plaintext password (will be hashed).
            roles: Role names (default ``["user"]``).
            metadata: Extra metadata.

        Returns:
            The stored UserEntry (with password_hash, not plaintext).
        """
        user = UserEntry(
            username=username,
            email=email,
            password_hash=hash_password(password) if password else "",
            roles=roles or ["user"],
            metadata=metadata or {},
        )
        return self._provider.register(user)

    def get(self, username: str) -> UserEntry | None:
        """Get a user by username."""
        return self._provider.get(username)

    def get_by_email(self, email: str) -> UserEntry | None:
        """Get a user by email address."""
        return self._provider.get_by_email(email)

    def list(self) -> list[UserEntry]:
        """List all registered users."""
        return self._provider.list()

    def update(self, username: str, changes: dict[str, Any]) -> UserEntry | None:
        """Update fields on an existing user.

        If ``password`` is in changes, it is hashed before storage.
        """
        processed = dict(changes)
        if "password" in processed:
            raw_pw = processed.pop("password")
            processed["password_hash"] = hash_password(raw_pw) if raw_pw else ""
        return self._provider.update(username, processed)

    def delete(self, username: str) -> bool:
        """Delete a user."""
        return self._provider.delete(username)

    def authenticate(self, username: str, password: str) -> UserEntry | None:
        """Authenticate a user by username and password.

        On success, updates ``last_login_at`` and returns the user.
        On failure (wrong password, disabled, not found), returns ``None``.

        Args:
            username: The username to authenticate.
            password: The plaintext password to verify.

        Returns:
            The UserEntry if authentication succeeds, ``None`` otherwise.
        """
        user = self._provider.get(username)
        if user is None:
            logger.info(
                "Authentication failed (user not found): %s", username,
                extra={"event": "user.auth_failed", "user": username, "reason": "not_found"},
            )
            return None
        if not user.enabled:
            logger.info(
                "Authentication failed (account disabled): %s", username,
                extra={"event": "user.auth_failed", "user": username, "reason": "disabled"},
            )
            return None
        if not user.password_hash:
            logger.info(
                "Authentication failed (no password set): %s", username,
                extra={"event": "user.auth_failed", "user": username, "reason": "no_password"},
            )
            return None
        if not verify_password(password, user.password_hash):
            logger.info(
                "Authentication failed (wrong password): %s", username,
                extra={"event": "user.auth_failed", "user": username, "reason": "wrong_password"},
            )
            return None
        # Update last_login_at
        self._provider.update(username, {"last_login_at": _now()})
        logger.info(
            "Authentication succeeded: %s", username,
            extra={"event": "user.auth_success", "user": username},
        )
        return self._provider.get(username)

    def change_password(self, username: str, new_password: str) -> bool:
        """Change a user's password.

        Args:
            username: The username.
            new_password: The new plaintext password.

        Returns:
            ``True`` if the password was changed, ``False`` if user not found.
        """
        result = self._provider.update(username, {
            "password_hash": hash_password(new_password) if new_password else "",
        })
        if result is not None:
            logger.info(
                "Password changed for user: %s", username,
                extra={"event": "user.password_changed", "user": username},
            )
            return True
        return False

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Default singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_default_manager: UserManager | None = None
_default_manager_lock = threading.Lock()


def get_user_manager() -> UserManager:
    """Get the default UserManager singleton.

    The singleton is lazily initialised as disabled (NullUserProvider)
    on first access.  Call ``set_user_manager()`` to configure it.
    """
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = UserManager(enabled=False)
    return _default_manager


def set_user_manager(manager: UserManager) -> None:
    """Set the global UserManager singleton."""
    global _default_manager
    with _default_manager_lock:
        _default_manager = manager


# ---------------------------------------------------------------------------
# Register default providers
# ---------------------------------------------------------------------------

@register_user_provider("memory")
def _make_in_memory_provider(**kwargs: Any) -> InMemoryUserProvider:
    """Factory for InMemoryUserProvider."""
    return InMemoryUserProvider()


@register_user_provider("null")
def _make_null_provider(**kwargs: Any) -> NullUserProvider:
    """Factory for NullUserProvider."""
    return NullUserProvider()
