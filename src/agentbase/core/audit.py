"""Audit log service — structured recording of critical operations.

Records who did what, when, and the outcome. Designed for enterprise
compliance and security forensics.

Pluggable storage:
- ``SQLiteAuditProvider`` (default) — zero-config, uses SQLite
- ``NullAuditProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_audit_provider("name")``

Usage::

    from agentbase.core.audit import audit_registry, AuditEvent

    provider = audit_registry.get("sqlite")
    provider.record(AuditEvent(
        actor="user@example.com",
        action="agent.invoke",
        resource="agent:default",
        result="success",
        detail={"thread_id": "abc123"},
    ))
    events = provider.query(actor="user@example.com", limit=50)
    provider.export("/tmp/audit_log.json")
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from agentbase.core.storage import StorageBackend, create_storage
from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AuditEvent:
    """A single audit log entry — records a critical operation.

    Attributes:
        actor: Who performed the action (user ID, agent name, system).
        action: What was done (e.g. ``"agent.invoke"``, ``"document.delete"``).
        resource: What was acted upon (e.g. ``"agent:default"``, ``"doc:42"``).
        result: Outcome — ``"success"``, ``"failure"``, ``"denied"``.
        detail: Arbitrary structured metadata (request_id, IP, etc.).
        timestamp: ISO 8601 UTC timestamp (auto-set on record).
        id: Auto-assigned record ID (from database).
    """

    actor: str
    action: str
    resource: str = ""
    result: str = "success"
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditFilter:
    """Filter criteria for querying audit events.

    All fields are optional — ``None`` means "no filter on this field".
    """

    actor: str | None = None
    action: str | None = None
    resource: str | None = None
    result: str | None = None
    since: str | None = None  # ISO timestamp, inclusive
    until: str | None = None  # ISO timestamp, exclusive
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AuditLogProvider(Protocol):
    """Protocol for audit log providers.

    Implementations must be thread-safe.
    """

    def record(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit event. Returns the event with ID assigned."""
        ...

    def query(self, filter: AuditFilter | None = None) -> list[AuditEvent]:
        """Query audit events matching the filter."""
        ...

    def export(self, path: str, *, format: str = "json", filter: AuditFilter | None = None) -> int:
        """Export events to a file. Returns count exported."""
        ...

    def export_stream(self, *, format: str = "json", filter: AuditFilter | None = None) -> tuple[str, list[AuditEvent]]:
        """Export events as an in-memory string. Returns (content, events)."""
        ...

    def count(self, filter: AuditFilter | None = None) -> int:
        """Count events matching the filter."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullAuditProvider:
    """No-op audit provider — discards all events.

    Used when audit logging is disabled (``audit.enabled=false``).
    """

    def record(self, event: AuditEvent) -> AuditEvent:
        return event

    def query(self, filter: AuditFilter | None = None) -> list[AuditEvent]:
        return []

    def export(
        self,
        path: str,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> int:
        return 0

    def export_stream(
        self,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> tuple[str, list[AuditEvent]]:
        # Return format-appropriate empty content
        if format == "csv":
            import csv as _csv
            import io

            buf = io.StringIO()
            writer = _csv.writer(buf)
            writer.writerow([
                "id", "timestamp", "actor", "action",
                "resource", "result", "detail",
            ])
            return buf.getvalue(), []
        elif format == "yaml":
            return "[]\n", []
        return "[]", []

    def count(self, filter: AuditFilter | None = None) -> int:
        return 0

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# SQLite provider (default, zero-config)
# ---------------------------------------------------------------------------

def _row_to_event(row: Any) -> AuditEvent:
    """Convert a DB row to an AuditEvent."""
    def _get(key: str) -> Any:
        if hasattr(row, "__getitem__"):
            try:
                return row[key]
            except (KeyError, IndexError):
                return None
        return getattr(row, key, None)

    return AuditEvent(
        id=_get("id"),
        actor=_get("actor") or "",
        action=_get("action") or "",
        resource=_get("resource") or "",
        result=_get("result") or "success",
        detail=json.loads(_get("detail") or "{}"),
        timestamp=_get("timestamp") or "",
    )


class SQLiteAuditProvider:
    """SQLite-backed audit log provider.

    Zero-config: creates an ``audit_events`` table in the specified
    SQLite database. Thread-safe via ``threading.RLock``.

    Can also use PostgreSQL or MySQL via the unified ``StorageBackend``
    abstraction — pass a ``dsn`` instead of ``db_path``.

    Usage::

        # SQLite (dev)
        provider = SQLiteAuditProvider(db_path=Path("data/audit.db"))

        # PostgreSQL (prod)
        provider = SQLiteAuditProvider(dsn="postgresql://user:pass@host/db")
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        dsn: str | None = None,
        backend: StorageBackend | None = None,
    ) -> None:
        self._lock = threading.RLock()
        if backend is not None:
            self._db = backend
        else:
            self._db = create_storage(db_path=db_path, dsn=dsn)
        self._init_db()

    def _init_db(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor       TEXT NOT NULL,
                action      TEXT NOT NULL,
                resource    TEXT DEFAULT '',
                result      TEXT DEFAULT 'success',
                detail      TEXT DEFAULT '{}',
                timestamp   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
            """
        )
        self._db.commit()

    def record(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit event. Returns the event with ID assigned."""
        detail_json = json.dumps(event.detail, ensure_ascii=False)
        with self._lock:
            self._db.execute(
                """
                INSERT INTO audit_events (actor, action, resource, result, detail, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (event.actor, event.action, event.resource, event.result, detail_json, event.timestamp),
            )
            self._db.commit()
            event.id = self._db.last_insert_id()
        logger.debug(
            "Audit event recorded: actor=%s action=%s result=%s id=%s",
            event.actor, event.action, event.result, event.id,
            extra={"event": "audit.record", "actor": event.actor, "action": event.action},
        )
        return event

    def query(self, filter: AuditFilter | None = None) -> list[AuditEvent]:
        """Query audit events matching the filter."""
        f = filter or AuditFilter()
        conditions: list[str] = []
        params: list[Any] = []

        if f.actor is not None:
            conditions.append("actor = %s")
            params.append(f.actor)
        if f.action is not None:
            conditions.append("action = %s")
            params.append(f.action)
        if f.resource is not None:
            conditions.append("resource = %s")
            params.append(f.resource)
        if f.result is not None:
            conditions.append("result = %s")
            params.append(f.result)
        if f.since is not None:
            conditions.append("timestamp >= %s")
            params.append(f.since)
        if f.until is not None:
            conditions.append("timestamp < %s")
            params.append(f.until)

        sql = "SELECT * FROM audit_events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY timestamp DESC"
        # Clamp limit to prevent excessive result sets
        limit = max(1, min(f.limit, 10000))
        sql += f" LIMIT {limit}"
        if f.offset > 0:
            sql += f" OFFSET {f.offset}"

        rows = self._db.fetchall(sql, params)
        return [_row_to_event(r) for r in rows]

    def count(self, filter: AuditFilter | None = None) -> int:
        """Count events matching the filter."""
        f = filter or AuditFilter()
        conditions: list[str] = []
        params: list[Any] = []

        if f.actor is not None:
            conditions.append("actor = %s")
            params.append(f.actor)
        if f.action is not None:
            conditions.append("action = %s")
            params.append(f.action)
        if f.resource is not None:
            conditions.append("resource = %s")
            params.append(f.resource)
        if f.result is not None:
            conditions.append("result = %s")
            params.append(f.result)
        if f.since is not None:
            conditions.append("timestamp >= %s")
            params.append(f.since)
        if f.until is not None:
            conditions.append("timestamp < %s")
            params.append(f.until)

        sql = "SELECT COUNT(*) AS cnt FROM audit_events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        row = self._db.fetchone(sql, params)
        if row is None:
            return 0
        try:
            return row["cnt"]
        except (KeyError, IndexError):
            return getattr(row, "cnt", 0)

    def export(
        self,
        path: str,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> int:
        """Export events to a file. Returns count exported.

        Args:
            path: Output file path.
            format: ``"json"`` (default), ``"csv"``, or ``"yaml"``.
            filter: Optional filter criteria. When ``None``, exports all
                events (up to 10,000).
        """
        # Use provided filter or default (all events, up to 10k)
        flt = filter or AuditFilter(limit=10000)
        events = self.query(flt)
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "csv":
            import csv as _csv
            import io

            output_buf = io.StringIO()
            writer = _csv.writer(output_buf)
            # Header row
            writer.writerow([
                "id", "timestamp", "actor", "action",
                "resource", "result", "detail",
            ])
            for event in events:
                writer.writerow([
                    event.id or "",
                    event.timestamp,
                    event.actor,
                    event.action,
                    event.resource,
                    event.result,
                    json.dumps(event.detail, ensure_ascii=False, default=str),
                ])
            output_path.write_text(output_buf.getvalue(), encoding="utf-8")
        elif format == "yaml":
            import yaml
            data = [e.to_dict() for e in events]
            output_path.write_text(
                yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        else:
            data = [e.to_dict() for e in events]
            output_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        return len(events)

    def export_stream(
        self,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> tuple[str, list[AuditEvent]]:
        """Export events as an in-memory string. Returns (content, events).

        This is used by the API layer to stream exports via HTTP without
        writing to the server's filesystem.

        Args:
            format: ``"json"`` (default), ``"csv"``, or ``"yaml"``.
            filter: Optional filter criteria.

        Returns:
            A tuple of (serialized content, event list).
        """
        flt = filter or AuditFilter(limit=10000)
        events = self.query(flt)

        if format == "csv":
            import csv as _csv
            import io

            output_buf = io.StringIO()
            writer = _csv.writer(output_buf)
            writer.writerow([
                "id", "timestamp", "actor", "action",
                "resource", "result", "detail",
            ])
            for event in events:
                writer.writerow([
                    event.id or "",
                    event.timestamp,
                    event.actor,
                    event.action,
                    event.resource,
                    event.result,
                    json.dumps(event.detail, ensure_ascii=False, default=str),
                ])
            return output_buf.getvalue(), events
        elif format == "yaml":
            import yaml
            data = [e.to_dict() for e in events]
            return yaml.dump(
                data, default_flow_style=False, sort_keys=False, allow_unicode=True,
            ), events
        else:
            data = [e.to_dict() for e in events]
            return json.dumps(
                data, indent=2, ensure_ascii=False, default=str,
            ), events

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AuditRegistry:
    """Thread-safe registry for audit log providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., AuditLogProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., AuditLogProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty audit provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Audit provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> AuditLogProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(f"Unknown audit provider: {key}. Available: {available}")
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
audit_registry = AuditRegistry()

# Register defaults
audit_registry.register("null", NullAuditProvider)
audit_registry.register("sqlite", SQLiteAuditProvider)


def register_audit_provider(name: str, *, override: bool = False):
    """Decorator: register an audit log provider class.

    Usage::

        @register_audit_provider("elasticsearch")
        class ElasticsearchAuditProvider:
            def record(self, event: AuditEvent) -> AuditEvent: ...
    """
    def decorator(factory: Callable[..., AuditLogProvider]):
        audit_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class AuditManager:
    """High-level audit log manager.

    Wraps an ``AuditLogProvider`` and provides convenience methods.
    When ``enabled=False``, uses ``NullAuditProvider`` (no-op).

    Usage::

        manager = AuditManager(provider="sqlite", db_path=Path("data/audit.db"))
        manager.record_event(
            actor="admin",
            action="config.update",
            resource="agent:default",
            result="success",
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
            self._provider: AuditLogProvider = NullAuditProvider()
        else:
            self._provider = audit_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record_event(
        self,
        *,
        actor: str,
        action: str,
        resource: str = "",
        result: str = "success",
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record an audit event. No-op when disabled."""
        event = AuditEvent(
            actor=actor,
            action=action,
            resource=resource,
            result=result,
            detail=detail or {},
        )
        return self._provider.record(event)

    def query_events(self, filter: AuditFilter | None = None) -> list[AuditEvent]:
        """Query audit events. Returns empty list when disabled."""
        return self._provider.query(filter)

    def export_events(
        self,
        path: str,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> int:
        """Export events to a file. Returns 0 when disabled."""
        return self._provider.export(path, format=format, filter=filter)

    def export_events_stream(
        self,
        *,
        format: str = "json",
        filter: AuditFilter | None = None,
    ) -> tuple[str, list[AuditEvent]]:
        """Export events as an in-memory string. Returns empty when disabled.

        Args:
            format: ``"json"`` (default), ``"csv"``, or ``"yaml"``.
            filter: Optional filter criteria.

        Returns:
            A tuple of (serialized content, event list).
        """
        return self._provider.export_stream(format=format, filter=filter)

    def count_events(self, filter: AuditFilter | None = None) -> int:
        """Count events matching the filter. Returns 0 when disabled."""
        return self._provider.count(filter)

    def close(self) -> None:
        self._provider.close()
