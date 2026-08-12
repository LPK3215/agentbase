"""Tracing and observability provider registry.

Provides a pluggable trace system so agent invocations, tool calls,
and model calls can be traced for debugging and performance analysis.

Default: ``NullTracer`` — no-op, zero overhead.
Register custom tracers (Langfuse, OpenTelemetry, etc.) with
``@register_tracer_provider``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable
from uuid import uuid4


@dataclass
class Span:
    """A trace span representing a unit of work.

    Attributes:
        id: Unique span identifier.
        trace_id: Parent trace identifier.
        name: Human-readable span name.
        parent_id: Parent span ID (for nested spans).
        started_at: ISO timestamp when the span started.
        finished_at: ISO timestamp when the span finished (None if still active).
        attributes: Key-value metadata attached to the span.
        events: List of timestamped events within the span.
        status: ``"ok"`` or ``"error"``.
        error: Error message if status is ``"error"``.
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = ""
    name: str = ""
    parent_id: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, event_name: str, **attrs: Any) -> None:
        self.events.append({
            "name": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": attrs,
        })

    def finish(self, *, status: str = "ok", error: str | None = None) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.error = error

    @property
    def duration_ms(self) -> float | None:
        """Return the span duration in milliseconds, or None if not finished."""
        if self.finished_at is None:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.finished_at)
            return (end - start).total_seconds() * 1000
        except Exception:
            return None

    @property
    def is_active(self) -> bool:
        """True if the span has not been finished yet."""
        return self.finished_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
            "error": self.error,
        }


@runtime_checkable
class TracerProvider(Protocol):
    """Protocol for tracing providers."""

    def start_trace(self, name: str, **attributes: Any) -> str:
        """Start a new trace. Returns trace_id."""
        ...

    def start_span(self, name: str, *, trace_id: str | None = None, parent_id: str | None = None, **attributes: Any) -> Span:
        """Start a new span within a trace."""
        ...

    def finish_span(self, span: Span, *, status: str = "ok", error: str | None = None) -> None:
        """Finish a span."""
        ...

    def get_trace(self, trace_id: str) -> list[Span]:
        """Get all spans for a trace."""
        ...


class NullTracer:
    """No-op tracer — zero overhead, drops all spans."""

    def start_trace(self, name: str, **attributes: Any) -> str:
        return str(uuid4())

    def start_span(self, name: str, *, trace_id: str | None = None, parent_id: str | None = None, **attributes: Any) -> Span:
        return Span(name=name, trace_id=trace_id or "", parent_id=parent_id)

    def finish_span(self, span: Span, *, status: str = "ok", error: str | None = None) -> None:
        pass

    def get_trace(self, trace_id: str) -> list[Span]:
        return []


class InMemoryTracer:
    """In-memory tracer — stores all spans for inspection. Useful for testing.

    Features:
    - Sampling rate — control what fraction of traces are recorded
    - Active span tracking — list spans that haven't been finished
    - Duration tracking — each span reports ``duration_ms``
    - Clear method — reset all stored traces
    """

    def __init__(self, *, sampling_rate: float = 1.0) -> None:
        self._spans: dict[str, list[Span]] = {}
        self._sampling_rate = min(max(sampling_rate, 0.0), 1.0)

    def start_trace(self, name: str, **attributes: Any) -> str:
        trace_id = str(uuid4())
        # Apply sampling — if sampled, don't store
        import random
        if self._sampling_rate < 1.0 and random.random() > self._sampling_rate:
            # Sampled out — return a trace_id but don't store
            return trace_id
        self._spans[trace_id] = []
        self.start_span(name, trace_id=trace_id, **attributes)
        return trace_id

    def start_span(self, name: str, *, trace_id: str | None = None, parent_id: str | None = None, **attributes: Any) -> Span:
        tid = trace_id or str(uuid4())
        span = Span(name=name, trace_id=tid, parent_id=parent_id)
        for k, v in attributes.items():
            span.set_attribute(k, v)
        # Only store if this trace is being tracked
        if tid in self._spans or trace_id is None:
            self._spans.setdefault(tid, []).append(span)
        return span

    def finish_span(self, span: Span, *, status: str = "ok", error: str | None = None) -> None:
        span.finish(status=status, error=error)

    def get_trace(self, trace_id: str) -> list[Span]:
        return list(self._spans.get(trace_id, []))

    def all_traces(self) -> dict[str, list[Span]]:
        return dict(self._spans)

    def list_active_spans(self) -> list[Span]:
        """Return all spans that haven't been finished yet."""
        return [
            span for spans in self._spans.values()
            for span in spans
            if span.is_active
        ]

    def stats(self) -> dict[str, Any]:
        """Return tracer statistics."""
        total_spans = sum(len(spans) for spans in self._spans.values())
        active_spans = sum(1 for s in self._list_all_spans() if s.is_active)
        error_spans = sum(1 for s in self._list_all_spans() if s.status == "error")
        return {
            "trace_count": len(self._spans),
            "total_spans": total_spans,
            "active_spans": active_spans,
            "error_spans": error_spans,
            "sampling_rate": self._sampling_rate,
        }

    def _list_all_spans(self) -> list[Span]:
        return [s for spans in self._spans.values() for s in spans]

    def clear(self) -> int:
        """Clear all stored traces. Returns the number of cleared traces."""
        count = len(self._spans)
        self._spans.clear()
        return count


class TracerRegistry:
    """Thread-safe registry for tracer providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., TracerProvider]] = {}
        self._lock = threading.RLock()

    def register(self, name: str, factory: Callable[..., TracerProvider], *, override: bool = False) -> None:
        key = name.lower()
        with self._lock:
            if key in self._factories and not override:
                raise ValueError(f"Tracer provider '{name}' is already registered")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> TracerProvider:
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories.keys())) or "<empty>"
                raise KeyError(f"Unknown tracer provider: {name}. Available: {available}")
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        """Remove a factory. Returns True if removed."""
        key = name.lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global registry
tracer_registry = TracerRegistry()
tracer_registry.register("null", NullTracer, )
tracer_registry.register("memory", InMemoryTracer)


def register_tracer_provider(name: str, *, override: bool = False):
    """Decorator to register a tracer provider."""

    def decorator(factory: Callable[..., TracerProvider]):
        tracer_registry.register(name, factory, override=override)
        return factory

    return decorator


class TraceContext:
    """Context manager for tracing a block of work."""

    def __init__(self, tracer: TracerProvider, name: str, *, trace_id: str | None = None, parent_id: str | None = None, **attributes: Any) -> None:
        self._tracer = tracer
        self._name = name
        self._trace_id = trace_id
        self._parent_id = parent_id
        self._attributes = attributes
        self._span: Span | None = None

    def __enter__(self) -> Span:
        self._span = self._tracer.start_span(
            self._name,
            trace_id=self._trace_id,
            parent_id=self._parent_id,
            **self._attributes,
        )
        return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._span:
            if exc_type:
                self._tracer.finish_span(self._span, status="error", error=str(exc_val))
            else:
                self._tracer.finish_span(self._span)


def trace(tracer: TracerProvider, name: str, **attributes: Any) -> TraceContext:
    """Create a trace context manager for a span."""
    return TraceContext(tracer, name, **attributes)


# ---------------------------------------------------------------------------
# Langfuse tracing provider
# ---------------------------------------------------------------------------

class LangfuseTracer:
    """Tracing provider that sends spans to Langfuse.

    Requires ``langfuse`` package and ``LANGFUSE_PUBLIC_KEY``,
    ``LANGFUSE_SECRET_KEY`` environment variables.

    Usage::

        # In config:
        tracer:
          provider: langfuse

        # Or programmatically:
        from agentbase.core.tracer import LangfuseTracer
        tracer = LangfuseTracer()
    """

    def __init__(self, **kwargs: Any) -> None:
        self._client = None
        self._kwargs = kwargs

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from langfuse import Langfuse
            except ImportError as exc:
                raise ImportError(
                    "Langfuse tracing requires the langfuse package. "
                    "Install with: pip install langfuse"
                ) from exc
            self._client = Langfuse(**self._kwargs)
        return self._client

    def start_trace(self, name: str, **attributes: Any) -> str:
        trace_id = str(uuid4())
        client = self._get_client()
        client.trace(
            id=trace_id,
            name=name,
            metadata=attributes,
        )
        return trace_id

    def start_span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_id: str | None = None,
        **attributes: Any,
    ) -> Span:
        tid = trace_id or str(uuid4())
        span = Span(name=name, trace_id=tid, parent_id=parent_id)
        for k, v in attributes.items():
            span.set_attribute(k, v)

        client = self._get_client()
        client.span(
            trace_id=tid,
            parent_observation_id=parent_id,
            name=name,
            metadata=attributes,
        )
        return span

    def finish_span(self, span: Span, *, status: str = "ok", error: str | None = None) -> None:
        span.finish(status=status, error=error)
        client = self._get_client()
        client.span(
            trace_id=span.trace_id,
            id=span.id,
            name=span.name,
            end_time=span.finished_at,
            level="ERROR" if status == "error" else "DEBUG",
            status_message=error,
        )

    def get_trace(self, trace_id: str) -> list[Span]:
        return []


# Register Langfuse if available
try:
    import langfuse  # noqa: F401
    tracer_registry.register("langfuse", LangfuseTracer, override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# OpenTelemetry tracing provider
# ---------------------------------------------------------------------------

class OpenTelemetryTracer:
    """Tracing provider using OpenTelemetry (OTel).

    Exports traces to any OTel-compatible backend (Jaeger, Zipkin,
    Tempo, Datadog, etc.) via OTLP.

    Requires ``opentelemetry-api`` and ``opentelemetry-sdk`` packages.

    Usage::

        # In config:
        tracer:
          provider: opentelemetry
          options:
            service_name: agentbase
            endpoint: http://localhost:4317

        # Or programmatically:
        from agentbase.core.tracer import OpenTelemetryTracer
        tracer = OpenTelemetryTracer(service_name="agentbase")
    """

    def __init__(
        self,
        *,
        service_name: str = "agentbase",
        endpoint: str | None = None,
    ) -> None:
        self._service_name = service_name
        self._endpoint = endpoint
        self._tracer = None

    def _get_tracer(self) -> Any:
        if self._tracer is None:
            try:
                from opentelemetry import trace
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import (
                    BatchSpanProcessor,
                )
            except ImportError as exc:
                raise ImportError(
                    "OpenTelemetry tracing requires opentelemetry packages. "
                    "Install with: pip install opentelemetry-api opentelemetry-sdk "
                    "opentelemetry-exporter-otlp"
                ) from exc

            provider = TracerProvider()
            if self._endpoint:
                try:
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                        OTLPSpanExporter,
                    )
                    exporter = OTLPSpanExporter(endpoint=self._endpoint)
                    provider.add_span_processor(
                        BatchSpanProcessor(exporter)
                    )
                except ImportError:
                    pass
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self._service_name)
        return self._tracer

    def start_trace(self, name: str, **attributes: Any) -> str:
        trace_id = str(uuid4())
        tracer = self._get_tracer()
        span = tracer.start_span(name, attributes={"trace_id": trace_id, **attributes})
        span.end()
        return trace_id

    def start_span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_id: str | None = None,
        **attributes: Any,
    ) -> Span:
        tid = trace_id or str(uuid4())
        span = Span(name=name, trace_id=tid, parent_id=parent_id)
        for k, v in attributes.items():
            span.set_attribute(k, v)

        tracer = self._get_tracer()
        otel_span = tracer.start_span(name, attributes={"trace_id": tid, **attributes})
        span._otel_span = otel_span  # type: ignore[attr-defined]
        return span

    def finish_span(self, span: Span, *, status: str = "ok", error: str | None = None) -> None:
        span.finish(status=status, error=error)
        otel_span = getattr(span, "_otel_span", None)
        if otel_span:
            if status == "error":
                otel_span.set_status("ERROR", description=error or "")
            else:
                otel_span.set_status("OK")
            otel_span.end()

    def get_trace(self, trace_id: str) -> list[Span]:
        return []


# Register OpenTelemetry if available
try:
    import opentelemetry  # noqa: F401
    tracer_registry.register("opentelemetry", OpenTelemetryTracer, override=True)
except ImportError:
    pass
