"""Webhook event notification service.

Registers webhook endpoints and delivers event notifications via HTTP POST
when key lifecycle events occur (agent invoked, stream completed, errors,
etc.).  This is a standard capability of any AI backend platform — it allows
external systems to subscribe to real-time events without polling.

Pluggable storage:
- ``InMemoryWebhookProvider`` (default) — zero-config, thread-safe, in-process
- ``NullWebhookProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_webhook_provider("name")``

Delivery:
- ``httpx`` is used when available (async, connection-pooling).
- Falls back to ``urllib.request`` (stdlib) when httpx is not installed.
- Each delivery runs in a background thread to avoid blocking the caller.
- Retry with exponential backoff (configurable, default 3 attempts).

Usage::

    from agentbase.core.webhook import WebhookManager, WebhookEndpoint

    manager = WebhookManager(provider="memory", enabled=True)

    manager.register_endpoint(
        url="https://example.com/webhook",
        events=["agent.invoke.completed", "agent.invoke.failed"],
        secret="my-signing-secret",
    )

    manager.dispatch_event(
        event="agent.invoke.completed",
        payload={"agent": "default", "thread_id": "abc123", "duration_ms": 1234.5},
    )
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
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
class WebhookEndpoint:
    """A registered webhook endpoint — a URL to receive event notifications.

    Attributes:
        url: Target URL for HTTP POST delivery.
        events: List of event types to subscribe to (``["*"]`` = all events).
        secret: Optional signing secret for HMAC-SHA256 payload verification.
        active: Whether this endpoint is currently receiving events.
        description: Human-readable description.
        created_at: ISO 8601 UTC timestamp (auto-set).
        updated_at: ISO 8601 UTC timestamp (auto-set).
        id: Auto-assigned endpoint ID.
    """

    url: str
    events: list[str] = field(default_factory=lambda: ["*"])
    secret: str = ""
    active: bool = True
    description: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]

    def matches(self, event: str) -> bool:
        """Check if this endpoint subscribes to the given event type."""
        if not self.active:
            return False
        if "*" in self.events:
            return True
        # Support wildcard prefix matching (e.g. "agent.invoke.*")
        for pattern in self.events:
            if pattern == event:
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if event.startswith(prefix + "."):
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "events": list(self.events),
            "secret": "***" if self.secret else "",
            "active": self.active,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class WebhookDelivery:
    """A single delivery attempt record.

    Attributes:
        endpoint_id: The endpoint ID that was targeted.
        endpoint_url: The URL that was called.
        event: The event type that triggered the delivery.
        payload: The JSON payload that was sent.
        status: Delivery status — ``"success"``, ``"failed"``, ``"pending"``.
        status_code: HTTP status code from the response (0 if not delivered).
        error: Error message if the delivery failed.
        attempts: Number of delivery attempts made.
        timestamp: ISO 8601 UTC timestamp.
        id: Auto-assigned delivery ID.
    """

    endpoint_id: str
    endpoint_url: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    status_code: int = 0
    error: str = ""
    attempts: int = 0
    timestamp: str = field(default_factory=_now)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "endpoint_id": self.endpoint_id,
            "endpoint_url": self.endpoint_url,
            "event": self.event,
            "payload": self.payload,
            "status": self.status,
            "status_code": self.status_code,
            "error": self.error,
            "attempts": self.attempts,
            "timestamp": self.timestamp,
        }


@dataclass
class WebhookDeliveryFilter:
    """Filter criteria for querying delivery records.

    All fields are optional — ``None`` means "no filter on this field".
    """

    endpoint_id: str | None = None
    event: str | None = None
    status: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class WebhookStats:
    """Aggregate webhook delivery statistics.

    Attributes:
        total_endpoints: Number of registered endpoints.
        active_endpoints: Number of active endpoints.
        total_deliveries: Total delivery attempts.
        successful_deliveries: Number of successful deliveries.
        failed_deliveries: Number of failed deliveries.
        success_rate: Success rate (0.0–1.0).
        by_event: Per-event delivery counts.
    """

    total_endpoints: int = 0
    active_endpoints: int = 0
    total_deliveries: int = 0
    successful_deliveries: int = 0
    failed_deliveries: int = 0
    success_rate: float = 0.0
    by_event: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_endpoints": self.total_endpoints,
            "active_endpoints": self.active_endpoints,
            "total_deliveries": self.total_deliveries,
            "successful_deliveries": self.successful_deliveries,
            "failed_deliveries": self.failed_deliveries,
            "success_rate": round(self.success_rate, 4),
            "by_event": dict(self.by_event),
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class WebhookProvider(Protocol):
    """Protocol for webhook storage providers.

    Implementations must be thread-safe.
    """

    def register_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        """Register or update a webhook endpoint. Returns the stored endpoint."""
        ...

    def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        """Get an endpoint by ID. Returns None if not found."""
        ...

    def list_endpoints(self, *, active_only: bool = False) -> list[WebhookEndpoint]:
        """List all registered endpoints."""
        ...

    def delete_endpoint(self, endpoint_id: str) -> bool:
        """Delete an endpoint. Returns True if deleted."""
        ...

    def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """Record a delivery attempt. Returns the delivery with ID."""
        ...

    def query_deliveries(self, filter: WebhookDeliveryFilter | None = None) -> list[WebhookDelivery]:
        """Query delivery records matching the filter."""
        ...

    def get_stats(self) -> WebhookStats:
        """Get aggregate webhook statistics."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Null provider (no-op, zero overhead)
# ---------------------------------------------------------------------------

class NullWebhookProvider:
    """No-op webhook provider — all operations return empty/None.

    Used when webhook notifications are disabled (``webhook.enabled=false``).
    """

    def register_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        return None

    def list_endpoints(self, *, active_only: bool = False) -> list[WebhookEndpoint]:
        return []

    def delete_endpoint(self, endpoint_id: str) -> bool:
        return False

    def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        return delivery

    def query_deliveries(self, filter: WebhookDeliveryFilter | None = None) -> list[WebhookDelivery]:
        return []

    def get_stats(self) -> WebhookStats:
        return WebhookStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryWebhookProvider:
    """In-memory webhook provider — thread-safe, zero-config.

    Stores endpoints and delivery records in memory.
    All data is lost on process restart.
    """

    def __init__(self, max_deliveries: int = 10_000) -> None:
        self._lock = threading.RLock()
        self._endpoints: dict[str, WebhookEndpoint] = {}
        self._deliveries: list[WebhookDelivery] = []
        self._max_deliveries = max_deliveries

    def register_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        with self._lock:
            endpoint.updated_at = _now()
            if endpoint.id not in self._endpoints:
                endpoint.created_at = _now()
            self._endpoints[endpoint.id] = endpoint
            logger.info(
                "Webhook endpoint registered: id=%s url=%s events=%s",
                endpoint.id,
                endpoint.url,
                endpoint.events,
                extra={
                    "event": "webhook.endpoint_registered",
                    "endpoint_id": endpoint.id,
                    "url": endpoint.url,
                },
            )
            return endpoint

    def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        with self._lock:
            return self._endpoints.get(endpoint_id)

    def list_endpoints(self, *, active_only: bool = False) -> list[WebhookEndpoint]:
        with self._lock:
            endpoints = list(self._endpoints.values())
        if active_only:
            endpoints = [e for e in endpoints if e.active]
        return endpoints

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self._lock:
            if endpoint_id not in self._endpoints:
                return False
            del self._endpoints[endpoint_id]
            logger.info(
                "Webhook endpoint deleted: id=%s",
                endpoint_id,
                extra={"event": "webhook.endpoint_deleted", "endpoint_id": endpoint_id},
            )
            return True

    def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        with self._lock:
            self._deliveries.append(delivery)
            if len(self._deliveries) > self._max_deliveries:
                excess = len(self._deliveries) - self._max_deliveries
                del self._deliveries[:excess]
            return delivery

    def query_deliveries(self, filter: WebhookDeliveryFilter | None = None) -> list[WebhookDelivery]:
        with self._lock:
            records = list(self._deliveries)
        if filter is None:
            return records
        return _apply_delivery_filter(records, filter)

    def get_stats(self) -> WebhookStats:
        with self._lock:
            endpoints = list(self._endpoints.values())
            deliveries = list(self._deliveries)

        total_ep = len(endpoints)
        active_ep = sum(1 for e in endpoints if e.active)
        total_dv = len(deliveries)
        success_dv = sum(1 for d in deliveries if d.status == "success")
        failed_dv = sum(1 for d in deliveries if d.status == "failed")

        by_event: dict[str, int] = {}
        for d in deliveries:
            by_event[d.event] = by_event.get(d.event, 0) + 1

        return WebhookStats(
            total_endpoints=total_ep,
            active_endpoints=active_ep,
            total_deliveries=total_dv,
            successful_deliveries=success_dv,
            failed_deliveries=failed_dv,
            success_rate=success_dv / total_dv if total_dv > 0 else 0.0,
            by_event=by_event,
        )

    def close(self) -> None:
        with self._lock:
            self._endpoints.clear()
            self._deliveries.clear()


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_delivery_filter(
    records: list[WebhookDelivery],
    flt: WebhookDeliveryFilter,
) -> list[WebhookDelivery]:
    """Apply filter criteria to a list of delivery records."""
    result: list[WebhookDelivery] = []
    for r in records:
        if flt.endpoint_id is not None and r.endpoint_id != flt.endpoint_id:
            continue
        if flt.event is not None and r.event != flt.event:
            continue
        if flt.status is not None and r.status != flt.status:
            continue
        if flt.since is not None and r.timestamp < flt.since:
            continue
        if flt.until is not None and r.timestamp >= flt.until:
            continue
        result.append(r)
    if flt.offset > 0:
        result = result[flt.offset:]
    if flt.limit > 0:
        result = result[:flt.limit]
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class WebhookRegistry:
    """Thread-safe registry for webhook providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., WebhookProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., WebhookProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty webhook provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Webhook provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> WebhookProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown webhook provider: {key}. Available: {available}"
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
webhook_registry = WebhookRegistry()

# Register defaults
webhook_registry.register("null", NullWebhookProvider)
webhook_registry.register("memory", InMemoryWebhookProvider)


def register_webhook_provider(name: str, *, override: bool = False):
    """Decorator: register a webhook provider class.

    Usage::

        @register_webhook_provider("redis")
        class RedisWebhookProvider:
            def register_endpoint(self, endpoint): ...
    """
    def decorator(factory: Callable[..., WebhookProvider]):
        webhook_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# HTTP delivery
# ---------------------------------------------------------------------------

def _sign_payload(payload: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for the payload.

    Returns an empty string when ``secret`` is empty (no signing).
    """
    if not secret:
        return ""
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def _deliver_httpx(
    url: str,
    body: bytes,
    signature: str,
    timeout: float,
    headers: dict[str, str],
) -> tuple[int, str]:
    """Deliver via httpx. Returns (status_code, error)."""
    import httpx  # type: ignore[import-untyped]

    all_headers = {**headers, "Content-Type": "application/json"}
    if signature:
        all_headers["X-Webhook-Signature"] = signature

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, content=body, headers=all_headers)
        return resp.status_code, ""


def _deliver_urllib(
    url: str,
    body: bytes,
    signature: str,
    timeout: float,
    headers: dict[str, str],
) -> tuple[int, str]:
    """Deliver via urllib (stdlib fallback). Returns (status_code, error)."""
    import urllib.error
    import urllib.request

    all_headers = {**headers, "Content-Type": "application/json"}
    if signature:
        all_headers["X-Webhook-Signature"] = signature

    req = urllib.request.Request(
        url,
        data=body,
        headers=all_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:
        return 0, str(exc)


def _deliver(
    url: str,
    payload: dict[str, Any],
    secret: str,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Deliver a payload to a webhook URL.

    Returns (status_code, error_message).
    Tries httpx first, falls back to urllib.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signature = _sign_payload(body, secret) if secret else ""
    headers = extra_headers or {}

    try:
        return _deliver_httpx(url, body, signature, timeout, headers)
    except ImportError:
        return _deliver_urllib(url, body, signature, timeout, headers)


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class WebhookManager:
    """High-level webhook event notification manager.

    Wraps a ``WebhookProvider`` for endpoint storage and delivery records.
    When ``enabled=False``, uses ``NullWebhookProvider`` (no-op).

    Event delivery runs in a background thread to avoid blocking the caller.
    Each delivery is retried with exponential backoff on failure.

    Usage::

        manager = WebhookManager(provider="memory", enabled=True)
        manager.register_endpoint(url="https://example.com/hook")
        manager.dispatch_event(
            event="agent.invoke.completed",
            payload={"agent": "default", "thread_id": "abc"},
        )
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._retry_backoff = retry_backoff
        if not enabled:
            self._provider: WebhookProvider = NullWebhookProvider()
        else:
            self._provider = webhook_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> WebhookProvider:
        return self._provider

    def register_endpoint(
        self,
        *,
        url: str,
        events: list[str] | None = None,
        secret: str = "",
        description: str = "",
        active: bool = True,
    ) -> WebhookEndpoint:
        """Register a new webhook endpoint."""
        if not url or not url.startswith(("http://", "https://")):
            raise RegistryError(
                f"Invalid webhook URL: {url}. Must start with http:// or https://"
            )
        endpoint = WebhookEndpoint(
            url=url,
            events=events or ["*"],
            secret=secret,
            description=description,
            active=active,
        )
        return self._provider.register_endpoint(endpoint)

    def update_endpoint(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        secret: str | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Update an existing endpoint. Returns the updated endpoint or None."""
        endpoint = self._provider.get_endpoint(endpoint_id)
        if endpoint is None:
            return None
        if url is not None:
            if not url.startswith(("http://", "https://")):
                raise RegistryError(
                    f"Invalid webhook URL: {url}. Must start with http:// or https://"
                )
            endpoint.url = url
        if events is not None:
            endpoint.events = events
        if secret is not None:
            endpoint.secret = secret
        if description is not None:
            endpoint.description = description
        if active is not None:
            endpoint.active = active
        return self._provider.register_endpoint(endpoint)

    def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        """Get an endpoint by ID."""
        return self._provider.get_endpoint(endpoint_id)

    def list_endpoints(self, *, active_only: bool = False) -> list[WebhookEndpoint]:
        """List all registered endpoints."""
        return self._provider.list_endpoints(active_only=active_only)

    def delete_endpoint(self, endpoint_id: str) -> bool:
        """Delete an endpoint."""
        return self._provider.delete_endpoint(endpoint_id)

    def dispatch_event(
        self,
        *,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Dispatch an event to all matching endpoints.

        Delivery runs in a background thread. Returns the number of endpoints
        that were targeted. No-op when disabled.
        """
        if not self._enabled:
            return 0

        payload = payload or {}
        endpoints = self._provider.list_endpoints(active_only=True)
        matched = [e for e in endpoints if e.matches(event)]
        if not matched:
            return 0

        # Run delivery in a background thread to avoid blocking
        thread = threading.Thread(
            target=self._deliver_to_endpoints,
            args=(matched, event, payload),
            daemon=True,
        )
        thread.start()
        return len(matched)

    def _deliver_to_endpoints(
        self,
        endpoints: list[WebhookEndpoint],
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Deliver an event to multiple endpoints (called in background)."""
        for endpoint in endpoints:
            self._deliver_single(endpoint, event, payload)

    def _deliver_single(
        self,
        endpoint: WebhookEndpoint,
        event: str,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Deliver an event to a single endpoint with retry logic."""
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            endpoint_url=endpoint.url,
            event=event,
            payload=payload,
            status="pending",
        )

        full_payload = {
            "event": event,
            "timestamp": _now(),
            "data": payload,
            "delivery_id": delivery.id,
        }

        last_error = ""
        last_status_code = 0

        for attempt in range(1, self._max_retries + 1):
            delivery.attempts = attempt
            try:
                status_code, error = _deliver(
                    url=endpoint.url,
                    payload=full_payload,
                    secret=endpoint.secret,
                    timeout=self._timeout,
                )
                last_status_code = status_code
                last_error = error

                # 2xx = success
                if 200 <= status_code < 300:
                    delivery.status = "success"
                    delivery.status_code = status_code
                    break

                # 4xx (except 429) = permanent failure, don't retry
                if 400 <= status_code < 500 and status_code != 429:
                    delivery.status = "failed"
                    delivery.status_code = status_code
                    delivery.error = f"HTTP {status_code}"
                    break

                # 5xx or 429 = retriable
                last_error = f"HTTP {status_code}"

            except Exception as exc:
                last_error = str(exc)
                last_status_code = 0

            # Retry with exponential backoff (except last attempt)
            if attempt < self._max_retries:
                time.sleep(self._retry_backoff * (2 ** (attempt - 1)))
        else:
            # All retries exhausted
            delivery.status = "failed"
            delivery.status_code = last_status_code
            delivery.error = last_error

        # Record the delivery
        self._provider.record_delivery(delivery)

        if delivery.status == "success":
            logger.debug(
                "Webhook delivered: endpoint=%s event=%s status=%d attempts=%d",
                endpoint.url,
                event,
                delivery.status_code,
                delivery.attempts,
                extra={
                    "event": "webhook.delivered",
                    "endpoint_id": endpoint.id,
                    "url": endpoint.url,
                    "event_type": event,
                    "status_code": delivery.status_code,
                },
            )
        else:
            logger.warning(
                "Webhook delivery failed: endpoint=%s event=%s error=%s attempts=%d",
                endpoint.url,
                event,
                delivery.error,
                delivery.attempts,
                extra={
                    "event": "webhook.delivery_failed",
                    "endpoint_id": endpoint.id,
                    "url": endpoint.url,
                    "event_type": event,
                    "error": delivery.error,
                },
            )

        return delivery

    def query_deliveries(self, filter: WebhookDeliveryFilter | None = None) -> list[WebhookDelivery]:
        """Query delivery records. Returns empty list when disabled."""
        return self._provider.query_deliveries(filter)

    def get_stats(self) -> WebhookStats:
        """Get aggregate statistics. Returns empty stats when disabled."""
        return self._provider.get_stats()

    def test_endpoint(self, endpoint_id: str) -> WebhookDelivery | None:
        """Send a test event to an endpoint.

        This is a synchronous delivery (not background) so the caller
        gets immediate feedback. Returns the delivery record, or None
        if the endpoint doesn't exist.
        """
        endpoint = self._provider.get_endpoint(endpoint_id)
        if endpoint is None:
            return None
        return self._deliver_single(
            endpoint,
            "webhook.test",
            {"message": "Test webhook delivery from AgentBase"},
        )

    def close(self) -> None:
        self._provider.close()


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_webhook_manager: WebhookManager | None = None
_webhook_manager_lock = threading.Lock()


def get_webhook_manager() -> WebhookManager:
    """Get the global WebhookManager singleton.

    Raises ``RuntimeError`` if not initialised — call ``set_webhook_manager``
    first (typically during application bootstrap).
    """
    if _webhook_manager is None:
        with _webhook_manager_lock:
            if _webhook_manager is None:
                raise RuntimeError(
                    "WebhookManager not initialised. Call set_webhook_manager() first."
                )
    return _webhook_manager  # type: ignore[return-value]


def set_webhook_manager(manager: WebhookManager) -> None:
    """Set the global WebhookManager singleton."""
    global _webhook_manager
    with _webhook_manager_lock:
        _webhook_manager = manager


def reset_webhook_manager() -> None:
    """Reset the global WebhookManager singleton (for testing)."""
    global _webhook_manager
    with _webhook_manager_lock:
        _webhook_manager = None
