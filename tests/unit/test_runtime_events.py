"""Tests for runtime events — covers EventType, RuntimeEvent, filter_events, create_event_filter.

Tests verify:
1. EventType enum values and helper class methods
2. RuntimeEvent — creation, serialization (to_dict, to_sse), properties (is_terminal, is_message)
3. RuntimeEvent.matches — with enum values and strings
4. filter_events — by type, agent, thread_id, combinations
5. create_event_filter — callable filter, type/agent/thread_id filters, combinations
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# EventType
# ---------------------------------------------------------------------------


class TestEventType:
    def test_enum_values(self):
        from agentbase.runtime.events import EventType

        assert EventType.RUN_STARTED.value == "run.started"
        assert EventType.RUN_FINISHED.value == "run.finished"
        assert EventType.RUN_ERROR.value == "run.error"
        assert EventType.MESSAGE_DELTA.value == "message.delta"
        assert EventType.MESSAGE_FINAL.value == "message.final"
        assert EventType.TOOL_START.value == "tool.start"
        assert EventType.TOOL_END.value == "tool.end"
        assert EventType.SUBAGENT_START.value == "subagent.start"
        assert EventType.SUBAGENT_END.value == "subagent.end"
        assert EventType.INTERRUPT.value == "interrupt"
        assert EventType.UPDATE.value == "update"
        assert EventType.RAW.value == "raw"

    def test_terminal_types(self):
        from agentbase.runtime.events import EventType

        terminals = EventType.terminal_types()
        assert EventType.RUN_FINISHED in terminals
        assert EventType.RUN_ERROR in terminals
        assert EventType.INTERRUPT in terminals
        assert EventType.MESSAGE_DELTA not in terminals
        assert EventType.TOOL_START not in terminals

    def test_message_types(self):
        from agentbase.runtime.events import EventType

        messages = EventType.message_types()
        assert EventType.MESSAGE_DELTA in messages
        assert EventType.MESSAGE_FINAL in messages
        assert EventType.RUN_STARTED not in messages
        assert EventType.TOOL_START not in messages

    def test_is_str_enum(self):
        from agentbase.runtime.events import EventType

        assert isinstance(EventType.RUN_STARTED, str)
        assert EventType.RUN_STARTED == "run.started"


# ---------------------------------------------------------------------------
# RuntimeEvent — creation and basic properties
# ---------------------------------------------------------------------------


class TestRuntimeEventCreation:
    def test_default_values(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert event.type == EventType.RUN_STARTED
        assert event.thread_id is None
        assert event.agent is None
        assert event.data == {}
        assert event.timestamp != ""

    def test_with_values(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            thread_id="t1",
            agent="my_agent",
            data={"text": "hello"},
        )
        assert event.type == EventType.MESSAGE_DELTA
        assert event.thread_id == "t1"
        assert event.agent == "my_agent"
        assert event.data["text"] == "hello"

    def test_timestamp_auto_generated(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        # Should be an ISO 8601 string
        assert "T" in event.timestamp
        assert event.timestamp.endswith(("Z", "+00:00"))

    def test_custom_timestamp(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED, timestamp="2024-01-01T00:00:00Z")
        assert event.timestamp == "2024-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# RuntimeEvent — serialization
# ---------------------------------------------------------------------------


class TestRuntimeEventSerialization:
    def test_to_dict(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            thread_id="t1",
            agent="agent1",
            data={"text": "hello"},
        )
        d = event.to_dict()
        assert d["type"] == EventType.MESSAGE_DELTA
        assert d["thread_id"] == "t1"
        assert d["agent"] == "agent1"
        assert d["data"]["text"] == "hello"

    def test_to_sse_format(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            thread_id="t1",
            agent="agent1",
            data={"text": "hello"},
        )
        sse = event.to_sse()
        assert sse.startswith("event: message.delta\n")
        assert "data: " in sse
        assert sse.endswith("\n\n")

    def test_to_sse_json_payload(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(
            type=EventType.MESSAGE_FINAL,
            thread_id="t1",
            agent="agent1",
            data={"text": "final answer"},
        )
        sse = event.to_sse()
        # Extract JSON from SSE
        json_line = [line for line in sse.strip().split("\n") if line.startswith("data: ")][0]
        payload = json.loads(json_line[6:])
        assert payload["type"] == "message.final"
        assert payload["thread_id"] == "t1"
        assert payload["data"]["text"] == "final answer"

    def test_to_sse_with_unicode(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(
            type=EventType.MESSAGE_DELTA,
            data={"text": "你好世界"},
        )
        sse = event.to_sse()
        assert "你好世界" in sse


# ---------------------------------------------------------------------------
# RuntimeEvent — properties
# ---------------------------------------------------------------------------


class TestRuntimeEventProperties:
    def test_is_terminal_run_finished(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_FINISHED)
        assert event.is_terminal is True

    def test_is_terminal_run_error(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_ERROR)
        assert event.is_terminal is True

    def test_is_terminal_interrupt(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.INTERRUPT)
        assert event.is_terminal is True

    def test_is_terminal_non_terminal(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.MESSAGE_DELTA)
        assert event.is_terminal is False

    def test_is_message_delta(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.MESSAGE_DELTA)
        assert event.is_message is True

    def test_is_message_final(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.MESSAGE_FINAL)
        assert event.is_message is True

    def test_is_message_non_message(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert event.is_message is False


# ---------------------------------------------------------------------------
# RuntimeEvent.matches
# ---------------------------------------------------------------------------


class TestRuntimeEventMatches:
    def test_matches_single_enum(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert event.matches(EventType.RUN_STARTED) is True

    def test_matches_single_string(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert event.matches("run.started") is True

    def test_matches_multiple(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.MESSAGE_DELTA)
        assert event.matches(EventType.RUN_STARTED, EventType.MESSAGE_DELTA) is True

    def test_no_match(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert event.matches(EventType.MESSAGE_DELTA) is False

    def test_matches_mixed_string_and_enum(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.TOOL_START)
        assert event.matches("message.delta", EventType.TOOL_START) is True

    def test_matches_empty(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        event = RuntimeEvent(type=EventType.RUN_STARTED)
        # No types to match against — should return False
        assert event.matches() is False


# ---------------------------------------------------------------------------
# filter_events
# ---------------------------------------------------------------------------


class TestFilterEvents:
    def _make_events(self):
        from agentbase.runtime.events import EventType, RuntimeEvent

        return [
            RuntimeEvent(type=EventType.RUN_STARTED, thread_id="t1", agent="a1"),
            RuntimeEvent(type=EventType.MESSAGE_DELTA, thread_id="t1", agent="a1", data={"text": "hello"}),
            RuntimeEvent(type=EventType.MESSAGE_FINAL, thread_id="t1", agent="a1", data={"text": "hello"}),
            RuntimeEvent(type=EventType.RUN_FINISHED, thread_id="t1", agent="a1"),
            RuntimeEvent(type=EventType.RUN_STARTED, thread_id="t2", agent="a2"),
            RuntimeEvent(type=EventType.MESSAGE_DELTA, thread_id="t2", agent="a2", data={"text": "world"}),
            RuntimeEvent(type=EventType.RUN_ERROR, thread_id="t2", agent="a2", data={"error": "boom"}),
        ]

    def test_no_filter_returns_all(self):
        from agentbase.runtime.events import filter_events

        events = self._make_events()
        result = filter_events(events)
        assert len(result) == 7

    def test_filter_by_type_enum(self):
        from agentbase.runtime.events import EventType, filter_events

        events = self._make_events()
        result = filter_events(events, types=[EventType.MESSAGE_DELTA])
        assert len(result) == 2
        assert all(e.type == EventType.MESSAGE_DELTA for e in result)

    def test_filter_by_type_string(self):
        from agentbase.runtime.events import filter_events

        events = self._make_events()
        result = filter_events(events, types=["message.delta"])
        assert len(result) == 2

    def test_filter_by_multiple_types(self):
        from agentbase.runtime.events import EventType, filter_events

        events = self._make_events()
        result = filter_events(events, types=[EventType.MESSAGE_DELTA, EventType.MESSAGE_FINAL])
        assert len(result) == 3

    def test_filter_by_agent(self):
        from agentbase.runtime.events import filter_events

        events = self._make_events()
        result = filter_events(events, agent="a1")
        assert len(result) == 4
        assert all(e.agent == "a1" for e in result)

    def test_filter_by_thread_id(self):
        from agentbase.runtime.events import filter_events

        events = self._make_events()
        result = filter_events(events, thread_id="t2")
        assert len(result) == 3
        assert all(e.thread_id == "t2" for e in result)

    def test_filter_combined(self):
        from agentbase.runtime.events import EventType, filter_events

        events = self._make_events()
        result = filter_events(events, types=[EventType.MESSAGE_DELTA], agent="a1")
        assert len(result) == 1
        assert result[0].agent == "a1"
        assert result[0].type == EventType.MESSAGE_DELTA

    def test_filter_no_match(self):
        from agentbase.runtime.events import EventType, filter_events

        events = self._make_events()
        result = filter_events(events, types=[EventType.TOOL_START])
        assert len(result) == 0

    def test_filter_empty_events(self):
        from agentbase.runtime.events import EventType, filter_events

        result = filter_events([], types=[EventType.RUN_STARTED])
        assert result == []


# ---------------------------------------------------------------------------
# create_event_filter
# ---------------------------------------------------------------------------


class TestCreateEventFilter:
    def test_no_filter(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter()
        event = RuntimeEvent(type=EventType.RUN_STARTED)
        assert should_emit(event) is True

    def test_filter_by_type(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(types=[EventType.MESSAGE_DELTA])
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_DELTA)) is True
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED)) is False

    def test_filter_by_type_string(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(types=["message.delta"])
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_DELTA)) is True

    def test_filter_by_agent(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(agent="a1")
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED, agent="a1")) is True
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED, agent="a2")) is False

    def test_filter_by_thread_id(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(thread_id="t1")
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED, thread_id="t1")) is True
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED, thread_id="t2")) is False

    def test_filter_combined(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(types=[EventType.MESSAGE_DELTA], agent="a1", thread_id="t1")
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_DELTA, agent="a1", thread_id="t1")) is True
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_DELTA, agent="a2", thread_id="t1")) is False
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED, agent="a1", thread_id="t1")) is False

    def test_filter_multiple_types(self):
        from agentbase.runtime.events import EventType, RuntimeEvent, create_event_filter

        should_emit = create_event_filter(types=[EventType.MESSAGE_DELTA, EventType.MESSAGE_FINAL])
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_DELTA)) is True
        assert should_emit(RuntimeEvent(type=EventType.MESSAGE_FINAL)) is True
        assert should_emit(RuntimeEvent(type=EventType.RUN_STARTED)) is False

    def test_filter_returns_callable(self):
        from agentbase.runtime.events import create_event_filter

        should_emit = create_event_filter()
        assert callable(should_emit)
