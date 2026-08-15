"""API Key management service — CRUD, generation, and verification.

Provides a pluggable API Key management system that allows users to:
- Generate API keys with configurable prefixes, roles, and expiration
- Query, update, and delete API keys at runtime
- Revoke (deactivate) keys without deleting them
- Track usage statistics (last used timestamp, total call count)
- Bind keys to specific users or applications for fine-grained access control

Pluggable storage:
- ``InMemoryApiKeyProvider`` (default) — zero-config, in-process
- ``NullApiKeyProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_apikey_provider("name")``

Usage::

    from agentbase.core.apikey_manager import ApiKeyManager, ApiKeyEntry

    manager = ApiKeyManager(provider="memory", enabled=True)

    # Create a key
    entry, raw_key = manager.create(
        name="production-key",
        roles=["user"],
        description="Key for production app",
    )
    # raw_key is the full key string (only visible at creation time)

    # Verify a key
    verified = manager.verify(raw_key)
    # → ApiKeyEntry if valid, None if invalid/disabled/expired

    # List all
    all_keys = manager.list()
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


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Key generation and hashing
# ---------------------------------------------------------------------------

# Prefix for all generated API keys (identifies them in logs/headers)
_KEY_PREFIX = "agk_"
# Length of the random portion of the key (bytes before hex encoding)
_KEY_RANDOM_BYTES = 32  # 256 bits → 64 hex chars


def generate_api_key() -> str:
    """Generate a random API key string.

    Returns a key in the format ``agk_<64 hex chars>``, e.g.::

        agk_a1b2c3d4...

    The key is URL-safe and suitable for use in Bearer tokens or
    ``X-API-Key`` headers.
    """
    random_part = secrets.token_hex(_KEY_RANDOM_BYTES)
    return f"{_KEY_PREFIX}{random_part}"


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for secure storage.

    Uses SHA-256 with a static pepper (the key itself is already
    high-entropy, so we only need a one-way hash for lookup).

    The hash is prefixed with ``sha256$`` to allow future algorithm
    upgrades.
    """
    dk = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"sha256${dk}"


def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """Verify a raw API key against a stored hash.

    Uses constant-time comparison to prevent timing attacks.
    """
    try:
        parts = stored_hash.split("$", 1)
        if len(parts) != 2 or parts[0] != "sha256":
            return False
        expected = parts[1]
        dk = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return hmac.compare_digest(dk, expected)
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ApiKeyEntry:
    """A registered API key.

    Attributes:
        key_id: Unique identifier (short UUID for internal reference).
        name: Human-readable name for the key.
        key_hash: Hashed key string (never store the raw key).
        key_prefix: First 12 chars of the raw key for identification
            (e.g. ``agk_a1b2c3d4``). Shown in listings so users can
            identify which key is which without seeing the full key.
        roles: List of role names bound to this key (e.g. ``["admin"]``).
        user_id: Username this key is bound to (optional, for linking
            keys to user accounts).
        description: Human-readable description.
        enabled: Whether this key is active.
        expires_at: ISO 8601 UTC timestamp for expiration (empty = never).
        created_at: ISO 8601 UTC timestamp (auto-set on creation).
        updated_at: ISO 8601 UTC timestamp (auto-set on update).
        last_used_at: ISO 8601 UTC timestamp of last use (empty if never).
        call_count: Total number of successful verifications.
        metadata: Arbitrary metadata.
    """

    key_id: str
    name: str = ""
    key_hash: str = ""
    key_prefix: str = ""
    roles: list[str] = field(default_factory=lambda: ["user"])
    user_id: str = ""
    description: str = ""
    enabled: bool = True
    expires_at: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_used_at: str = ""
    call_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_hash: bool = False) -> dict[str, Any]:
        """Serialise the key entry.  By default, the key hash is excluded."""
        d = {
            "key_id": self.key_id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "roles": self.roles,
            "user_id": self.user_id,
            "description": self.description,
            "enabled": self.enabled,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "call_count": self.call_count,
            "metadata": self.metadata,
        }
        if include_hash:
            d["key_hash"] = self.key_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiKeyEntry:
        """Create an ApiKeyEntry from a dict, ignoring unknown keys."""
        known_fields = {
            "key_id", "name", "key_hash", "key_prefix", "roles",
            "user_id", "description", "enabled", "expires_at",
            "created_at", "updated_at", "last_used_at", "call_count",
            "metadata",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    @property
    def is_expired(self) -> bool:
        """Check if this key has expired."""
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            now = datetime.now(timezone.utc)
            # Handle naive vs aware datetimes
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return now >= exp
        except (ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ApiKeyProvider(Protocol):
    """Protocol for API key storage providers.

    Implementations must be thread-safe.
    """

    def create(self, entry: ApiKeyEntry) -> ApiKeyEntry:
        """Create a new key entry. Returns the stored entry."""
        ...

    def get(self, key_id: str) -> ApiKeyEntry | None:
        """Get a key by ID. Returns None if not found."""
        ...

    def get_by_hash(self, key_hash: str) -> ApiKeyEntry | None:
        """Get a key by its hash. Returns None if not found."""
        ...

    def list(self) -> list[ApiKeyEntry]:
        """List all registered keys."""
        ...

    def update(self, key_id: str, changes: dict[str, Any]) -> ApiKeyEntry | None:
        """Update fields on an existing key. Returns the updated entry or None."""
        ...

    def delete(self, key_id: str) -> bool:
        """Delete a key. Returns True if deleted."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullApiKeyProvider:
    """No-op API key provider — stores nothing.

    Used when API key management is disabled (``apikey_manager.enabled=false``).
    """

    def create(self, entry: ApiKeyEntry) -> ApiKeyEntry:
        return entry

    def get(self, key_id: str) -> ApiKeyEntry | None:
        return None

    def get_by_hash(self, key_hash: str) -> ApiKeyEntry | None:
        return None

    def list(self) -> list[ApiKeyEntry]:
        return []

    def update(self, key_id: str, changes: dict[str, Any]) -> ApiKeyEntry | None:
        return None

    def delete(self, key_id: str) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryApiKeyProvider:
    """In-memory API key provider — zero-config, process-local, thread-safe.

    Keys are stored in dicts and lost on process restart.  Suitable for
    development, testing, and single-instance deployments.

    For production multi-instance setups, implement a storage-backed
    provider (PostgreSQL, Redis, etc.) and register it with
    ``@register_apikey_provider("name")``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keys: dict[str, ApiKeyEntry] = {}  # key_id → entry
        self._hash_index: dict[str, str] = {}  # key_hash → key_id
        self._name_index: dict[str, str] = {}  # name → key_id

    def create(self, entry: ApiKeyEntry) -> ApiKeyEntry:
        with self._lock:
            if not entry.key_id:
                raise ValueError("key_id cannot be empty")
            # Check name uniqueness if name is set
            if entry.name and entry.name in self._name_index:
                raise ValueError(f"A key with name '{entry.name}' already exists")
            self._keys[entry.key_id] = entry
            if entry.key_hash:
                self._hash_index[entry.key_hash] = entry.key_id
            if entry.name:
                self._name_index[entry.name] = entry.key_id
            logger.info(
                "API key created: %s (name=%s, roles=%s)",
                entry.key_id, entry.name, entry.roles,
                extra={
                    "event": "apikey.create",
                    "key_id": entry.key_id,
                    "key_name": entry.name,
                },
            )
            return entry

    def get(self, key_id: str) -> ApiKeyEntry | None:
        with self._lock:
            return self._keys.get(key_id)

    def get_by_hash(self, key_hash: str) -> ApiKeyEntry | None:
        with self._lock:
            key_id = self._hash_index.get(key_hash)
            if key_id is None:
                return None
            return self._keys.get(key_id)

    def list(self) -> list[ApiKeyEntry]:
        with self._lock:
            return list(self._keys.values())

    def update(self, key_id: str, changes: dict[str, Any]) -> ApiKeyEntry | None:
        with self._lock:
            existing = self._keys.get(key_id)
            if existing is None:
                return None
            data = existing.to_dict(include_hash=True)
            old_name = existing.name
            for key, value in changes.items():
                if key in data and key != "key_id" and key != "created_at":
                    data[key] = value
            data["updated_at"] = _now()
            updated = ApiKeyEntry.from_dict(data)
            self._keys[key_id] = updated
            # Update name index
            if old_name and old_name != updated.name:
                self._name_index.pop(old_name, None)
            if updated.name:
                self._name_index[updated.name] = key_id
            logger.info(
                "API key updated: %s (fields: %s)",
                key_id, list(changes.keys()),
                extra={"event": "apikey.update", "key_id": key_id},
            )
            return updated

    def delete(self, key_id: str) -> bool:
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is None:
                return False
            if entry.key_hash:
                self._hash_index.pop(entry.key_hash, None)
            if entry.name:
                self._name_index.pop(entry.name, None)
            del self._keys[key_id]
            logger.info(
                "API key deleted: %s", key_id,
                extra={"event": "apikey.delete", "key_id": key_id},
            )
            return True

    def close(self) -> None:
        with self._lock:
            count = len(self._keys)
            self._keys.clear()
            self._hash_index.clear()
            self._name_index.clear()
            if count:
                logger.info("API key provider closed: %d entries cleared", count)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_apikey_registry: dict[str, Callable[..., ApiKeyProvider]] = {}
_apikey_registry_lock = threading.Lock()


def register_apikey_provider(name: str) -> Callable[[Callable], Callable]:
    """Decorator to register an API key provider implementation.

    Usage::

        @register_apikey_provider("redis")
        class RedisApiKeyProvider:
            def __init__(self, **kwargs):
                ...
    """

    def decorator(cls: Callable) -> Callable:
        with _apikey_registry_lock:
            _apikey_registry[name] = cls
        logger.debug("API key provider registered: %s -> %s", name, cls.__name__)
        return cls

    return decorator


def get_apikey_provider(name: str, **kwargs: Any) -> ApiKeyProvider:
    """Get an API key provider instance by name.

    Raises RegistryError if the provider is not found.
    """
    with _apikey_registry_lock:
        factory = _apikey_registry.get(name)
    if factory is None:
        raise RegistryError(
            f"Unknown API key provider: '{name}'. "
            f"Available: {', '.join(sorted(_apikey_registry.keys())) or 'none'}",
            code="AGENTBASE_REG_001",
        )
    return factory(**kwargs)


def list_apikey_providers() -> list[str]:
    """List all registered API key provider names."""
    with _apikey_registry_lock:
        return sorted(_apikey_registry.keys())


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ApiKeyManager:
    """High-level API key management service.

    Wraps an ``ApiKeyProvider`` instance and provides:
    - Key generation (cryptographically random)
    - CRUD operations (create / get / list / update / delete)
    - Key verification (check raw key against stored hash)
    - Usage tracking (last used timestamp, call count)
    - Key revocation (disable without deleting)

    Configuration::

        apikey_manager:
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
            self._provider: ApiKeyProvider = NullApiKeyProvider()
            logger.info("API key manager disabled (NullApiKeyProvider)")
        else:
            try:
                self._provider = get_apikey_provider(provider, **kwargs)
            except RegistryError:
                logger.warning(
                    "API key provider '%s' not found, falling back to NullApiKeyProvider",
                    provider,
                )
                self._provider = NullApiKeyProvider()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> ApiKeyProvider:
        return self._provider

    def create(
        self,
        *,
        name: str = "",
        roles: list[str] | None = None,
        user_id: str = "",
        description: str = "",
        expires_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[ApiKeyEntry, str]:
        """Generate and store a new API key.

        The raw key is only returned once — it is hashed before storage
        and cannot be recovered.

        Args:
            name: Human-readable name for the key (unique).
            roles: Role names bound to this key (default ``["user"]``).
            user_id: Username this key is bound to (optional).
            description: Human-readable description.
            expires_at: ISO 8601 UTC timestamp for expiration (empty = never).
            metadata: Extra metadata.

        Returns:
            A tuple of (stored entry, raw key string).
        """
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        key_id = secrets.token_hex(8)  # 16-char short ID
        entry = ApiKeyEntry(
            key_id=key_id,
            name=name,
            key_hash=key_hash,
            key_prefix=raw_key[:12],  # e.g. "agk_a1b2c3d4"
            roles=roles or ["user"],
            user_id=user_id,
            description=description,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        stored = self._provider.create(entry)
        logger.info(
            "API key generated: %s (name=%s, prefix=%s)",
            stored.key_id, stored.name, stored.key_prefix,
            extra={
                "event": "apikey.generate",
                "key_id": stored.key_id,
                "key_prefix": stored.key_prefix,
            },
        )
        return stored, raw_key

    def get(self, key_id: str) -> ApiKeyEntry | None:
        """Get a key by ID."""
        return self._provider.get(key_id)

    def get_by_name(self, name: str) -> ApiKeyEntry | None:
        """Get a key by name."""
        for entry in self._provider.list():
            if entry.name == name:
                return entry
        return None

    def list(self) -> list[ApiKeyEntry]:
        """List all registered keys."""
        return self._provider.list()

    def update(self, key_id: str, changes: dict[str, Any]) -> ApiKeyEntry | None:
        """Update fields on an existing key.

        Only ``name``, ``roles``, ``description``, ``enabled``,
        ``expires_at``, and ``metadata`` can be updated.
        """
        allowed_fields = {
            "name", "roles", "description", "enabled",
            "expires_at", "metadata",
        }
        filtered = {k: v for k, v in changes.items() if k in allowed_fields}
        if not filtered:
            return None
        return self._provider.update(key_id, filtered)

    def delete(self, key_id: str) -> bool:
        """Delete a key permanently."""
        return self._provider.delete(key_id)

    def revoke(self, key_id: str) -> ApiKeyEntry | None:
        """Revoke a key by disabling it (without deleting)."""
        return self._provider.update(key_id, {"enabled": False})

    def verify(self, raw_key: str) -> ApiKeyEntry | None:
        """Verify a raw API key.

        Checks that:
        - The key exists in storage
        - The key is enabled
        - The key has not expired

        On success, updates ``last_used_at`` and ``call_count``,
        then returns the entry.  On failure, returns ``None``.

        Args:
            raw_key: The raw API key string (e.g. ``agk_...``).

        Returns:
            The ApiKeyEntry if valid, ``None`` otherwise.
        """
        if not raw_key:
            return None
        key_hash = hash_api_key(raw_key)
        entry = self._provider.get_by_hash(key_hash)
        if entry is None:
            logger.debug(
                "API key verification failed (not found): prefix=%s",
                raw_key[:8] if len(raw_key) > 8 else "short",
                extra={"event": "apikey.verify_failed", "reason": "not_found"},
            )
            return None
        if not entry.enabled:
            logger.info(
                "API key verification failed (disabled): %s", entry.key_id,
                extra={"event": "apikey.verify_failed", "key_id": entry.key_id, "reason": "disabled"},
            )
            return None
        if entry.is_expired:
            logger.info(
                "API key verification failed (expired): %s", entry.key_id,
                extra={"event": "apikey.verify_failed", "key_id": entry.key_id, "reason": "expired"},
            )
            return None
        # Update usage stats
        self._provider.update(entry.key_id, {
            "last_used_at": _now(),
            "call_count": entry.call_count + 1,
        })
        # Return a fresh copy with updated stats
        updated = self._provider.get(entry.key_id)
        return updated or entry

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Default singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_default_manager: ApiKeyManager | None = None
_default_manager_lock = threading.Lock()


def get_apikey_manager() -> ApiKeyManager:
    """Get the default ApiKeyManager singleton.

    The singleton is lazily initialised as disabled (NullApiKeyProvider)
    on first access.  Call ``set_apikey_manager()`` to configure it.
    """
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = ApiKeyManager(enabled=False)
    return _default_manager


def set_apikey_manager(manager: ApiKeyManager) -> None:
    """Set the global ApiKeyManager singleton."""
    global _default_manager
    with _default_manager_lock:
        _default_manager = manager


# ---------------------------------------------------------------------------
# Register default providers
# ---------------------------------------------------------------------------

@register_apikey_provider("memory")
def _make_in_memory_provider(**kwargs: Any) -> InMemoryApiKeyProvider:
    """Factory for InMemoryApiKeyProvider."""
    return InMemoryApiKeyProvider()


@register_apikey_provider("null")
def _make_null_provider(**kwargs: Any) -> NullApiKeyProvider:
    """Factory for NullApiKeyProvider."""
    return NullApiKeyProvider()
