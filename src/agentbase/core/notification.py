"""Notification center — in-app notification management and delivery.

Provides a pluggable notification system that allows the platform to:
- Create notifications for users (system announcements, quota alerts, task
  completion, threshold warnings, etc.)
- Query, mark-as-read, and delete notifications
- Track unread counts and delivery statistics
- Broadcast notifications to all users

Pluggable storage:
- ``InMemoryNotificationProvider`` (default) — zero-config, thread-safe, in-process
- ``NullNotificationProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_notification_provider("name")``

Usage::

    from agentbase.core.notification import NotificationManager

    manager = NotificationManager(provider="memory", enabled=True)

    # Send a notification to a specific user
    notif = manager.create_notification(
        user_id="user-001",
        title="Quota Warning",
        message="You have used 90% of your token quota.",
        category="quota_alert",
        severity="warning",
    )

    # Broadcast a system announcement
    manager.broadcast(
        title="System Maintenance",
        message="The system will be down for maintenance at 2:00 AM UTC.",
        category="system",
        severity="info",
    )

    # Query unread notifications for a user
    unread = manager.list_notifications(user_id="user-001", unread_only=True)
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Notification:
    """A single notification entry.

    Attributes:
        user_id: Target user ID. ``"*"`` means broadcast (all users).
        title: Short notification title / subject.
        message: Full notification body text.
        category: Notification category for grouping/filtering.
            Standard categories: ``system``, ``quota_alert``, ``task_complete``,
            ``usage_warning``, ``security``, ``feedback``, ``webhook``.
        severity: Severity level — ``info``, ``warning``, ``error``, ``critical``.
        read: Whether the notification has been read by the user.
        action_url: Optional URL for the user to take action (e.g. ``/usage``).
        action_label: Optional label for the action URL (e.g. ``"View Usage"``).
        metadata: Arbitrary key-value metadata for extensibility.
        created_at: ISO 8601 UTC timestamp (auto-set).
        read_at: ISO 8601 UTC timestamp when the notification was marked as read.
        expires_at: Optional ISO 8601 UTC timestamp when the notification expires.
        id: Auto-assigned notification ID.
    """

    user_id: str
    title: str
    message: str = ""
    category: str = "system"
    severity: str = "info"
    read: bool = False
    action_url: str = ""
    action_label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    read_at: str = ""
    expires_at: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]

    @property
    def is_expired(self) -> bool:
        """Check if the notification has expired."""
        if not self.expires_at:
            return False
        return _now() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "message": self.message,
            "category": self.category,
            "severity": self.severity,
            "read": self.read,
            "action_url": self.action_url,
            "action_label": self.action_label,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "read_at": self.read_at,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired,
        }


@dataclass
class NotificationFilter:
    """Filter criteria for querying notifications.

    All fields are optional — ``None`` means "no filter on this field".
    """

    user_id: str | None = None
    category: str | None = None
    severity: str | None = None
    unread_only: bool = False
    since: str | None = None
    until: str | None = None
    include_broadcast: bool = True
    limit: int = 100
    offset: int = 0


@dataclass
class NotificationStats:
    """Aggregate notification statistics.

    Attributes:
        total: Total number of notifications.
        unread: Number of unread notifications.
        read: Number of read notifications.
        by_category: Per-category notification counts.
        by_severity: Per-severity notification counts.
        by_user: Per-user notification counts.
        broadcasts: Number of broadcast notifications (user_id="*").
    """

    total: int = 0
    unread: int = 0
    read: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    by_user: dict[str, int] = field(default_factory=dict)
    broadcasts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "unread": self.unread,
            "read": self.read,
            "by_category": dict(self.by_category),
            "by_severity": dict(self.by_severity),
            "by_user": dict(self.by_user),
            "broadcasts": self.broadcasts,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class NotificationProvider(Protocol):
    """Protocol for notification storage providers.

    Implementations must be thread-safe.
    """

    def create(self, notification: Notification) -> Notification:
        """Store a notification. Returns the stored notification with ID."""
        ...

    def get(self, notification_id: str) -> Notification | None:
        """Get a notification by ID. Returns None if not found."""
        ...

    def query(self, filter: NotificationFilter | None = None) -> list[Notification]:
        """Query notifications matching the filter."""
        ...

    def update(self, notification_id: str, changes: dict[str, Any]) -> Notification | None:
        """Update fields on an existing notification. Returns updated or None."""
        ...

    def mark_read(self, notification_id: str, *, read: bool = True) -> Notification | None:
        """Mark a notification as read or unread. Returns updated or None."""
        ...

    def mark_all_read(self, user_id: str) -> int:
        """Mark all notifications for a user as read. Returns count affected."""
        ...

    def delete(self, notification_id: str) -> bool:
        """Delete a notification. Returns True if deleted."""
        ...

    def stats(self) -> NotificationStats:
        """Get aggregate notification statistics."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullNotificationProvider:
    """No-op notification provider — all operations return empty/None.

    Used when notifications are disabled (``notification.enabled=false``).
    """

    def create(self, notification: Notification) -> Notification:
        return notification

    def get(self, notification_id: str) -> Notification | None:
        return None

    def query(self, filter: NotificationFilter | None = None) -> list[Notification]:
        return []

    def update(self, notification_id: str, changes: dict[str, Any]) -> Notification | None:
        return None

    def mark_read(self, notification_id: str, *, read: bool = True) -> Notification | None:
        return None

    def mark_all_read(self, user_id: str) -> int:
        return 0

    def delete(self, notification_id: str) -> bool:
        return False

    def stats(self) -> NotificationStats:
        return NotificationStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryNotificationProvider:
    """In-memory notification provider — thread-safe, zero-config.

    Stores notifications in memory.  All data is lost on process restart.
    """

    def __init__(self, max_records: int = 100_000) -> None:
        self._lock = threading.RLock()
        self._notifications: dict[str, Notification] = {}
        self._max_records = max_records

    def create(self, notification: Notification) -> Notification:
        with self._lock:
            # FIFO eviction when capacity reached
            if len(self._notifications) >= self._max_records:
                oldest_id = min(
                    self._notifications,
                    key=lambda k: self._notifications[k].created_at,
                )
                del self._notifications[oldest_id]
            self._notifications[notification.id] = notification
            logger.info(
                "Notification created: id=%s user=%s category=%s severity=%s",
                notification.id,
                notification.user_id,
                notification.category,
                notification.severity,
                extra={
                    "event": "notification.created",
                    "notification_id": notification.id,
                    "user_id": notification.user_id,
                    "category": notification.category,
                    "severity": notification.severity,
                },
            )
            return notification

    def get(self, notification_id: str) -> Notification | None:
        with self._lock:
            return self._notifications.get(notification_id)

    def query(self, filter: NotificationFilter | None = None) -> list[Notification]:
        with self._lock:
            records = list(self._notifications.values())
        # Filter out expired notifications
        records = [n for n in records if not n.is_expired]
        if filter is None:
            records.sort(key=lambda n: n.created_at, reverse=True)
            return records
        return _apply_notification_filter(records, filter)

    def update(self, notification_id: str, changes: dict[str, Any]) -> Notification | None:
        with self._lock:
            existing = self._notifications.get(notification_id)
            if existing is None:
                return None
            # Apply changes
            for key, value in changes.items():
                if key in ("id", "created_at"):
                    continue
                if hasattr(existing, key):
                    setattr(existing, key, value)
            logger.info(
                "Notification updated: id=%s fields=%s",
                notification_id,
                list(changes.keys()),
                extra={"event": "notification.updated", "notification_id": notification_id},
            )
            return existing

    def mark_read(self, notification_id: str, *, read: bool = True) -> Notification | None:
        with self._lock:
            existing = self._notifications.get(notification_id)
            if existing is None:
                return None
            existing.read = read
            existing.read_at = _now() if read else ""
            logger.info(
                "Notification marked %s: id=%s",
                "read" if read else "unread",
                notification_id,
                extra={
                    "event": "notification.mark_read" if read else "notification.mark_unread",
                    "notification_id": notification_id,
                },
            )
            return existing

    def mark_all_read(self, user_id: str) -> int:
        with self._lock:
            count = 0
            for n in self._notifications.values():
                if (n.user_id == user_id or n.user_id == "*") and not n.read:
                    n.read = True
                    n.read_at = _now()
                    count += 1
            if count:
                logger.info(
                    "Marked %d notifications as read for user=%s",
                    count,
                    user_id,
                    extra={
                        "event": "notification.mark_all_read",
                        "user_id": user_id,
                        "count": count,
                    },
                )
            return count

    def delete(self, notification_id: str) -> bool:
        with self._lock:
            if notification_id not in self._notifications:
                return False
            del self._notifications[notification_id]
            logger.info(
                "Notification deleted: id=%s",
                notification_id,
                extra={"event": "notification.deleted", "notification_id": notification_id},
            )
            return True

    def stats(self) -> NotificationStats:
        with self._lock:
            records = [n for n in self._notifications.values() if not n.is_expired]
        total = len(records)
        unread = sum(1 for n in records if not n.read)
        read = sum(1 for n in records if n.read)

        by_category: dict[str, int] = {}
        for n in records:
            by_category[n.category] = by_category.get(n.category, 0) + 1

        by_severity: dict[str, int] = {}
        for n in records:
            by_severity[n.severity] = by_severity.get(n.severity, 0) + 1

        by_user: dict[str, int] = {}
        for n in records:
            by_user[n.user_id] = by_user.get(n.user_id, 0) + 1

        broadcasts = sum(1 for n in records if n.user_id == "*")

        return NotificationStats(
            total=total,
            unread=unread,
            read=read,
            by_category=by_category,
            by_severity=by_severity,
            by_user=by_user,
            broadcasts=broadcasts,
        )

    def close(self) -> None:
        with self._lock:
            self._notifications.clear()


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_notification_filter(
    records: list[Notification],
    flt: NotificationFilter,
) -> list[Notification]:
    """Apply filter criteria to a list of notifications."""
    result: list[Notification] = []
    for n in records:
        if flt.user_id is not None:
            # Match specific user or broadcast ("*")
            if n.user_id != flt.user_id:
                if not (flt.include_broadcast and n.user_id == "*"):
                    continue
        if flt.category is not None and n.category != flt.category:
            continue
        if flt.severity is not None and n.severity != flt.severity:
            continue
        if flt.unread_only and n.read:
            continue
        if flt.since is not None and n.created_at < flt.since:
            continue
        if flt.until is not None and n.created_at >= flt.until:
            continue
        result.append(n)
    # Sort by created_at descending (newest first)
    result.sort(key=lambda n: n.created_at, reverse=True)
    if flt.offset > 0:
        result = result[flt.offset:]
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class NotificationRegistry:
    """Thread-safe registry for notification providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., NotificationProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., NotificationProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty notification provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Notification provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> NotificationProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown notification provider: {key}. Available: {available}"
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
notification_registry = NotificationRegistry()

# Register defaults
notification_registry.register("null", NullNotificationProvider)
notification_registry.register("memory", InMemoryNotificationProvider)


def register_notification_provider(name: str, *, override: bool = False):
    """Decorator: register a notification provider class.

    Usage::

        @register_notification_provider("redis")
        class RedisNotificationProvider:
            def create(self, notification): ...
    """
    def decorator(factory: Callable[..., NotificationProvider]):
        notification_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class NotificationManager:
    """High-level notification center manager.

    Wraps a ``NotificationProvider`` for notification storage, querying,
    and statistics.  When ``enabled=False``, uses ``NullNotificationProvider``
    (no-op).

    Usage::

        manager = NotificationManager(provider="memory", enabled=True)
        manager.create_notification(
            user_id="user-001",
            title="Welcome",
            message="Welcome to AgentBase!",
        )
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: NotificationProvider = NullNotificationProvider()
        else:
            self._provider = notification_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> NotificationProvider:
        return self._provider

    def create_notification(
        self,
        *,
        user_id: str,
        title: str,
        message: str = "",
        category: str = "system",
        severity: str = "info",
        action_url: str = "",
        action_label: str = "",
        metadata: dict[str, Any] | None = None,
        expires_at: str = "",
    ) -> Notification:
        """Create a new notification for a specific user.

        Args:
            user_id: Target user ID. Use ``"*"`` for broadcast.
            title: Short notification title (required).
            message: Full notification body text.
            category: Notification category (default ``"system"``).
            severity: Severity level (``info``, ``warning``, ``error``, ``critical``).
            action_url: Optional action URL.
            action_label: Optional action button label.
            metadata: Extensible metadata.
            expires_at: Optional ISO 8601 expiry timestamp.

        Returns:
            The stored ``Notification`` with ID assigned.

        Raises:
            RegistryError: If ``user_id`` or ``title`` is empty.
        """
        if not user_id:
            raise RegistryError("user_id is required for notification")
        if not title:
            raise RegistryError("title is required for notification")

        notification = Notification(
            user_id=user_id,
            title=title,
            message=message,
            category=category,
            severity=severity,
            action_url=action_url,
            action_label=action_label,
            metadata=metadata or {},
            expires_at=expires_at,
        )
        return self._provider.create(notification)

    def broadcast(
        self,
        *,
        title: str,
        message: str = "",
        category: str = "system",
        severity: str = "info",
        action_url: str = "",
        action_label: str = "",
        metadata: dict[str, Any] | None = None,
        expires_at: str = "",
    ) -> Notification:
        """Broadcast a notification to all users.

        Creates a notification with ``user_id="*"`` which is included
        in all users' notification lists when queried with
        ``include_broadcast=True`` (default).

        Args:
            title: Short notification title (required).
            message: Full notification body text.
            category: Notification category (default ``"system"``).
            severity: Severity level.
            action_url: Optional action URL.
            action_label: Optional action button label.
            metadata: Extensible metadata.
            expires_at: Optional ISO 8601 expiry timestamp.

        Returns:
            The stored ``Notification`` with ID assigned.

        Raises:
            RegistryError: If ``title`` is empty.
        """
        if not title:
            raise RegistryError("title is required for broadcast")

        notification = Notification(
            user_id="*",
            title=title,
            message=message,
            category=category,
            severity=severity,
            action_url=action_url,
            action_label=action_label,
            metadata=metadata or {},
            expires_at=expires_at,
        )
        return self._provider.create(notification)

    def get_notification(self, notification_id: str) -> Notification | None:
        """Get a notification by ID."""
        return self._provider.get(notification_id)

    def list_notifications(
        self,
        *,
        user_id: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        unread_only: bool = False,
        since: str | None = None,
        until: str | None = None,
        include_broadcast: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Notification]:
        """Query notifications with filters. Returns empty list when disabled."""
        flt = NotificationFilter(
            user_id=user_id,
            category=category,
            severity=severity,
            unread_only=unread_only,
            since=since,
            until=until,
            include_broadcast=include_broadcast,
            limit=limit,
            offset=offset,
        )
        return self._provider.query(flt)

    def update_notification(
        self,
        notification_id: str,
        *,
        title: str | None = None,
        message: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        action_url: str | None = None,
        action_label: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> Notification | None:
        """Update an existing notification.

        Only provided fields are updated. Returns the updated notification,
        or None if not found.
        """
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if message is not None:
            changes["message"] = message
        if category is not None:
            changes["category"] = category
        if severity is not None:
            changes["severity"] = severity
        if action_url is not None:
            changes["action_url"] = action_url
        if action_label is not None:
            changes["action_label"] = action_label
        if metadata is not None:
            changes["metadata"] = metadata
        if expires_at is not None:
            changes["expires_at"] = expires_at
        if not changes:
            return self._provider.get(notification_id)
        return self._provider.update(notification_id, changes)

    def mark_read(self, notification_id: str) -> Notification | None:
        """Mark a notification as read."""
        return self._provider.mark_read(notification_id, read=True)

    def mark_unread(self, notification_id: str) -> Notification | None:
        """Mark a notification as unread."""
        return self._provider.mark_read(notification_id, read=False)

    def mark_all_read(self, user_id: str) -> int:
        """Mark all notifications for a user as read.

        Also marks broadcast notifications as read for this user.
        Returns the number of notifications marked as read.
        """
        return self._provider.mark_all_read(user_id)

    def delete_notification(self, notification_id: str) -> bool:
        """Delete a notification. Returns True if deleted."""
        return self._provider.delete(notification_id)

    def get_stats(
        self,
        *,
        user_id: str | None = None,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> NotificationStats:
        """Get aggregate notification statistics.

        When filters are provided, statistics are computed only for
        matching records. Returns zero-values when disabled.
        """
        if user_id or category or since or until:
            flt = NotificationFilter(
                user_id=user_id,
                category=category,
                since=since,
                until=until,
                limit=0,  # 0 = no limit
            )
            records = self._provider.query(flt)
            return _compute_stats(records)
        return self._provider.stats()

    def get_unread_count(self, user_id: str) -> int:
        """Get the unread notification count for a specific user.

        Includes broadcast notifications that haven't been marked as read.
        """
        flt = NotificationFilter(user_id=user_id, unread_only=True, limit=0)
        records = self._provider.query(flt)
        return len(records)

    def clear_all(self) -> int:
        """Delete all notifications. Returns the count deleted."""
        records = self._provider.query(None)
        count = 0
        for n in records:
            if self._provider.delete(n.id):
                count += 1
        logger.info("Cleared all notifications: count=%d", count)
        return count

    def close(self) -> None:
        self._provider.close()


def _compute_stats(records: list[Notification]) -> NotificationStats:
    """Compute aggregate statistics from a list of notifications."""
    total = len(records)
    unread = sum(1 for n in records if not n.read)
    read = sum(1 for n in records if n.read)

    by_category: dict[str, int] = {}
    for n in records:
        by_category[n.category] = by_category.get(n.category, 0) + 1

    by_severity: dict[str, int] = {}
    for n in records:
        by_severity[n.severity] = by_severity.get(n.severity, 0) + 1

    by_user: dict[str, int] = {}
    for n in records:
        by_user[n.user_id] = by_user.get(n.user_id, 0) + 1

    broadcasts = sum(1 for n in records if n.user_id == "*")

    return NotificationStats(
        total=total,
        unread=unread,
        read=read,
        by_category=by_category,
        by_severity=by_severity,
        by_user=by_user,
        broadcasts=broadcasts,
    )


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_notification_manager: NotificationManager | None = None
_notification_manager_lock = threading.Lock()


def get_notification_manager() -> NotificationManager:
    """Get the global NotificationManager singleton.

    Raises ``RuntimeError`` if not initialised — call ``set_notification_manager``
    first (typically during application bootstrap).
    """
    if _notification_manager is None:
        with _notification_manager_lock:
            if _notification_manager is None:
                raise RuntimeError(
                    "NotificationManager not initialised. "
                    "Call set_notification_manager() first."
                )
    return _notification_manager  # type: ignore[return-value]


def set_notification_manager(manager: NotificationManager) -> None:
    """Set the global NotificationManager singleton."""
    global _notification_manager
    with _notification_manager_lock:
        _notification_manager = manager


def reset_notification_manager() -> None:
    """Reset the global NotificationManager singleton (for testing)."""
    global _notification_manager
    with _notification_manager_lock:
        _notification_manager = None
