"""Calendar management tools — expose CalendarManager CRUD to agents.

Tools provided:
- ``calendar_create_event`` — create an event (title + ISO start/end required)
- ``calendar_list_events``   — list events with optional filters
- ``calendar_upcoming``      — list the next upcoming events
- ``calendar_update_event``  — update event fields by ID
- ``calendar_delete_event``  — delete an event by ID

Requires the calendar service (``calendar.enabled=true``) and the
``calendar_manager`` context key provided by the agent factory.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _get_mgr(context: dict[str, Any] | None) -> Any:
    mgr = (context or {}).get("calendar_manager")
    if mgr is None:
        raise RuntimeError("calendar_manager not available in context")
    return mgr


@register_tool("calendar_create_event", meta=ExtensionMeta(
    name="calendar_create_event",
    kind="tool",
    description="Create a calendar event with title and ISO-8601 start/end times.",
    requires_context=["calendar_manager"],
    default_enabled=False,
    tags=["calendar", "scheduling"],
))
def build_calendar_create_event_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def calendar_create_event(
        title: str,
        start_time: str,
        end_time: str,
        location: str = "",
        description: str = "",
        attendees: str = "",
        tags: str = "",
        reminder_minutes: int | None = None,
    ) -> str:
        """Create a calendar event.

        Args:
            title: Event title (required).
            start_time: ISO-8601 start, e.g. "2026-09-01T09:00:00+00:00" (required).
            end_time: ISO-8601 end, must be after start_time (required).
            location: Optional location.
            description: Optional description.
            attendees: Comma-separated attendee list (optional).
            tags: Comma-separated tags (optional).
            reminder_minutes: Optional reminder offset in minutes before start.

        Returns:
            JSON event dict, or an error message on invalid input.
        """
        try:
            event = mgr.create_event(
                title=title,
                start_time=start_time,
                end_time=end_time,
                location=location,
                description=description,
                attendees=[a.strip() for a in attendees.split(",") if a.strip()],
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                reminder_minutes=reminder_minutes,
            )
            logger.info(
                "calendar_create_event: created %s",
                event.event_id,
                extra={"event": "calendar_tool.created", "event_id": event.event_id},
            )
            return json.dumps(event.to_dict(), ensure_ascii=False)
        except Exception as exc:
            logger.warning(
                "calendar_create_event failed: %s",
                exc,
                extra={"event": "calendar_tool.create_error", "error": str(exc)},
            )
            return f"Error creating event: {exc}"

    return calendar_create_event


@register_tool("calendar_list_events", meta=ExtensionMeta(
    name="calendar_list_events",
    kind="tool",
    description="List calendar events with optional status/tag/location/attendee/time filters.",
    requires_context=["calendar_manager"],
    default_enabled=False,
    tags=["calendar", "scheduling"],
))
def build_calendar_list_events_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def calendar_list_events(
        status: str = "",
        tag: str = "",
        location: str = "",
        attendee: str = "",
        since: str = "",
        until: str = "",
        limit: int = 20,
    ) -> str:
        """List calendar events sorted by start_time.

        Args:
            status: Filter by status: confirmed/tentative/cancelled ("" = all).
            tag: Filter by exact tag ("" = all).
            location: Substring match on location ("" = all).
            attendee: Filter by attendee ("" = all).
            since: ISO-8601 lower bound on end_time ("" = unbounded).
            until: ISO-8601 upper bound on start_time ("" = unbounded).
            limit: Max events to return (default 20).

        Returns:
            JSON list of events (may be empty).
        """
        events = mgr.list_events(
            status=status or None,
            tag=tag or None,
            location=location or None,
            attendee=attendee or None,
            since=since or None,
            until=until or None,
            limit=limit,
        )
        return json.dumps([e.to_dict() for e in events], ensure_ascii=False)

    return calendar_list_events


@register_tool("calendar_upcoming", meta=ExtensionMeta(
    name="calendar_upcoming",
    kind="tool",
    description="List the next upcoming calendar events (start_time >= now).",
    requires_context=["calendar_manager"],
    default_enabled=False,
    tags=["calendar", "scheduling"],
))
def build_calendar_upcoming_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def calendar_upcoming(limit: int = 5) -> str:
        """List the next upcoming events.

        Args:
            limit: Max events to return (default 5).

        Returns:
            JSON list of upcoming events sorted by start_time asc.
        """
        events = mgr.list_events(upcoming_only=True, limit=limit)
        return json.dumps([e.to_dict() for e in events], ensure_ascii=False)

    return calendar_upcoming


@register_tool("calendar_update_event", meta=ExtensionMeta(
    name="calendar_update_event",
    kind="tool",
    description="Update a calendar event's fields by ID.",
    requires_context=["calendar_manager"],
    default_enabled=False,
    tags=["calendar", "scheduling"],
))
def build_calendar_update_event_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def calendar_update_event(
        event_id: str,
        title: str = "",
        start_time: str = "",
        end_time: str = "",
        location: str = "",
        description: str = "",
        attendees: str = "",
        tags: str = "",
        status: str = "",
        reminder_minutes: int | None = None,
    ) -> str:
        """Update a calendar event. Only provided fields change.

        Args:
            event_id: ID of the event to update (required).
            title: New title ("" = keep).
            start_time / end_time: New ISO-8601 times ("" = keep).
            location / description: New values ("" = keep).
            attendees / tags: Comma-separated lists ("" = keep).
            status: New status: confirmed/tentative/cancelled ("" = keep).
            reminder_minutes: New reminder offset (None = keep).

        Returns:
            JSON updated event, or an error message.
        """
        changes: dict[str, Any] = {}
        if title:
            changes["title"] = title
        if start_time:
            changes["start_time"] = start_time
        if end_time:
            changes["end_time"] = end_time
        if location:
            changes["location"] = location
        if description:
            changes["description"] = description
        if attendees:
            changes["attendees"] = [a.strip() for a in attendees.split(",") if a.strip()]
        if tags:
            changes["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if status:
            changes["status"] = status
        if reminder_minutes is not None:
            changes["reminder_minutes"] = reminder_minutes

        try:
            event = mgr.update_event(event_id, changes)
        except Exception as exc:
            return f"Error updating event: {exc}"
        if event is None:
            return f"Event not found: {event_id}"
        return json.dumps(event.to_dict(), ensure_ascii=False)

    return calendar_update_event


@register_tool("calendar_delete_event", meta=ExtensionMeta(
    name="calendar_delete_event",
    kind="tool",
    description="Delete a calendar event by ID.",
    requires_context=["calendar_manager"],
    default_enabled=False,
    tags=["calendar", "scheduling"],
))
def build_calendar_delete_event_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def calendar_delete_event(event_id: str) -> str:
        """Delete a calendar event by ID.

        Args:
            event_id: ID of the event to delete (required).

        Returns:
            Confirmation or not-found message.
        """
        deleted = mgr.delete_event(event_id)
        if not deleted:
            return f"Event not found: {event_id}"
        return f"Deleted event: {event_id}"

    return calendar_delete_event
