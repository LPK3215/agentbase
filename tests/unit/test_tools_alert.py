"""Unit tests for alert_ops tools."""
from __future__ import annotations

import json

import pytest

from agentbase.core.alert import AlertManager
from agentbase.extensions.tools import alert_ops  # noqa: F401 — triggers registration
from agentbase.registry.tools import tool_registry


@pytest.fixture
def alert_mgr():
    mgr = AlertManager(provider="memory", enabled=True)
    mgr.create_rule("high-errors", metric="errors_total", threshold=100)
    mgr.create_rule("slow", metric="latency_avg_ms", threshold=500, enabled=False)
    return mgr


@pytest.fixture
def ctx(alert_mgr):
    return {"alert_manager": alert_mgr}


class TestRegistration:
    def test_both_tools_registered(self):
        assert tool_registry.has("alert_list_rules")
        assert tool_registry.has("alert_list_events")

    def test_missing_context_raises(self):
        from agentbase.extensions.tools.alert_ops import build_alert_list_rules_tool

        with pytest.raises(RuntimeError, match="alert_manager not available"):
            build_alert_list_rules_tool(context={})


class TestListRulesTool:
    def test_list_all(self, ctx):
        from agentbase.extensions.tools.alert_ops import build_alert_list_rules_tool

        tool_fn = build_alert_list_rules_tool(context=ctx)
        rules = json.loads(tool_fn.invoke({}))
        assert [r["name"] for r in rules] == ["high-errors", "slow"]

    def test_filter_by_metric(self, ctx):
        from agentbase.extensions.tools.alert_ops import build_alert_list_rules_tool

        tool_fn = build_alert_list_rules_tool(context=ctx)
        rules = json.loads(tool_fn.invoke({"metric": "latency_avg_ms"}))
        assert [r["name"] for r in rules] == ["slow"]

    def test_filter_by_state(self, ctx, alert_mgr):
        from agentbase.extensions.tools.alert_ops import build_alert_list_rules_tool

        # fire the rule to move it into "firing"
        alert_mgr.set_metrics_reader(lambda m: 150.0)
        alert_mgr.tick()
        tool_fn = build_alert_list_rules_tool(context=ctx)
        rules = json.loads(tool_fn.invoke({"state": "firing"}))
        assert [r["name"] for r in rules] == ["high-errors"]


class TestListEventsTool:
    def test_events_after_fire(self, ctx, alert_mgr):
        from agentbase.extensions.tools.alert_ops import build_alert_list_events_tool

        alert_mgr.set_metrics_reader(lambda m: 150.0)
        alert_mgr.tick()
        tool_fn = build_alert_list_events_tool(context=ctx)
        events = json.loads(tool_fn.invoke({}))
        assert len(events) == 1
        assert events[0]["state"] == "firing"

    def test_filter_by_state(self, ctx, alert_mgr):
        from agentbase.extensions.tools.alert_ops import build_alert_list_events_tool

        alert_mgr.set_metrics_reader(lambda m: 150.0)
        alert_mgr.tick()
        tool_fn = build_alert_list_events_tool(context=ctx)
        assert json.loads(tool_fn.invoke({"state": "resolved"})) == []

    def test_empty_when_no_events(self, ctx):
        from agentbase.extensions.tools.alert_ops import build_alert_list_events_tool

        tool_fn = build_alert_list_events_tool(context=ctx)
        assert json.loads(tool_fn.invoke({})) == []


class TestDisabledManager:
    def test_disabled_lists_empty(self):
        mgr = AlertManager()  # disabled
        from agentbase.extensions.tools.alert_ops import build_alert_list_rules_tool

        tool_fn = build_alert_list_rules_tool(context={"alert_manager": mgr})
        assert json.loads(tool_fn.invoke({})) == []
