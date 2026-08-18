"""Calendar / event management service — 日程管理.

Provides recurring-free event (日程) storage and query for agents and
API consumers: create / list / update / delete events with time ranges,
statuses (confirmed / tentative / cancelled), attendees, tags, reminders,
and aggregate statistics.

Architecture (mirrors notification / conversation / scheduler services):

- ``CalendarProvider`` — Protocol with CRUD + stats
- ``InMemoryCalendarProvider`` — default zero-config implementation
  (thread-safe, FIFO eviction)
- ``NullCalendarProvider`` — no-op when disabled (``calendar.enabled=false``)
- ``CalendarManager`` — high-level facade with validation
- ``CalendarRegistry`` + ``@register_calendar_provider`` — pluggable
  providers (swap storage with one config line)

All timestamps are UTC ISO-8601 strings. Events are always returned sorted
by ``start_time`` ascending.

Configuration (``configs/default.yaml``)::

    calendar:
      enabled: true
      provider: memory   # or a registered custom provider
      max_events: 10000

Register a custom provider::

    from agentbase.core.calendar import register_calendar_provider

    @register_calendar_provider("postgres")
    class PostgresCalendarProvider:
        def create_event(self, event): ...
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "CalendarEvent",
    "CalendarFilter",
    "CalendarStats",
    "CalendarProvider",
    "NullCalendarProvider",
    "InMemoryCalendarProvider",
    "CalendarRegistry",
    "calendar_registry",
    "register_calendar_provider",
    "CalendarManager",
    "get_calendar_manager",
    "set_calendar_manager",
    "reset_calendar_manager",
]

# Valid event statuses
EVENT_STATUSES = ("confirmed", "tentative", "cancelled")

# Safety limits
_MAX_TITLE_LENGTH = 200        # 标题最大长度（字符）
_MAX_DESCRIPTION_LENGTH = 8_000  # 描述最大长度（字符）
_MAX_ATTENDEES = 100           # 参与者上限
_MAX_REMINDER_MINUTES = 60 * 24 * 30  # 提醒最远提前 30 天
_DEFAULT_MAX_EVENTS = 10_000   # 默认容量（FIFO 淘汰）


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(value: str | datetime) -> datetime | None:
    """Parse an ISO-8601 string (or pass through a datetime) to aware UTC.

    Naive datetimes are treated as UTC. Returns None when unparseable.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CalendarEvent:
    """A single calendar event (日程条目).

    Attributes:
        event_id: Unique ID (auto-generated UUID when empty).
        title: Human-readable title (required, max 200 chars).
        start_time: ISO-8601 UTC start timestamp (required).
        end_time: ISO-8601 UTC end timestamp (required, > start_time).
        location: Optional location string.
        attendees: Optional attendee list (max 100).
        description: Optional free-text description (max 8000 chars).
        tags: Optional tag list.
        status: ``confirmed`` | ``tentative`` | ``cancelled``.
        reminder_minutes: Optional reminder offset in minutes before start.
        metadata: Extensible key-value metadata.
        created_at / updated_at: Auto-maintained timestamps.
    """

    title: str
    start_time: str
    end_time: str
    event_id: str = ""
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "confirmed"
    reminder_minutes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict form (JSON-safe)."""
        return {
            "event_id": self.event_id,
            "title": self.title,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "location": self.location,
            "attendees": list(self.attendees),
            "description": self.description,
            "tags": list(self.tags),
            "status": self.status,
            "reminder_minutes": self.reminder_minutes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalendarEvent:
        """Build an event from a dict (unknown keys ignored)."""
        return cls(
            event_id=data.get("event_id", ""),
            title=data.get("title", ""),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            location=data.get("location", ""),
            attendees=list(data.get("attendees", [])),
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            status=data.get("status", "confirmed"),
            reminder_minutes=data.get("reminder_minutes"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", ""),
        )


@dataclass
class CalendarFilter:
    """Filter + pagination for ``list_events``.

    Attributes:
        status: Exact status match (``confirmed`` / ``tentative`` / ``cancelled``).
        tag: Match events containing this tag.
        location: Substring match on location (case-insensitive).
        attendee: Match events containing this attendee.
        since: Only events ending at/after this ISO timestamp.
        until: Only events starting at/before this ISO timestamp.
        upcoming_only: Only events with ``start_time >= now``.
        limit / offset: Pagination applied after sorting.
    """

    status: str | None = None
    tag: str | None = None
    location: str | None = None
    attendee: str | None = None
    since: str | None = None
    until: str | None = None
    upcoming_only: bool = False
    limit: int | None = None
    offset: int = 0


@dataclass
class CalendarStats:
    """Aggregate calendar statistics."""

    total: int = 0
    upcoming: int = 0
    past: int = 0
    cancelled: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_tag: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "upcoming": self.upcoming,
            "past": self.past,
            "cancelled": self.cancelled,
            "by_status": dict(self.by_status),
            "by_tag": dict(self.by_tag),
        }


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CalendarProvider(Protocol):
    """Storage contract for calendar events."""

    def create_event(self, event: CalendarEvent) -> CalendarEvent: ...
    def get_event(self, event_id: str) -> CalendarEvent | None: ...
    def list_events(self, filter: CalendarFilter | None = None) -> list[CalendarEvent]: ...
    def update_event(self, event_id: str, changes: dict[str, Any]) -> CalendarEvent | None: ...
    def delete_event(self, event_id: str) -> bool: ...
    def get_stats(self) -> CalendarStats: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Null provider (disabled mode)
# ---------------------------------------------------------------------------

class NullCalendarProvider:
    """No-op calendar provider — all operations return empty/None.

    Used when the calendar service is disabled (``calendar.enabled=false``).
    """

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        return event

    def get_event(self, event_id: str) -> CalendarEvent | None:
        return None

    def list_events(self, filter: CalendarFilter | None = None) -> list[CalendarEvent]:
        return []

    def update_event(self, event_id: str, changes: dict[str, Any]) -> CalendarEvent | None:
        return None

    def delete_event(self, event_id: str) -> bool:
        return False

    def get_stats(self) -> CalendarStats:
        return CalendarStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryCalendarProvider:
    """In-memory calendar store — thread-safe with FIFO eviction.

    Args:
        max_events: Max stored events before oldest are evicted.
    """

    def __init__(self, max_events: int = _DEFAULT_MAX_EVENTS) -> None:
        self._events: dict[str, CalendarEvent] = {}
        self._order: list[str] = []  # insertion order for FIFO eviction
        self._lock = threading.RLock()
        self._max_events = max(1, int(max_events))

    # -- internal helpers ---------------------------------------------------

    def _evict_locked(self) -> None:
        while len(self._order) > self._max_events:
            oldest = self._order.pop(0)
            self._events.pop(oldest, None)
            logger.debug(
                "Calendar event evicted: %s",
                oldest,
                extra={"event": "calendar.evicted", "event_id": oldest},
            )

    # -- CalendarProvider ----------------------------------------------------

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        with self._lock:
            if event.event_id in self._events:
                raise RegistryError(f"Calendar event already exists: {event.event_id}")
            self._events[event.event_id] = event
            self._order.append(event.event_id)
            self._evict_locked()
        logger.info(
            "Calendar event created: %s (%s)",
            event.title[:60],
            event.event_id,
            extra={"event": "calendar.created", "event_id": event.event_id},
        )
        return event

    def get_event(self, event_id: str) -> CalendarEvent | None:
        with self._lock:
            return self._events.get(event_id)

    def list_events(self, filter: CalendarFilter | None = None) -> list[CalendarEvent]:
        with self._lock:
            events = list(self._events.values())
        events = _apply_calendar_filter(events, filter)
        return events

    def update_event(self, event_id: str, changes: dict[str, Any]) -> CalendarEvent | None:
        with self._lock:
            event = self._events.get(event_id)
            if event is None:
                return None
            for key, value in changes.items():
                if hasattr(event, key):
                    setattr(event, key, value)
            event.updated_at = _now_iso()
            logger.info(
                "Calendar event updated: %s",
                event_id,
                extra={"event": "calendar.updated", "event_id": event_id},
            )
            return event

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            if event_id not in self._events:
                return False
            self._events.pop(event_id, None)
            if event_id in self._order:
                self._order.remove(event_id)
        logger.info(
            "Calendar event deleted: %s",
            event_id,
            extra={"event": "calendar.deleted", "event_id": event_id},
        )
        return True

    def get_stats(self) -> CalendarStats:
        with self._lock:
            events = list(self._events.values())
        now = _now()
        stats = CalendarStats(total=len(events))
        for ev in events:
            start = _parse_iso(ev.start_time)
            if start is not None and start >= now:
                stats.upcoming += 1
            else:
                stats.past += 1
            stats.by_status[ev.status] = stats.by_status.get(ev.status, 0) + 1
            for tag in ev.tags:
                stats.by_tag[tag] = stats.by_tag.get(tag, 0) + 1
        stats.cancelled = stats.by_status.get("cancelled", 0)
        return stats

    def close(self) -> None:
        with self._lock:
            self._events.clear()
            self._order.clear()


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------

def _apply_calendar_filter(
    events: list[CalendarEvent], flt: CalendarFilter | None
) -> list[CalendarEvent]:
    """Apply filter criteria then sort by start_time asc and paginate."""
    if flt is not None:
        now = _now()
        since = _parse_iso(flt.since) if flt.since else None
        until = _parse_iso(flt.until) if flt.until else None

        def keep(ev: CalendarEvent) -> bool:
            start = _parse_iso(ev.start_time)
            end = _parse_iso(ev.end_time)
            if flt.status is not None and ev.status != flt.status:
                return False
            if flt.tag is not None and flt.tag not in ev.tags:
                return False
            if flt.location is not None and flt.location.lower() not in ev.location.lower():
                return False
            if flt.attendee is not None and flt.attendee not in ev.attendees:
                return False
            if since is not None and (end is None or end < since):
                return False
            if until is not None and (start is None or start > until):
                return False
            if flt.upcoming_only and (start is None or start < now):
                return False
            return True

        events = [ev for ev in events if keep(ev)]

    events = sorted(events, key=lambda ev: (ev.start_time, ev.event_id))
    if flt is not None:
        if flt.offset > 0:
            events = events[flt.offset:]
        if flt.limit is not None and flt.limit >= 0:
            events = events[: flt.limit]
    return events


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class CalendarRegistry:
    """Thread-safe registry for calendar providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., CalendarProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., CalendarProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty calendar provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Calendar provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> CalendarProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown calendar provider: {key}. Available: {available}"
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
calendar_registry = CalendarRegistry()

# Register defaults
calendar_registry.register("null", NullCalendarProvider)
calendar_registry.register("memory", InMemoryCalendarProvider)


def register_calendar_provider(name: str, *, override: bool = False):
    """Decorator: register a calendar provider class.

    Usage::

        @register_calendar_provider("postgres")
        class PostgresCalendarProvider:
            def create_event(self, event): ...
    """
    def decorator(factory: Callable[..., CalendarProvider]):
        calendar_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class CalendarManager:
    """High-level calendar manager.

    Wraps a ``CalendarProvider`` for event CRUD, filtered queries, and
    statistics, with input validation (time format, ordering, status
    whitelist, safety limits). When ``enabled=False`` it wraps a
    ``NullCalendarProvider`` (no-op).

    Usage::

        manager = CalendarManager(provider="memory", enabled=True)
        manager.create_event(
            title="Team sync",
            start_time="2026-09-01T09:00:00+00:00",
            end_time="2026-09-01T10:00:00+00:00",
            attendees=["alice@example.com"],
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
            self._provider: CalendarProvider = NullCalendarProvider()
        else:
            self._provider = calendar_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> CalendarProvider:
        return self._provider

    # -- validation -----------------------------------------------------------

    @staticmethod
    def _validate_times(start_time: str, end_time: str) -> None:
        start = _parse_iso(start_time)
        if start is None:
            raise RegistryError(f"Invalid start_time (ISO-8601 expected): {start_time!r}")
        end = _parse_iso(end_time)
        if end is None:
            raise RegistryError(f"Invalid end_time (ISO-8601 expected): {end_time!r}")
        if end <= start:
            raise RegistryError("end_time must be after start_time")

    # -- CRUD -----------------------------------------------------------------

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        location: str = "",
        attendees: list[str] | None = None,
        description: str = "",
        tags: list[str] | None = None,
        status: str = "confirmed",
        reminder_minutes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CalendarEvent:
        """Create a calendar event.

        Args:
            title: Event title (required, max 200 chars).
            start_time / end_time: ISO-8601 timestamps (required, end > start).
            location: Optional location.
            attendees: Optional attendee list (max 100).
            description: Optional description (max 8000 chars).
            tags: Optional tags.
            status: ``confirmed`` / ``tentative`` / ``cancelled``.
            reminder_minutes: Optional reminder offset (0 .. 43200).
            metadata: Extensible metadata.

        Returns:
            The stored ``CalendarEvent`` with ID set.

        Raises:
            RegistryError: On invalid or out-of-limit input.
        """
        if not title or not title.strip():
            raise RegistryError("title is required for calendar event")
        title = title.strip()
        if len(title) > _MAX_TITLE_LENGTH:
            raise RegistryError(
                f"title too long: {len(title)} (max {_MAX_TITLE_LENGTH})"
            )
        self._validate_times(start_time, end_time)
        if len(description) > _MAX_DESCRIPTION_LENGTH:
            raise RegistryError(
                f"description too long: {len(description)} (max {_MAX_DESCRIPTION_LENGTH})"
            )
        attendees = list(attendees or [])
        if len(attendees) > _MAX_ATTENDEES:
            raise RegistryError(
                f"too many attendees: {len(attendees)} (max {_MAX_ATTENDEES})"
            )
        status = (status or "confirmed").strip().lower()
        if status not in EVENT_STATUSES:
            raise RegistryError(
                f"Invalid status: {status!r} (use {'/'.join(EVENT_STATUSES)})"
            )
        if reminder_minutes is not None:
            reminder_minutes = int(reminder_minutes)
            if reminder_minutes < 0 or reminder_minutes > _MAX_REMINDER_MINUTES:
                raise RegistryError(
                    f"reminder_minutes out of range: {reminder_minutes} "
                    f"(0 .. {_MAX_REMINDER_MINUTES})"
                )

        event = CalendarEvent(
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location,
            attendees=attendees,
            description=description,
            tags=list(tags or []),
            status=status,
            reminder_minutes=reminder_minutes,
            metadata=metadata or {},
        )
        return self._provider.create_event(event)

    def get_event(self, event_id: str) -> CalendarEvent | None:
        """Get an event by ID (None when missing)."""
        return self._provider.get_event(event_id)

    def list_events(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
        location: str | None = None,
        attendee: str | None = None,
        since: str | None = None,
        until: str | None = None,
        upcoming_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CalendarEvent]:
        """List events (sorted by start_time asc) with optional filters."""
        flt = CalendarFilter(
            status=status,
            tag=tag,
            location=location,
            attendee=attendee,
            since=since,
            until=until,
            upcoming_only=upcoming_only,
            limit=limit,
            offset=offset,
        )
        return self._provider.list_events(flt)

    def update_event(self, event_id: str, changes: dict[str, Any]) -> CalendarEvent | None:
        """Update an event's fields (validates time/status fields when changed).

        Returns the updated event, or None when the ID is unknown.
        """
        current = self._provider.get_event(event_id)
        if current is None:
            return None
        merged = {
            "start_time": current.start_time,
            "end_time": current.end_time,
            "status": current.status,
            "title": current.title,
            "description": current.description,
            "attendees": current.attendees,
            "reminder_minutes": current.reminder_minutes,
        }
        merged.update({k: v for k, v in changes.items() if k in merged})
        if not merged["title"] or not str(merged["title"]).strip():
            raise RegistryError("title cannot be empty")
        if len(str(merged["title"])) > _MAX_TITLE_LENGTH:
            raise RegistryError(
                f"title too long (max {_MAX_TITLE_LENGTH})"
            )
        self._validate_times(str(merged["start_time"]), str(merged["end_time"]))
        status = str(merged["status"]).strip().lower()
        if status not in EVENT_STATUSES:
            raise RegistryError(
                f"Invalid status: {status!r} (use {'/'.join(EVENT_STATUSES)})"
            )
        if merged["reminder_minutes"] is not None:
            rm = int(merged["reminder_minutes"])
            if rm < 0 or rm > _MAX_REMINDER_MINUTES:
                raise RegistryError(f"reminder_minutes out of range (0 .. {_MAX_REMINDER_MINUTES})")
        if len(merged["attendees"] or []) > _MAX_ATTENDEES:
            raise RegistryError(f"too many attendees (max {_MAX_ATTENDEES})")
        if len(str(merged["description"])) > _MAX_DESCRIPTION_LENGTH:
            raise RegistryError(f"description too long (max {_MAX_DESCRIPTION_LENGTH})")

        allowed = {
            "title", "start_time", "end_time", "location", "attendees",
            "description", "tags", "status", "reminder_minutes", "metadata",
        }
        return self._provider.update_event(
            event_id, {k: v for k, v in changes.items() if k in allowed}
        )

    def delete_event(self, event_id: str) -> bool:
        """Delete an event. Returns True when deleted, False when missing."""
        return self._provider.delete_event(event_id)

    def get_stats(self) -> CalendarStats:
        """Aggregate statistics (total / upcoming / past / by_status / by_tag)."""
        return self._provider.get_stats()

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_calendar_manager: CalendarManager | None = None
_calendar_manager_lock = threading.Lock()


def get_calendar_manager() -> CalendarManager:
    """Get the module-level CalendarManager (creates a disabled one if unset)."""
    global _calendar_manager
    if _calendar_manager is None:
        with _calendar_manager_lock:
            if _calendar_manager is None:
                _calendar_manager = CalendarManager()
    return _calendar_manager


def set_calendar_manager(manager: CalendarManager) -> None:
    """Set the module-level CalendarManager (used by bootstrap/API wiring)."""
    global _calendar_manager
    with _calendar_manager_lock:
        _calendar_manager = manager


def reset_calendar_manager() -> None:
    """Reset the singleton (used by tests)."""
    global _calendar_manager
    with _calendar_manager_lock:
        _calendar_manager = None
