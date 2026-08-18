"""Alert rule service — threshold-based metric monitoring with notifications.

Evaluates platform metrics against configurable alert rules on a
background tick loop and fires notifications when thresholds are
breached (and when they recover):

- Rule = metric + comparison operator + threshold + consecutive-breach
  requirement + cooldown + severity
- Evaluation is periodic (``tick_seconds``) and non-blocking — a failing
  reader/notifier never crashes the loop
- Alert history records every firing and recovery event

Supported metric names (aligned with ``MetricsCollector`` in the API
layer, injected at wiring time as a ``metrics_reader`` callback):

- ``requests_total``, ``errors_total``, ``documents_uploaded_total``
- ``queue_submitted_total``, ``queue_completed_total``, ``queue_failed_total``
- ``ws_active_connections``, ``active_sessions``
- ``latency_avg_ms`` (derived: latency sum / count)

The service is transport-agnostic: both the metric source
(``metrics_reader: Callable[[str], float]``) and the notification sink
(``notifier: Callable[..., Any]`` — typically ``NotificationManager.create``)
are dependency-injected, so the core never imports the API layer.

Pluggable backends:
- ``InMemoryAlertProvider`` (default) — zero-config, thread-safe,
  background tick thread, FIFO eviction for rules and alert records
- ``NullAlertProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_alert_provider("name")``

Usage::

    from agentbase.core.alert import AlertManager

    manager = AlertManager(provider="memory", enabled=True)
    manager.set_metrics_reader(lambda name: 42.0)
    manager.set_notifier(lambda **kw: None)

    manager.create_rule(
        name="high-error-rate",
        metric="errors_total",
        operator="gt",
        threshold=100,
    )
    manager.start()
    manager.tick()  # or wait for the background loop
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "SUPPORTED_METRICS",
    "OPERATORS",
    "AlertRule",
    "AlertEvent",
    "AlertFilter",
    "EventFilter",
    "AlertStats",
    "AlertProvider",
    "NullAlertProvider",
    "InMemoryAlertProvider",
    "AlertRegistry",
    "alert_registry",
    "register_alert_provider",
    "AlertManager",
    "get_alert_manager",
    "set_alert_manager",
    "reset_alert_manager",
]

# ---------------------------------------------------------------------------
# Constants and validation limits
# ---------------------------------------------------------------------------

SUPPORTED_METRICS: frozenset[str] = frozenset({
    "requests_total",
    "errors_total",
    "documents_uploaded_total",
    "queue_submitted_total",
    "queue_completed_total",
    "queue_failed_total",
    "ws_active_connections",
    "active_sessions",
    "latency_avg_ms",
})

OPERATORS: frozenset[str] = frozenset({"gt", "gte", "lt", "lte", "eq", "ne"})

_MAX_RULES = 500
_MAX_EVENTS = 5_000
_MAX_NAME_LENGTH = 64
_MAX_MESSAGE_LENGTH = 1_000
_MAX_DURATION_TICKS = 100
_MAX_COOLDOWN_SECONDS = 86_400  # 24h
_MIN_TICK_SECONDS = 1
_MAX_TICK_SECONDS = 3_600
_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
_DEFAULT_MAX_EVENTS = 1_000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(ts: str) -> float:
    """Parse an ISO-8601 timestamp into a UTC epoch float (0 on error)."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _compare(value: float, operator: str, threshold: float) -> bool:
    """Evaluate ``value <operator> threshold``."""
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    return value != threshold  # ne


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AlertRule:
    """A threshold rule over a named metric."""

    rule_id: str = ""
    name: str = ""
    metric: str = ""
    operator: str = "gt"
    threshold: float = 0.0
    severity: str = "warning"
    duration_ticks: int = 1  # consecutive breaches before firing
    cooldown_seconds: int = 300  # min seconds between repeated firings
    notify_user_id: str = "*"  # "*" = broadcast
    enabled: bool = True
    description: str = ""
    # runtime state (not user-configured)
    breach_count: int = 0
    last_fired_at: str | None = None
    state: str = "ok"  # ok | firing
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "severity": self.severity,
            "duration_ticks": self.duration_ticks,
            "cooldown_seconds": self.cooldown_seconds,
            "notify_user_id": self.notify_user_id,
            "enabled": self.enabled,
            "description": self.description,
            "breach_count": self.breach_count,
            "last_fired_at": self.last_fired_at,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AlertEvent:
    """A fired or resolved alert occurrence."""

    event_id: str = ""
    rule_id: str = ""
    rule_name: str = ""
    metric: str = ""
    value: float = 0.0
    threshold: float = 0.0
    operator: str = "gt"
    state: str = "firing"  # firing | resolved
    severity: str = "warning"
    message: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "operator": self.operator,
            "state": self.state,
            "severity": self.severity,
            "message": self.message,
            "created_at": self.created_at,
        }


@dataclass
class AlertFilter:
    """Filter criteria for listing rules."""

    enabled: bool | None = None
    metric: str | None = None
    severity: str | None = None
    state: str | None = None
    limit: int | None = None
    offset: int = 0


@dataclass
class EventFilter:
    """Filter criteria for listing alert events."""

    rule_id: str | None = None
    state: str | None = None  # firing | resolved
    severity: str | None = None
    metric: str | None = None
    since: str | None = None  # ISO-8601 lower bound
    until: str | None = None  # ISO-8601 upper bound
    limit: int | None = None
    offset: int = 0


@dataclass
class AlertStats:
    """Aggregate statistics over rules and recent events."""

    total_rules: int = 0
    enabled_rules: int = 0
    firing_rules: int = 0
    total_events: int = 0
    firing_events: int = 0
    resolved_events: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rules": self.total_rules,
            "enabled_rules": self.enabled_rules,
            "firing_rules": self.firing_rules,
            "total_events": self.total_events,
            "firing_events": self.firing_events,
            "resolved_events": self.resolved_events,
            "by_severity": dict(self.by_severity),
        }


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AlertProvider(Protocol):
    """Storage contract for alert rules and events."""

    def create_rule(self, rule: AlertRule) -> AlertRule: ...
    def get_rule(self, rule_id: str) -> AlertRule | None: ...
    def list_rules(self, filter: AlertFilter | None = None) -> list[AlertRule]: ...
    def update_rule(self, rule_id: str, changes: dict[str, Any]) -> AlertRule | None: ...
    def delete_rule(self, rule_id: str) -> bool: ...
    def record_event(self, event: AlertEvent) -> AlertEvent: ...
    def list_events(self, filter: EventFilter | None = None) -> list[AlertEvent]: ...
    def get_stats(self) -> AlertStats: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Null provider (disabled mode)
# ---------------------------------------------------------------------------

class NullAlertProvider:
    """No-op alert provider — stores nothing, evaluates nothing.

    Accepts (and ignores) the same constructor kwargs as the in-memory
    provider so manager construction is uniform across providers.
    """

    def __init__(self, **kwargs: Any) -> None:
        pass

    def create_rule(self, rule: AlertRule) -> AlertRule:
        return rule

    def get_rule(self, rule_id: str) -> AlertRule | None:
        return None

    def list_rules(self, filter: AlertFilter | None = None) -> list[AlertRule]:
        return []

    def update_rule(self, rule_id: str, changes: dict[str, Any]) -> AlertRule | None:
        return None

    def delete_rule(self, rule_id: str) -> bool:
        return False

    def record_event(self, event: AlertEvent) -> AlertEvent:
        return event

    def list_events(self, filter: EventFilter | None = None) -> list[AlertEvent]:
        return []

    def get_stats(self) -> AlertStats:
        return AlertStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_rule_filter(rules: list[AlertRule], flt: AlertFilter | None) -> list[AlertRule]:
    if flt is not None:
        if flt.enabled is not None:
            rules = [r for r in rules if r.enabled == flt.enabled]
        if flt.metric is not None:
            rules = [r for r in rules if r.metric == flt.metric]
        if flt.severity is not None:
            rules = [r for r in rules if r.severity == flt.severity]
        if flt.state is not None:
            rules = [r for r in rules if r.state == flt.state]
        rules = sorted(rules, key=lambda r: r.name)
        if flt.offset > 0:
            rules = rules[flt.offset:]
        if flt.limit is not None and flt.limit >= 0:
            rules = rules[: flt.limit]
    else:
        rules = sorted(rules, key=lambda r: r.name)
    return rules


def _apply_event_filter(events: list[AlertEvent], flt: EventFilter | None) -> list[AlertEvent]:
    # newest first
    events = sorted(events, key=lambda e: e.created_at, reverse=True)
    if flt is None:
        return events
    since = _parse_iso(flt.since) if flt.since else None
    until = _parse_iso(flt.until) if flt.until else None
    out: list[AlertEvent] = []
    for e in events:
        if flt.rule_id is not None and e.rule_id != flt.rule_id:
            continue
        if flt.state is not None and e.state != flt.state:
            continue
        if flt.severity is not None and e.severity != flt.severity:
            continue
        if flt.metric is not None and e.metric != flt.metric:
            continue
        ts = _parse_iso(e.created_at)
        if since is not None and ts < since:
            continue
        if until is not None and ts > until:
            continue
        out.append(e)
    if flt.offset > 0:
        out = out[flt.offset:]
    if flt.limit is not None and flt.limit >= 0:
        out = out[: flt.limit]
    return out


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryAlertProvider:
    """In-memory alert store — thread-safe with FIFO eviction.

    Args:
        max_rules: Max stored rules before the oldest-inserted are evicted.
        max_events: Max stored alert events (FIFO).
    """

    def __init__(
        self,
        max_rules: int = _MAX_RULES,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        import uuid

        self._uuid = uuid.uuid4
        self._rules: dict[str, AlertRule] = {}
        self._rule_order: list[str] = []
        self._events: dict[str, AlertEvent] = {}
        self._event_order: list[str] = []
        self._lock = threading.RLock()
        self._max_rules = max(1, int(max_rules))
        self._max_events = max(1, min(int(max_events), _MAX_EVENTS))

    # -- internal helpers ---------------------------------------------------

    def _evict_rules_locked(self) -> None:
        while len(self._rule_order) > self._max_rules:
            oldest = self._rule_order.pop(0)
            self._rules.pop(oldest, None)

    def _evict_events_locked(self) -> None:
        while len(self._event_order) > self._max_events:
            oldest = self._event_order.pop(0)
            self._events.pop(oldest, None)

    # -- AlertProvider --------------------------------------------------------

    def create_rule(self, rule: AlertRule) -> AlertRule:
        with self._lock:
            if not rule.rule_id:
                rule.rule_id = self._uuid().hex[:12]
            if rule.rule_id in self._rules:
                raise RegistryError(f"Alert rule already exists: {rule.rule_id}")
            self._rules[rule.rule_id] = rule
            self._rule_order.append(rule.rule_id)
            self._evict_rules_locked()
        return rule

    def get_rule(self, rule_id: str) -> AlertRule | None:
        with self._lock:
            return self._rules.get(rule_id)

    def list_rules(self, filter: AlertFilter | None = None) -> list[AlertRule]:
        with self._lock:
            rules = list(self._rules.values())
        return _apply_rule_filter(rules, filter)

    def update_rule(self, rule_id: str, changes: dict[str, Any]) -> AlertRule | None:
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                return None
            for key, value in changes.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)
            rule.updated_at = _now_iso()
            return rule

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            if rule_id not in self._rules:
                return False
            self._rules.pop(rule_id, None)
            if rule_id in self._rule_order:
                self._rule_order.remove(rule_id)
            return True

    def record_event(self, event: AlertEvent) -> AlertEvent:
        with self._lock:
            if not event.event_id:
                event.event_id = self._uuid().hex[:12]
            self._events[event.event_id] = event
            self._event_order.append(event.event_id)
            self._evict_events_locked()
        return event

    def list_events(self, filter: EventFilter | None = None) -> list[AlertEvent]:
        with self._lock:
            events = list(self._events.values())
        return _apply_event_filter(events, filter)

    def get_stats(self) -> AlertStats:
        with self._lock:
            rules = list(self._rules.values())
            events = list(self._events.values())
        stats = AlertStats(
            total_rules=len(rules),
            enabled_rules=sum(1 for r in rules if r.enabled),
            firing_rules=sum(1 for r in rules if r.state == "firing"),
            total_events=len(events),
        )
        for e in events:
            if e.state == "firing":
                stats.firing_events += 1
            else:
                stats.resolved_events += 1
            stats.by_severity[e.severity] = stats.by_severity.get(e.severity, 0) + 1
        return stats

    def close(self) -> None:
        with self._lock:
            self._rules.clear()
            self._rule_order.clear()
            self._events.clear()
            self._event_order.clear()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AlertRegistry:
    """Thread-safe registry for alert providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., AlertProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., AlertProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty alert provider name")
            if key in self._factories and not override:
                raise RegistryError(f"Alert provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> AlertProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown alert provider: {key}. Available: {available}"
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
alert_registry = AlertRegistry()

# Register defaults
alert_registry.register("null", NullAlertProvider)
alert_registry.register("memory", InMemoryAlertProvider)


def register_alert_provider(name: str, *, override: bool = False):
    """Decorator: register an alert provider class.

    Usage::

        @register_alert_provider("redis")
        class RedisAlertProvider:
            def create_rule(self, rule): ...
    """
    def decorator(factory: Callable[..., AlertProvider]):
        alert_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade + evaluation engine
# ---------------------------------------------------------------------------

MetricsReader = Callable[[str], float]
Notifier = Callable[..., Any]


class AlertManager:
    """High-level alert manager: rule CRUD + periodic evaluation.

    The evaluation engine reads metric values through an injected
    ``metrics_reader`` callback and delivers notifications through an
    injected ``notifier`` callback (typically ``NotificationManager.create``
    — called as ``notifier(user_id=..., title=..., message=...,
    severity=..., category="alert", metadata=...)``). This keeps the
    core transport-agnostic and free of API-layer imports.

    Evaluation semantics per rule:
    1. Read the metric value; reader errors count as "unknown" and skip
       the rule this tick (state unchanged).
    2. If the comparison is breached, increment ``breach_count``; when it
       reaches ``duration_ticks`` **and** the cooldown window has elapsed,
       fire an alert (record event + notify) and set ``state="firing"``.
    3. If not breached and the rule was firing, record a ``resolved``
       event, reset the counters, and set ``state="ok"``.

    Usage::

        manager = AlertManager(provider="memory", enabled=True)
        manager.set_metrics_reader(lambda m: 120.0)
        manager.create_rule("high-errors", metric="errors_total",
                            operator="gt", threshold=100)
        manager.tick()   # fires: 120 > 100
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        tick_seconds: int = 60,
        max_rules: int = _MAX_RULES,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        self._enabled = enabled
        self._tick_seconds = max(_MIN_TICK_SECONDS, min(int(tick_seconds), _MAX_TICK_SECONDS))
        if not enabled:
            self._provider: AlertProvider = NullAlertProvider()
        else:
            self._provider = alert_registry.create(
                provider, max_rules=max_rules, max_events=max_events
            )
        self._metrics_reader: MetricsReader | None = None
        self._notifier: Notifier | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> AlertProvider:
        return self._provider

    @property
    def tick_seconds(self) -> int:
        return self._tick_seconds

    # -- dependency injection ---------------------------------------------------

    def set_metrics_reader(self, reader: MetricsReader | None) -> None:
        """Inject the metric source: ``reader(metric_name) -> float``."""
        self._metrics_reader = reader

    def set_notifier(self, notifier: Notifier | None) -> None:
        """Inject the notification sink (e.g. ``NotificationManager.create``)."""
        self._notifier = notifier

    # -- validation ---------------------------------------------------------------

    @staticmethod
    def _validate_rule_fields(
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        severity: str,
        duration_ticks: int,
        cooldown_seconds: int,
        description: str,
    ) -> None:
        name = (name or "").strip()
        if not name:
            raise RegistryError("Rule name is required")
        if len(name) > _MAX_NAME_LENGTH:
            raise RegistryError(f"Rule name too long (max {_MAX_NAME_LENGTH})")
        if metric not in SUPPORTED_METRICS:
            raise RegistryError(
                f"Unsupported metric: {metric!r}. "
                f"Supported: {', '.join(sorted(SUPPORTED_METRICS))}"
            )
        if operator not in OPERATORS:
            raise RegistryError(
                f"Invalid operator: {operator!r}. Supported: {', '.join(sorted(OPERATORS))}"
            )
        if severity not in _SEVERITIES:
            raise RegistryError(
                f"Invalid severity: {severity!r}. "
                f"Supported: {', '.join(sorted(_SEVERITIES))}"
            )
        if not (1 <= duration_ticks <= _MAX_DURATION_TICKS):
            raise RegistryError(
                f"duration_ticks must be 1..{_MAX_DURATION_TICKS}, got {duration_ticks}"
            )
        if not (0 <= cooldown_seconds <= _MAX_COOLDOWN_SECONDS):
            raise RegistryError(
                f"cooldown_seconds must be 0..{_MAX_COOLDOWN_SECONDS}, got {cooldown_seconds}"
            )
        if len(description) > _MAX_MESSAGE_LENGTH:
            raise RegistryError(f"description too long (max {_MAX_MESSAGE_LENGTH})")

    # -- rule CRUD ------------------------------------------------------------------

    def create_rule(
        self,
        name: str,
        *,
        metric: str,
        operator: str = "gt",
        threshold: float = 0.0,
        severity: str = "warning",
        duration_ticks: int = 1,
        cooldown_seconds: int = 300,
        notify_user_id: str = "*",
        enabled: bool = True,
        description: str = "",
    ) -> AlertRule:
        """Create an alert rule.

        Raises:
            RegistryError: On duplicate name, unsupported metric/operator,
                or out-of-range parameters.
        """
        name = (name or "").strip()
        self._validate_rule_fields(
            name, metric, operator, threshold, severity,
            duration_ticks, cooldown_seconds, description,
        )
        # duplicate-name check (by stored name, not id)
        for existing in self._provider.list_rules():
            if existing.name == name:
                raise RegistryError(f"Alert rule name already used: {name}")
        rule = AlertRule(
            name=name,
            metric=metric,
            operator=operator,
            threshold=threshold,
            severity=severity,
            duration_ticks=duration_ticks,
            cooldown_seconds=cooldown_seconds,
            notify_user_id=notify_user_id or "*",
            enabled=enabled,
            description=description,
        )
        stored = self._provider.create_rule(rule)
        logger.info(
            "Alert rule created: %s (%s %s %s)",
            name, metric, operator, threshold,
            extra={"event": "alert.rule_created", "rule": name},
        )
        return stored

    def get_rule(self, rule_id: str) -> AlertRule | None:
        """Get a rule by id (None when missing)."""
        return self._provider.get_rule(rule_id)

    def list_rules(
        self,
        *,
        enabled: bool | None = None,
        metric: str | None = None,
        severity: str | None = None,
        state: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AlertRule]:
        """List rules (sorted by name) with optional filters."""
        flt = AlertFilter(
            enabled=enabled, metric=metric, severity=severity,
            state=state, limit=limit, offset=offset,
        )
        return self._provider.list_rules(flt)

    def update_rule(self, rule_id: str, changes: dict[str, Any]) -> AlertRule | None:
        """Update rule fields (threshold/enabled/severity/...).

        Resets breach counters when the threshold or operator changes.
        Returns None when the rule is missing.
        """
        rule = self._provider.get_rule(rule_id)
        if rule is None:
            return None
        allowed = {
            "operator", "threshold", "severity", "duration_ticks",
            "cooldown_seconds", "notify_user_id", "enabled", "description",
        }
        clean = {k: v for k, v in changes.items() if k in allowed}
        if "operator" in clean and clean["operator"] not in OPERATORS:
            raise RegistryError(f"Invalid operator: {clean['operator']!r}")
        if "severity" in clean and clean["severity"] not in _SEVERITIES:
            raise RegistryError(f"Invalid severity: {clean['severity']!r}")
        if "metric" in clean and clean["metric"] not in SUPPORTED_METRICS:
            raise RegistryError(f"Unsupported metric: {clean['metric']!r}")
        if "metric" in clean:
            clean.pop("metric")  # metric is immutable; silently ignore
        spec_changed = ("threshold" in clean) or ("operator" in clean)
        if spec_changed:
            clean["breach_count"] = 0
            clean["state"] = "ok"
        return self._provider.update_rule(rule_id, clean)

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule. Returns True when deleted."""
        return self._provider.delete_rule(rule_id)

    # -- events ------------------------------------------------------------------

    def list_events(
        self,
        *,
        rule_id: str | None = None,
        state: str | None = None,
        severity: str | None = None,
        metric: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AlertEvent]:
        """List alert events (newest first) with optional filters."""
        flt = EventFilter(
            rule_id=rule_id, state=state, severity=severity, metric=metric,
            since=since, until=until, limit=limit, offset=offset,
        )
        return self._provider.list_events(flt)

    def get_stats(self) -> AlertStats:
        """Aggregate statistics over rules and events."""
        return self._provider.get_stats()

    # -- evaluation engine -----------------------------------------------------------

    def _read_metric(self, metric: str) -> float | None:
        """Read a metric value; returns None when unavailable."""
        if self._metrics_reader is None:
            return None
        try:
            value = self._metrics_reader(metric)
            value = float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        except Exception:  # noqa: BLE001 — reader must never crash the loop
            logger.warning(
                "Alert metrics reader failed for %s",
                metric,
                extra={"event": "alert.reader_error", "metric": metric},
                exc_info=True,
            )
            return None
        if value != value:  # NaN guard
            return None
        return value

    def _notify(self, rule: AlertRule, event: AlertEvent) -> None:
        """Deliver a notification through the injected sink (best-effort)."""
        if self._notifier is None:
            return
        try:
            self._notifier(
                user_id=rule.notify_user_id,
                title=f"[{event.severity.upper()}] Alert: {rule.name}",
                message=event.message,
                severity=event.severity,
                category="alert",
                metadata={
                    "rule_id": rule.rule_id,
                    "event_id": event.event_id,
                    "metric": rule.metric,
                    "value": event.value,
                    "threshold": event.threshold,
                    "operator": rule.operator,
                    "state": event.state,
                },
            )
        except Exception:  # noqa: BLE001 — notifier must never crash the loop
            logger.warning(
                "Alert notifier failed for rule %s",
                rule.name,
                extra={"event": "alert.notifier_error", "rule": rule.name},
                exc_info=True,
            )

    def evaluate_rule(self, rule: AlertRule) -> AlertEvent | None:
        """Evaluate one rule for the current tick.

        Returns the fired/resolved ``AlertEvent`` (already recorded),
        or None when nothing happened this tick.
        """
        value = self._read_metric(rule.metric)
        if value is None:
            return None  # metric unavailable this tick — leave state as-is

        breached = _compare(value, rule.operator, rule.threshold)

        if breached:
            rule.breach_count += 1
            now_ts = datetime.now(UTC).timestamp()
            last_fired = (
                _parse_iso(rule.last_fired_at) if rule.last_fired_at else 0.0
            )
            cooldown_elapsed = (now_ts - last_fired) >= rule.cooldown_seconds
            if (
                rule.state != "firing"
                and rule.breach_count >= rule.duration_ticks
                and cooldown_elapsed
            ):
                rule.state = "firing"
                rule.last_fired_at = _now_iso()
                event = AlertEvent(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    metric=rule.metric,
                    value=value,
                    threshold=rule.threshold,
                    operator=rule.operator,
                    state="firing",
                    severity=rule.severity,
                    message=(
                        f"Alert '{rule.name}' FIRING: {rule.metric}="
                        f"{value:.2f} ({rule.operator} {rule.threshold})"
                    ),
                )
                self._provider.update_rule(rule.rule_id, {
                    "breach_count": rule.breach_count,
                    "state": rule.state,
                    "last_fired_at": rule.last_fired_at,
                })
                recorded = self._provider.record_event(event)
                self._notify(rule, recorded)
                logger.warning(
                    "Alert fired: %s (%s=%s %s %s)",
                    rule.name, rule.metric, value, rule.operator, rule.threshold,
                    extra={"event": "alert.fired", "rule": rule.name},
                )
                return recorded
            # still breaching but not firing yet (or in cooldown) — persist counter
            self._provider.update_rule(rule.rule_id, {
                "breach_count": rule.breach_count,
            })
            return None

        # not breached
        if rule.state == "firing":
            rule.state = "ok"
            rule.breach_count = 0
            event = AlertEvent(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                metric=rule.metric,
                value=value,
                threshold=rule.threshold,
                operator=rule.operator,
                state="resolved",
                severity=rule.severity,
                message=(
                    f"Alert '{rule.name}' RESOLVED: {rule.metric}="
                    f"{value:.2f} back within {rule.operator} {rule.threshold}"
                ),
            )
            self._provider.update_rule(rule.rule_id, {
                "breach_count": 0,
                "state": "ok",
            })
            recorded = self._provider.record_event(event)
            self._notify(rule, recorded)
            logger.info(
                "Alert resolved: %s",
                rule.name,
                extra={"event": "alert.resolved", "rule": rule.name},
            )
            return recorded

        rule.breach_count = 0
        self._provider.update_rule(rule.rule_id, {"breach_count": 0})
        return None

    def tick(self) -> list[AlertEvent]:
        """Run one evaluation pass over all enabled rules.

        A failing rule never aborts the pass. Returns the events produced
        this tick (may be empty).
        """
        if not self._enabled:
            return []
        events: list[AlertEvent] = []
        for rule in self._provider.list_rules(AlertFilter(enabled=True)):
            try:
                event = self.evaluate_rule(rule)
            except Exception:  # noqa: BLE001 — one bad rule must not stop others
                logger.warning(
                    "Alert rule evaluation failed: %s",
                    rule.name,
                    extra={"event": "alert.eval_error", "rule": rule.name},
                    exc_info=True,
                )
                continue
            if event is not None:
                events.append(event)
        return events

    # -- background loop ------------------------------------------------------------

    def start(self) -> None:
        """Start the background evaluation loop (no-op when disabled/running)."""
        if not self._enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentbase-alert-tick", daemon=True
        )
        self._thread.start()
        logger.info(
            "Alert evaluation loop started (tick=%ss)",
            self._tick_seconds,
            extra={"event": "alert.loop_started", "tick_seconds": self._tick_seconds},
        )

    def _loop(self) -> None:
        while not self._stop_event.wait(self._tick_seconds):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — the loop must never die
                logger.warning(
                    "Alert tick failed",
                    extra={"event": "alert.tick_error"},
                    exc_info=True,
                )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background loop (safe to call repeatedly)."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def close(self) -> None:
        """Stop the loop and release provider resources."""
        self.stop()
        self._provider.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_alert_manager: AlertManager | None = None
_alert_manager_lock = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Get the process-wide AlertManager (creates a disabled one by default)."""
    global _alert_manager
    if _alert_manager is None:
        with _alert_manager_lock:
            if _alert_manager is None:
                _alert_manager = AlertManager()
    return _alert_manager


def set_alert_manager(manager: AlertManager) -> None:
    """Replace the process-wide AlertManager."""
    global _alert_manager
    with _alert_manager_lock:
        _alert_manager = manager


def reset_alert_manager() -> None:
    """Reset the process-wide AlertManager (for testing)."""
    global _alert_manager
    with _alert_manager_lock:
        _alert_manager = None
