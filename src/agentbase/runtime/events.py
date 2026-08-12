"""Runtime events — typed event model for agent stream output.

Events are yielded by ``AgentRunner.stream()`` and consumed by the API
layer's SSE endpoint. Each event has a type, thread ID, agent name,
timestamp, and free-form data payload.

Event types:
    - ``run.started`` / ``run.finished`` / ``run.error`` — lifecycle
    - ``message.delta`` / ``message.final`` — token streaming
    - ``tool.start`` / ``tool.end`` — tool invocations
    - ``subagent.start`` / ``subagent.end`` — subagent lifecycle
    - ``interrupt`` — human-in-the-loop interrupt
    - ``update`` / ``raw`` — catch-all for unrecognised payloads
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field


class EventType(str, Enum):
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_ERROR = "run.error"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_FINAL = "message.final"
    TOOL_START = "tool.start"
    TOOL_END = "tool.end"
    SUBAGENT_START = "subagent.start"
    SUBAGENT_END = "subagent.end"
    INTERRUPT = "interrupt"
    UPDATE = "update"
    RAW = "raw"

    @classmethod
    def terminal_types(cls) -> frozenset[EventType]:
        """Return event types that signal the end of a stream."""
        return frozenset({cls.RUN_FINISHED, cls.RUN_ERROR, cls.INTERRUPT})

    @classmethod
    def message_types(cls) -> frozenset[EventType]:
        """Return event types that carry message content."""
        return frozenset({cls.MESSAGE_DELTA, cls.MESSAGE_FINAL})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeEvent(BaseModel):
    """A single event emitted during agent execution.

    Attributes:
        type: Event type from :class:`EventType`.
        thread_id: Conversation thread identifier.
        agent: Agent name that produced this event.
        data: Free-form payload (text, error, raw, etc.).
        timestamp: ISO 8601 UTC timestamp (auto-generated).
    """

    type: EventType
    thread_id: str | None = None
    agent: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    def to_sse(self) -> str:
        """Serialize to an SSE-compatible string.

        Format: ``event: <type>\\ndata: <json>\\n\\n``
        """
        payload = {
            "type": self.type.value,
            "thread_id": self.thread_id,
            "agent": self.agent,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        json_str = json.dumps(payload, ensure_ascii=False, default=str)
        return f"event: {self.type.value}\ndata: {json_str}\n\n"

    @property
    def is_terminal(self) -> bool:
        """True if this event signals the end of a stream."""
        return self.type in EventType.terminal_types()

    @property
    def is_message(self) -> bool:
        """True if this event carries message content."""
        return self.type in EventType.message_types()

    def matches(self, *types: EventType | str) -> bool:
        """Check if this event matches any of the given types.

        Accepts both :class:`EventType` enum values and plain strings.
        """
        type_values = {t.value if isinstance(t, EventType) else t for t in types}
        return self.type.value in type_values


def filter_events(
    events: list[RuntimeEvent],
    *,
    types: list[EventType | str] | None = None,
    agent: str | None = None,
    thread_id: str | None = None,
) -> list[RuntimeEvent]:
    """Filter a list of events by type, agent, or thread_id.

    Args:
        events: Events to filter.
        types: If provided, only return events matching these types.
        agent: If provided, only return events from this agent.
        thread_id: If provided, only return events for this thread.

    Returns:
        Filtered list of events.
    """
    result = events
    if types:
        type_values = {t.value if isinstance(t, EventType) else t for t in types}
        result = [e for e in result if e.type.value in type_values]
    if agent is not None:
        result = [e for e in result if e.agent == agent]
    if thread_id is not None:
        result = [e for e in result if e.thread_id == thread_id]
    return result


def create_event_filter(
    *,
    types: list[EventType | str] | None = None,
    agent: str | None = None,
    thread_id: str | None = None,
) -> Callable[[RuntimeEvent], bool]:
    """Create a callable filter function for events.

    Usage::

        should_emit = create_event_filter(types=[EventType.MESSAGE_DELTA])
        for event in stream:
            if should_emit(event):
                yield event
    """
    type_values: set[str] | None = None
    if types:
        type_values = {t.value if isinstance(t, EventType) else t for t in types}

    def _filter(event: RuntimeEvent) -> bool:
        if type_values is not None and event.type.value not in type_values:
            return False
        if agent is not None and event.agent != agent:
            return False
        if thread_id is not None and event.thread_id != thread_id:
            return False
        return True

    return _filter
