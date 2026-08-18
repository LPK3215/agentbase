"""System configuration service — runtime, hot-reloadable key/value settings.

Complements the static YAML+env config system (which governs startup
parameters) with a runtime-writable key/value store:

- Create / read / update / delete config entries **without restarting**
- Entries are arbitrary JSON values grouped by ``category``
- Change callbacks (``on_change``) enable live reconfiguration of
  platform components (feature flags, quotas, tunables)
- ``is_public`` entries are safe to expose to unauthenticated readers
  (e.g. frontend feature flags)

Pluggable backends:
- ``InMemorySystemConfigProvider`` (default) — zero-config, thread-safe,
  FIFO eviction
- ``NullSystemConfigProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_system_config_provider("name")``

Usage::

    from agentbase.core.system_config import SystemConfigManager

    manager = SystemConfigManager(provider="memory", enabled=True)

    manager.set("feature.daily_report", True, category="feature",
                description="Enable the daily report job")
    manager.get("feature.daily_report")            # -> True
    manager.get("missing.key", default="off")      # -> "off"

    def on_change(key, old, new):
        print(f"{key}: {old!r} -> {new!r}")

    manager.on_change(on_change)
    manager.set("feature.daily_report", False)     # callback fires
"""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "ConfigItem",
    "SystemConfigFilter",
    "SystemConfigStats",
    "SystemConfigProvider",
    "NullSystemConfigProvider",
    "InMemorySystemConfigProvider",
    "SystemConfigRegistry",
    "system_config_registry",
    "register_system_config_provider",
    "SystemConfigManager",
    "get_system_config_manager",
    "set_system_config_manager",
    "reset_system_config_manager",
]

# ---------------------------------------------------------------------------
# Limits and validation constants
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ITEMS = 1_000
_MAX_KEY_LENGTH = 128
_MAX_VALUE_BYTES = 65_536  # 64 KiB serialized JSON
_MAX_CATEGORY_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1_000
_MAX_UPDATED_BY_LENGTH = 128

# key: lowercase letters/digits/dots/underscores/hyphens, must start with a
# letter or digit; recommended form ``namespace.name`` (checked softly)
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ConfigItem:
    """A single runtime config entry."""

    key: str
    value: Any = None
    category: str = "general"
    description: str = ""
    is_public: bool = False
    updated_by: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "description": self.description,
            "is_public": self.is_public,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }


@dataclass
class SystemConfigFilter:
    """Filter criteria for listing config entries."""

    category: str | None = None
    key_prefix: str | None = None
    public_only: bool = False
    updated_since: str | None = None  # ISO-8601; entries updated at/after this
    limit: int | None = None
    offset: int = 0


@dataclass
class SystemConfigStats:
    """Aggregate statistics over stored config entries."""

    total: int = 0
    public_count: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    recently_updated: int = 0  # updated within the last 24 hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "public_count": self.public_count,
            "by_category": dict(self.by_category),
            "recently_updated": self.recently_updated,
        }


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SystemConfigProvider(Protocol):
    """Storage contract for runtime config entries."""

    def set_item(self, item: ConfigItem) -> ConfigItem: ...
    def get_item(self, key: str) -> ConfigItem | None: ...
    def list_items(self, filter: SystemConfigFilter | None = None) -> list[ConfigItem]: ...
    def delete_item(self, key: str) -> bool: ...
    def get_stats(self) -> SystemConfigStats: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Null provider (disabled mode)
# ---------------------------------------------------------------------------

class NullSystemConfigProvider:
    """No-op system config provider — reads return None, writes are dropped.

    Used when the system config service is disabled
    (``system_config.enabled=false``).
    """

    def set_item(self, item: ConfigItem) -> ConfigItem:
        return item

    def get_item(self, key: str) -> ConfigItem | None:
        return None

    def list_items(self, filter: SystemConfigFilter | None = None) -> list[ConfigItem]:
        return []

    def delete_item(self, key: str) -> bool:
        return False

    def get_stats(self) -> SystemConfigStats:
        return SystemConfigStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemorySystemConfigProvider:
    """In-memory config store — thread-safe with FIFO eviction.

    Args:
        max_items: Max stored entries before the oldest-inserted entries
            are evicted.
    """

    def __init__(self, max_items: int = _DEFAULT_MAX_ITEMS) -> None:
        self._items: dict[str, ConfigItem] = {}
        self._order: list[str] = []  # insertion order for FIFO eviction
        self._lock = threading.RLock()
        self._max_items = max(1, int(max_items))

    # -- internal helpers ---------------------------------------------------

    def _evict_locked(self) -> None:
        while len(self._order) > self._max_items:
            oldest = self._order.pop(0)
            self._items.pop(oldest, None)
            logger.debug(
                "Config entry evicted: %s",
                oldest,
                extra={"event": "system_config.evicted", "key": oldest},
            )

    # -- SystemConfigProvider ------------------------------------------------

    def set_item(self, item: ConfigItem) -> ConfigItem:
        with self._lock:
            existing = self._items.get(item.key)
            if existing is not None:
                # upsert: preserve created_at, bump version
                item.created_at = existing.created_at
                item.version = existing.version + 1
                self._items[item.key] = item
            else:
                self._items[item.key] = item
                self._order.append(item.key)
                self._evict_locked()
        logger.info(
            "Config entry set: %s (v%s)",
            item.key,
            item.version,
            extra={"event": "system_config.set", "key": item.key},
        )
        return item

    def get_item(self, key: str) -> ConfigItem | None:
        with self._lock:
            return self._items.get(key)

    def list_items(self, filter: SystemConfigFilter | None = None) -> list[ConfigItem]:
        with self._lock:
            items = list(self._items.values())
        return _apply_system_config_filter(items, filter)

    def delete_item(self, key: str) -> bool:
        with self._lock:
            if key not in self._items:
                return False
            self._items.pop(key, None)
            if key in self._order:
                self._order.remove(key)
        logger.info(
            "Config entry deleted: %s",
            key,
            extra={"event": "system_config.deleted", "key": key},
        )
        return True

    def get_stats(self) -> SystemConfigStats:
        with self._lock:
            items = list(self._items.values())
        cutoff = datetime.now(UTC).timestamp() - 86_400
        stats = SystemConfigStats(total=len(items))
        for it in items:
            if it.is_public:
                stats.public_count += 1
            stats.by_category[it.category] = stats.by_category.get(it.category, 0) + 1
            try:
                ts = datetime.fromisoformat(it.updated_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts.timestamp() >= cutoff:
                    stats.recently_updated += 1
            except ValueError:
                pass
        return stats

    def close(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------

def _apply_system_config_filter(
    items: list[ConfigItem], flt: SystemConfigFilter | None
) -> list[ConfigItem]:
    """Apply filter criteria then sort by key asc and paginate."""
    if flt is not None:
        since = None
        if flt.updated_since:
            try:
                parsed = datetime.fromisoformat(flt.updated_since)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                since = parsed.timestamp()
            except ValueError:
                since = None

        def keep(it: ConfigItem) -> bool:
            if flt.category is not None and it.category != flt.category:
                return False
            if flt.key_prefix is not None and not it.key.startswith(flt.key_prefix):
                return False
            if flt.public_only and not it.is_public:
                return False
            if since is not None:
                try:
                    ts = datetime.fromisoformat(it.updated_at)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if ts.timestamp() < since:
                        return False
                except ValueError:
                    return False
            return True

        items = [it for it in items if keep(it)]

    items = sorted(items, key=lambda it: it.key)
    if flt is not None:
        if flt.offset > 0:
            items = items[flt.offset:]
        if flt.limit is not None and flt.limit >= 0:
            items = items[: flt.limit]
    return items


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SystemConfigRegistry:
    """Thread-safe registry for system config providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., SystemConfigProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., SystemConfigProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty system config provider name")
            if key in self._factories and not override:
                raise RegistryError(f"System config provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> SystemConfigProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown system config provider: {key}. Available: {available}"
                )
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
system_config_registry = SystemConfigRegistry()

# Register defaults
system_config_registry.register("null", NullSystemConfigProvider)
system_config_registry.register("memory", InMemorySystemConfigProvider)


def register_system_config_provider(name: str, *, override: bool = False):
    """Decorator: register a system config provider class.

    Usage::

        @register_system_config_provider("redis")
        class RedisSystemConfigProvider:
            def set_item(self, item): ...
    """
    def decorator(factory: Callable[..., SystemConfigProvider]):
        system_config_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

ChangeCallback = Callable[[str, Any, Any], None]


class SystemConfigManager:
    """High-level runtime config manager.

    Wraps a ``SystemConfigProvider`` for entry CRUD, filtered queries, and
    statistics, with input validation (key format, JSON value size, field
    length limits) and change-callback fanout. When ``enabled=False`` it
    wraps a ``NullSystemConfigProvider`` (no-op).

    Usage::

        manager = SystemConfigManager(provider="memory", enabled=True)
        manager.set("feature.dark_mode", True, category="feature")
        manager.get("feature.dark_mode")   # -> True
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        self._callbacks: list[ChangeCallback] = []
        self._callback_lock = threading.RLock()
        if not enabled:
            self._provider: SystemConfigProvider = NullSystemConfigProvider()
        else:
            self._provider = system_config_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> SystemConfigProvider:
        return self._provider

    # -- validation -----------------------------------------------------------

    @staticmethod
    def _validate_key(key: str) -> str:
        key = (key or "").strip().lower()
        if not key:
            raise RegistryError("Config key is required")
        if len(key) > _MAX_KEY_LENGTH:
            raise RegistryError(
                f"Config key too long: {len(key)} (max {_MAX_KEY_LENGTH})"
            )
        if not _KEY_PATTERN.match(key):
            raise RegistryError(
                f"Invalid config key: {key!r} "
                "(lowercase letters/digits/dots/underscores/hyphens, "
                "must start with letter or digit)"
            )
        return key

    @staticmethod
    def _validate_value(value: Any) -> None:
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise RegistryError(f"Config value not JSON-serializable: {exc}") from exc
        if len(serialized.encode("utf-8")) > _MAX_VALUE_BYTES:
            raise RegistryError(
                f"Config value too large: {len(serialized.encode('utf-8'))} bytes "
                f"(max {_MAX_VALUE_BYTES})"
            )

    @staticmethod
    def _validate_metadata(category: str, description: str, updated_by: str) -> None:
        if len(category) > _MAX_CATEGORY_LENGTH:
            raise RegistryError(
                f"category too long: {len(category)} (max {_MAX_CATEGORY_LENGTH})"
            )
        if len(description) > _MAX_DESCRIPTION_LENGTH:
            raise RegistryError(
                f"description too long: {len(description)} (max {_MAX_DESCRIPTION_LENGTH})"
            )
        if len(updated_by) > _MAX_UPDATED_BY_LENGTH:
            raise RegistryError(
                f"updated_by too long: {len(updated_by)} (max {_MAX_UPDATED_BY_LENGTH})"
            )

    # -- CRUD -----------------------------------------------------------------

    def set(
        self,
        key: str,
        value: Any,
        *,
        category: str = "general",
        description: str = "",
        is_public: bool = False,
        updated_by: str = "",
    ) -> ConfigItem:
        """Create or update a config entry (upsert).

        On update, ``created_at`` is preserved, ``version`` is bumped, and
        change callbacks fire with ``(key, old_value, new_value)``.

        Raises:
            RegistryError: On invalid key / oversized value / over-limit fields.
        """
        key = self._validate_key(key)
        self._validate_value(value)
        self._validate_metadata(category, description, updated_by)

        existing = self._provider.get_item(key)
        old_value = existing.value if existing is not None else None

        if existing is not None:
            item = ConfigItem(
                key=key,
                value=value,
                category=existing.category if category == "general" and existing.category else category,
                description=existing.description if not description else description,
                is_public=is_public,
                updated_by=updated_by,
                created_at=existing.created_at,
                updated_at=_now_iso(),
            )
        else:
            item = ConfigItem(
                key=key,
                value=value,
                category=category,
                description=description,
                is_public=is_public,
                updated_by=updated_by,
            )
        stored = self._provider.set_item(item)
        self._notify_change(key, old_value, value)
        return stored

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key (returns ``default`` when missing)."""
        item = self._provider.get_item((key or "").strip().lower())
        return item.value if item is not None else default

    def get_item(self, key: str) -> ConfigItem | None:
        """Get the full config entry (with metadata) by key."""
        return self._provider.get_item((key or "").strip().lower())

    def exists(self, key: str) -> bool:
        """Return True when the key exists."""
        return self._provider.get_item((key or "").strip().lower()) is not None

    def keys(self, prefix: str | None = None) -> list[str]:
        """List stored keys (optionally filtered by prefix), sorted asc."""
        items = self._provider.list_items(
            SystemConfigFilter(key_prefix=prefix) if prefix else None
        )
        return [it.key for it in items]

    def list_items(
        self,
        *,
        category: str | None = None,
        key_prefix: str | None = None,
        public_only: bool = False,
        updated_since: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ConfigItem]:
        """List entries (sorted by key asc) with optional filters."""
        flt = SystemConfigFilter(
            category=category,
            key_prefix=key_prefix,
            public_only=public_only,
            updated_since=updated_since,
            limit=limit,
            offset=offset,
        )
        return self._provider.list_items(flt)

    def delete(self, key: str) -> bool:
        """Delete an entry. Returns True when deleted, False when missing."""
        key = (key or "").strip().lower()
        existing = self._provider.get_item(key)
        deleted = self._provider.delete_item(key)
        if deleted and existing is not None:
            self._notify_change(key, existing.value, None)
        return deleted

    def get_stats(self) -> SystemConfigStats:
        """Aggregate statistics (total / public / by_category / recent)."""
        return self._provider.get_stats()

    # -- change callbacks -------------------------------------------------------

    def on_change(self, callback: ChangeCallback) -> None:
        """Register a callback fired as ``callback(key, old_value, new_value)``.

        Callbacks run synchronously after each set/delete; a raising
        callback is logged and skipped (it never blocks the operation).
        """
        with self._callback_lock:
            self._callbacks.append(callback)

    def _notify_change(self, key: str, old_value: Any, new_value: Any) -> None:
        with self._callback_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(key, old_value, new_value)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "System config change callback failed for key %s",
                    key,
                    extra={"event": "system_config.callback_error", "key": key},
                    exc_info=True,
                )

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_system_config_manager: SystemConfigManager | None = None
_system_config_manager_lock = threading.Lock()


def get_system_config_manager() -> SystemConfigManager:
    """Get the process-wide SystemConfigManager (creates a disabled one by default)."""
    global _system_config_manager
    if _system_config_manager is None:
        with _system_config_manager_lock:
            if _system_config_manager is None:
                _system_config_manager = SystemConfigManager()
    return _system_config_manager


def set_system_config_manager(manager: SystemConfigManager) -> None:
    """Replace the process-wide SystemConfigManager."""
    global _system_config_manager
    with _system_config_manager_lock:
        _system_config_manager = manager


def reset_system_config_manager() -> None:
    """Reset the process-wide SystemConfigManager (for testing)."""
    global _system_config_manager
    with _system_config_manager_lock:
        _system_config_manager = None
