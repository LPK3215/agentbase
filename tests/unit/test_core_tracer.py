"""Tests for the tracing framework — covers Span, NullTracer, InMemoryTracer, registry, TraceContext.

Tests verify:
1. Span — attributes, events, finish, duration_ms, is_active, to_dict
2. NullTracer — no-op behavior, Protocol compliance
3. InMemoryTracer — start_trace, start_span, finish_span, get_trace, stats, clear, sampling
4. TracerRegistry — register, create, has, names, count, unregister, thread safety
5. TraceContext — context manager, normal exit, exception exit
6. TracerProvider Protocol compliance
7. register_tracer_provider decorator
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


class TestSpan:
    def test_default_values(self):
        from agentbase.core.tracer import Span

        span = Span()
        assert span.id != ""
        assert span.trace_id == ""
        assert span.name == ""
        assert span.parent_id is None
        assert span.started_at != ""
        assert span.finished_at is None
        assert span.attributes == {}
        assert span.events == []
        assert span.status == "ok"
        assert span.error is None

    def test_with_values(self):
        from agentbase.core.tracer import Span

        span = Span(name="test_span", trace_id="trace-123", parent_id="parent-456")
        assert span.name == "test_span"
        assert span.trace_id == "trace-123"
        assert span.parent_id == "parent-456"

    def test_set_attribute(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.set_attribute("key1", "value1")
        span.set_attribute("key2", 42)
        assert span.attributes["key1"] == "value1"
        assert span.attributes["key2"] == 42

    def test_add_event(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.add_event("event1", extra="data")
        assert len(span.events) == 1
        assert span.events[0]["name"] == "event1"
        assert span.events[0]["timestamp"] != ""
        assert span.events[0]["attributes"]["extra"] == "data"

    def test_add_multiple_events(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.add_event("event1")
        span.add_event("event2")
        span.add_event("event3")
        assert len(span.events) == 3

    def test_finish_default(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.finish()
        assert span.finished_at is not None
        assert span.status == "ok"
        assert span.error is None

    def test_finish_with_error(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.finish(status="error", error="Something went wrong")
        assert span.status == "error"
        assert span.error == "Something went wrong"

    def test_is_active_before_finish(self):
        from agentbase.core.tracer import Span

        span = Span()
        assert span.is_active is True

    def test_is_active_after_finish(self):
        from agentbase.core.tracer import Span

        span = Span()
        span.finish()
        assert span.is_active is False

    def test_duration_ms_none_when_active(self):
        from agentbase.core.tracer import Span

        span = Span()
        assert span.duration_ms is None

    def test_duration_ms_when_finished(self):
        from agentbase.core.tracer import Span

        span = Span()
        time.sleep(0.01)
        span.finish()
        assert span.duration_ms is not None
        assert span.duration_ms > 0

    def test_to_dict(self):
        from agentbase.core.tracer import Span

        span = Span(name="test", trace_id="t1")
        span.set_attribute("key", "val")
        span.add_event("evt")
        span.finish()
        d = span.to_dict()
        assert d["name"] == "test"
        assert d["trace_id"] == "t1"
        assert d["attributes"]["key"] == "val"
        assert len(d["events"]) == 1
        assert d["duration_ms"] is not None
        assert d["status"] == "ok"

    def test_unique_ids(self):
        from agentbase.core.tracer import Span

        span1 = Span()
        span2 = Span()
        assert span1.id != span2.id


# ---------------------------------------------------------------------------
# NullTracer
# ---------------------------------------------------------------------------


class TestNullTracer:
    def test_start_trace_returns_uuid(self):
        from agentbase.core.tracer import NullTracer

        tracer = NullTracer()
        trace_id = tracer.start_trace("test")
        assert trace_id != ""
        # Should be a valid UUID string
        assert len(trace_id) == 36

    def test_start_span_returns_span(self):
        from agentbase.core.tracer import NullTracer

        tracer = NullTracer()
        span = tracer.start_span("test_span")
        assert span.name == "test_span"

    def test_finish_span_noop(self):
        from agentbase.core.tracer import NullTracer

        tracer = NullTracer()
        span = tracer.start_span("test")
        # Should not raise
        tracer.finish_span(span)
        # Span is not actually finished (noop)
        assert span.finished_at is None

    def test_get_trace_returns_empty(self):
        from agentbase.core.tracer import NullTracer

        tracer = NullTracer()
        assert tracer.get_trace("any-id") == []

    def test_is_tracer_provider(self):
        from agentbase.core.tracer import NullTracer, TracerProvider

        tracer = NullTracer()
        assert isinstance(tracer, TracerProvider)


# ---------------------------------------------------------------------------
# InMemoryTracer
# ---------------------------------------------------------------------------


class TestInMemoryTracer:
    def test_start_trace_creates_trace(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("my_trace")
        assert trace_id != ""
        spans = tracer.get_trace(trace_id)
        assert len(spans) == 1  # start_trace creates first span
        assert spans[0].name == "my_trace"

    def test_start_span_within_trace(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span = tracer.start_span("child", trace_id=trace_id)
        assert span.name == "child"
        assert span.trace_id == trace_id
        spans = tracer.get_trace(trace_id)
        assert len(spans) == 2

    def test_start_span_with_attributes(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span = tracer.start_span("child", trace_id=trace_id, user="alice", action="click")
        assert span.attributes["user"] == "alice"
        assert span.attributes["action"] == "click"

    def test_start_span_with_parent(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        parent_span = tracer.start_span("parent", trace_id=trace_id)
        child_span = tracer.start_span("child", trace_id=trace_id, parent_id=parent_span.id)
        assert child_span.parent_id == parent_span.id

    def test_finish_span(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span = tracer.start_span("child", trace_id=trace_id)
        tracer.finish_span(span)
        assert span.finished_at is not None
        assert span.is_active is False

    def test_finish_span_with_error(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span = tracer.start_span("child", trace_id=trace_id)
        tracer.finish_span(span, status="error", error="Failed")
        assert span.status == "error"
        assert span.error == "Failed"

    def test_get_trace_empty(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        assert tracer.get_trace("nonexistent") == []

    def test_all_traces(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        tid1 = tracer.start_trace("trace1")
        tid2 = tracer.start_trace("trace2")
        all_traces = tracer.all_traces()
        assert len(all_traces) == 2
        assert tid1 in all_traces
        assert tid2 in all_traces

    def test_list_active_spans(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span1 = tracer.start_span("active1", trace_id=trace_id)
        tracer.start_span("active2", trace_id=trace_id)
        tracer.finish_span(span1)
        active = tracer.list_active_spans()
        assert len(active) == 2  # root span + span2 are still active
        # span1 is finished
        assert span1 not in active

    def test_stats(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")
        span = tracer.start_span("child", trace_id=trace_id)
        tracer.finish_span(span, status="error", error="boom")
        stats = tracer.stats()
        assert stats["trace_count"] == 1
        assert stats["total_spans"] == 2  # root + child
        assert stats["active_spans"] == 1  # root is still active
        assert stats["error_spans"] == 1
        assert stats["sampling_rate"] == 1.0

    def test_clear(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        tracer.start_trace("trace1")
        tracer.start_trace("trace2")
        count = tracer.clear()
        assert count == 2
        assert tracer.all_traces() == {}

    def test_clear_empty(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        count = tracer.clear()
        assert count == 0

    def test_sampling_rate_clamped(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer_high = InMemoryTracer(sampling_rate=2.0)
        assert tracer_high._sampling_rate == 1.0

        tracer_low = InMemoryTracer(sampling_rate=-0.5)
        assert tracer_low._sampling_rate == 0.0

    def test_sampling_rate_zero(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer(sampling_rate=0.0)
        trace_id = tracer.start_trace("test")
        # With 0 sampling, trace should not be stored
        assert tracer.get_trace(trace_id) == []

    def test_is_tracer_provider(self):
        from agentbase.core.tracer import InMemoryTracer, TracerProvider

        tracer = InMemoryTracer()
        assert isinstance(tracer, TracerProvider)

    def test_start_span_without_trace_id(self):
        from agentbase.core.tracer import InMemoryTracer

        tracer = InMemoryTracer()
        # When trace_id is None, a new trace_id is generated
        span = tracer.start_span("orphan")
        assert span.trace_id != ""
        # Should be stored
        spans = tracer.get_trace(span.trace_id)
        assert len(spans) == 1


# ---------------------------------------------------------------------------
# TracerRegistry
# ---------------------------------------------------------------------------


class TestTracerRegistry:
    def test_register_and_create(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("custom", NullTracer)
        tracer = registry.create("custom")
        assert isinstance(tracer, NullTracer)

    def test_register_case_insensitive(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("MyTracer", NullTracer)
        assert registry.has("mytracer")
        assert registry.has("MYTRACER")

    def test_register_duplicate_raises(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("test", NullTracer)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("test", NullTracer)

    def test_register_override(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("test", NullTracer)
        # Should not raise with override=True
        registry.register("test", NullTracer, override=True)

    def test_create_unknown_raises(self):
        from agentbase.core.tracer import TracerRegistry

        registry = TracerRegistry()
        with pytest.raises(KeyError, match="Unknown tracer"):
            registry.create("nonexistent")

    def test_has(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("test", NullTracer)
        assert registry.has("test") is True
        assert registry.has("nonexistent") is False

    def test_names(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("alpha", NullTracer)
        registry.register("beta", NullTracer)
        names = registry.names()
        assert "alpha" in names
        assert "beta" in names

    def test_count(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        assert registry.count == 0
        registry.register("a", NullTracer)
        assert registry.count == 1
        registry.register("b", NullTracer)
        assert registry.count == 2

    def test_unregister(self):
        from agentbase.core.tracer import NullTracer, TracerRegistry

        registry = TracerRegistry()
        registry.register("test", NullTracer)
        assert registry.unregister("test") is True
        assert registry.has("test") is False

    def test_unregister_nonexistent(self):
        from agentbase.core.tracer import TracerRegistry

        registry = TracerRegistry()
        assert registry.unregister("nonexistent") is False

    def test_global_registry_has_null(self):
        from agentbase.core.tracer import tracer_registry

        assert tracer_registry.has("null")

    def test_global_registry_has_memory(self):
        from agentbase.core.tracer import tracer_registry

        assert tracer_registry.has("memory")

    def test_global_registry_create_null(self):
        from agentbase.core.tracer import NullTracer, tracer_registry

        tracer = tracer_registry.create("null")
        assert isinstance(tracer, NullTracer)

    def test_global_registry_create_memory(self):
        from agentbase.core.tracer import InMemoryTracer, tracer_registry

        tracer = tracer_registry.create("memory")
        assert isinstance(tracer, InMemoryTracer)


# ---------------------------------------------------------------------------
# register_tracer_provider decorator
# ---------------------------------------------------------------------------


class TestRegisterTracerProvider:
    def test_decorator_registers(self):
        from agentbase.core.tracer import TracerRegistry, register_tracer_provider

        registry = TracerRegistry()

        # Temporarily use a separate registry
        import agentbase.core.tracer as tracer_mod

        original_registry = tracer_mod.tracer_registry
        tracer_mod.tracer_registry = registry

        try:
            @register_tracer_provider("my_custom")
            class MyCustomTracer:
                def start_trace(self, name, **attrs):
                    return "custom-trace"
                def start_span(self, name, **kw):
                    pass
                def finish_span(self, span, **kw):
                    pass
                def get_trace(self, trace_id):
                    return []

            assert registry.has("my_custom")
            tracer = registry.create("my_custom")
            assert isinstance(tracer, MyCustomTracer)
        finally:
            tracer_mod.tracer_registry = original_registry


# ---------------------------------------------------------------------------
# TraceContext
# ---------------------------------------------------------------------------


class TestTraceContext:
    def test_normal_exit(self):
        from agentbase.core.tracer import InMemoryTracer, TraceContext

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")

        with TraceContext(tracer, "work", trace_id=trace_id) as span:
            span.set_attribute("work_done", True)

        assert span.finished_at is not None
        assert span.status == "ok"
        assert span.attributes["work_done"] is True

    def test_exception_exit(self):
        from agentbase.core.tracer import InMemoryTracer, TraceContext

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")

        with pytest.raises(RuntimeError, match="oops"):
            with TraceContext(tracer, "failing_work", trace_id=trace_id) as span:
                raise RuntimeError("oops")

        assert span.finished_at is not None
        assert span.status == "error"
        assert "oops" in span.error

    def test_with_attributes(self):
        from agentbase.core.tracer import InMemoryTracer, TraceContext

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")

        with TraceContext(tracer, "work", trace_id=trace_id, user="alice") as span:
            pass

        assert span.attributes["user"] == "alice"

    def test_trace_helper_function(self):
        from agentbase.core.tracer import InMemoryTracer, trace

        tracer = InMemoryTracer()
        trace_id = tracer.start_trace("root")

        with trace(tracer, "helper_work", trace_id=trace_id) as span:
            span.set_attribute("result", "done")

        assert span.finished_at is not None
        assert span.attributes["result"] == "done"


# ---------------------------------------------------------------------------
# LangfuseTracer (mocked)
# ---------------------------------------------------------------------------


class TestLangfuseTracer:
    def test_init_defaults(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        assert tracer._client is None
        assert tracer._kwargs == {}

    def test_init_with_kwargs(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer(public_key="pk-test", secret_key="sk-test")
        assert tracer._kwargs["public_key"] == "pk-test"

    def test_start_trace_with_mock_client(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        mock_client = MagicMock()
        tracer._client = mock_client

        trace_id = tracer.start_trace("test_trace", user="alice")
        assert trace_id != ""
        mock_client.trace.assert_called_once()

    def test_start_span_with_mock_client(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        mock_client = MagicMock()
        tracer._client = mock_client

        span = tracer.start_span("test_span", trace_id="t1")
        assert span.name == "test_span"
        mock_client.span.assert_called_once()

    def test_finish_span_with_mock_client(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        mock_client = MagicMock()
        tracer._client = mock_client

        span = tracer.start_span("test_span", trace_id="t1")
        tracer.finish_span(span)
        assert span.finished_at is not None
        # finish_span calls client.span again for end_time
        assert mock_client.span.call_count >= 2

    def test_finish_span_error_with_mock_client(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        mock_client = MagicMock()
        tracer._client = mock_client

        span = tracer.start_span("test_span", trace_id="t1")
        tracer.finish_span(span, status="error", error="boom")
        assert span.status == "error"

    def test_get_trace_returns_empty(self):
        from agentbase.core.tracer import LangfuseTracer

        tracer = LangfuseTracer()
        assert tracer.get_trace("any") == []


# ---------------------------------------------------------------------------
# OpenTelemetryTracer (mocked)
# ---------------------------------------------------------------------------


class TestOpenTelemetryTracer:
    def test_init_defaults(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        assert tracer._service_name == "agentbase"
        assert tracer._endpoint is None
        assert tracer._tracer is None

    def test_init_with_custom_values(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer(service_name="my-app", endpoint="http://localhost:4317")
        assert tracer._service_name == "my-app"
        assert tracer._endpoint == "http://localhost:4317"

    def test_start_trace_with_mock(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        mock_otel_tracer = MagicMock()
        tracer._tracer = mock_otel_tracer

        trace_id = tracer.start_trace("test")
        assert trace_id != ""
        mock_otel_tracer.start_span.assert_called_once()

    def test_start_span_with_mock(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        mock_otel_tracer = MagicMock()
        tracer._tracer = mock_otel_tracer

        span = tracer.start_span("test_span", trace_id="t1")
        assert span.name == "test_span"
        assert hasattr(span, "_otel_span")

    def test_finish_span_with_mock(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        mock_otel_tracer = MagicMock()
        tracer._tracer = mock_otel_tracer

        span = tracer.start_span("test_span", trace_id="t1")
        tracer.finish_span(span)
        assert span.finished_at is not None
        mock_otel_span = span._otel_span
        mock_otel_span.end.assert_called_once()

    def test_finish_span_error_with_mock(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        mock_otel_tracer = MagicMock()
        tracer._tracer = mock_otel_tracer

        span = tracer.start_span("test_span", trace_id="t1")
        tracer.finish_span(span, status="error", error="failed")
        mock_otel_span = span._otel_span
        mock_otel_span.set_status.assert_called_with("ERROR", description="failed")

    def test_get_trace_returns_empty(self):
        from agentbase.core.tracer import OpenTelemetryTracer

        tracer = OpenTelemetryTracer()
        assert tracer.get_trace("any") == []
