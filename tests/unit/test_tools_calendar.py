"""Unit tests for calendar_ops tools."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentbase.core.calendar import CalendarManager


@pytest.fixture
def cal_mgr():
    return CalendarManager(provider="memory", enabled=True)


@pytest.fixture
def ctx(cal_mgr):
    return {"calendar_manager": cal_mgr}


def _future(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


class TestCreateEventTool:
    def test_create(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_create_event_tool

        tool_fn = build_calendar_create_event_tool(context=ctx)
        result = tool_fn.invoke({
            "title": "standup",
            "start_time": _future(24),
            "end_time": _future(25),
            "attendees": "a@x.com, b@x.com",
            "tags": "work, daily",
            "reminder_minutes": 10,
        })
        data = json.loads(result)
        assert data["title"] == "standup"
        assert data["attendees"] == ["a@x.com", "b@x.com"]
        assert data["tags"] == ["work", "daily"]
        assert cal_mgr.get_event(data["event_id"]) is not None

    def test_create_invalid_returns_error_string(self, ctx):
        from agentbase.extensions.tools.calendar_ops import build_calendar_create_event_tool

        tool_fn = build_calendar_create_event_tool(context=ctx)
        result = tool_fn.invoke({
            "title": "bad",
            "start_time": "not-a-date",
            "end_time": _future(1),
        })
        assert result.startswith("Error creating event:")


class TestListEventsTool:
    def test_list_all(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_list_events_tool

        cal_mgr.create_event(title="a", start_time=_future(24), end_time=_future(25), tags=["work"])
        cal_mgr.create_event(title="b", start_time=_future(48), end_time=_future(49), tags=["private"])
        tool_fn = build_calendar_list_events_tool(context=ctx)
        result = json.loads(tool_fn.invoke({}))
        assert len(result) == 2

    def test_list_tag_filter(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_list_events_tool

        cal_mgr.create_event(title="a", start_time=_future(24), end_time=_future(25), tags=["work"])
        cal_mgr.create_event(title="b", start_time=_future(48), end_time=_future(49), tags=["private"])
        tool_fn = build_calendar_list_events_tool(context=ctx)
        result = json.loads(tool_fn.invoke({"tag": "private"}))
        assert [e["title"] for e in result] == ["b"]


class TestUpcomingTool:
    def test_upcoming_excludes_past(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_upcoming_tool

        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        cal_mgr.create_event(title="gone", start_time=past, end_time=_future(-0.5))
        cal_mgr.create_event(title="soon", start_time=_future(1), end_time=_future(2))
        tool_fn = build_calendar_upcoming_tool(context=ctx)
        result = json.loads(tool_fn.invoke({"limit": 5}))
        assert [e["title"] for e in result] == ["soon"]


class TestUpdateEventTool:
    def test_update_partial(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_update_event_tool

        ev = cal_mgr.create_event(title="old", start_time=_future(24), end_time=_future(25))
        tool_fn = build_calendar_update_event_tool(context=ctx)
        result = json.loads(tool_fn.invoke({"event_id": ev.event_id, "title": "new"}))
        assert result["title"] == "new"
        assert result["start_time"] == ev.start_time  # unchanged

    def test_update_not_found(self, ctx):
        from agentbase.extensions.tools.calendar_ops import build_calendar_update_event_tool

        tool_fn = build_calendar_update_event_tool(context=ctx)
        assert "not found" in tool_fn.invoke({"event_id": "nope", "title": "x"}).lower()

    def test_update_invalid_returns_error(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_update_event_tool

        ev = cal_mgr.create_event(title="t", start_time=_future(24), end_time=_future(25))
        tool_fn = build_calendar_update_event_tool(context=ctx)
        result = tool_fn.invoke({"event_id": ev.event_id, "status": "bogus"})
        assert result.startswith("Error updating event:")


class TestDeleteEventTool:
    def test_delete(self, ctx, cal_mgr):
        from agentbase.extensions.tools.calendar_ops import build_calendar_delete_event_tool

        ev = cal_mgr.create_event(title="t", start_time=_future(24), end_time=_future(25))
        tool_fn = build_calendar_delete_event_tool(context=ctx)
        result = tool_fn.invoke({"event_id": ev.event_id})
        assert "Deleted" in result
        assert cal_mgr.get_event(ev.event_id) is None

    def test_delete_not_found(self, ctx):
        from agentbase.extensions.tools.calendar_ops import build_calendar_delete_event_tool

        tool_fn = build_calendar_delete_event_tool(context=ctx)
        assert "not found" in tool_fn.invoke({"event_id": "nope"}).lower()


class TestRegistration:
    def test_all_tools_registered(self):
        from agentbase.registry.tools import tool_registry

        for name in (
            "calendar_create_event",
            "calendar_list_events",
            "calendar_upcoming",
            "calendar_update_event",
            "calendar_delete_event",
        ):
            assert tool_registry.has(name), name

    def test_missing_context_raises(self):
        from agentbase.extensions.tools.calendar_ops import build_calendar_create_event_tool

        with pytest.raises(RuntimeError, match="calendar_manager not available"):
            build_calendar_create_event_tool(context=None)
