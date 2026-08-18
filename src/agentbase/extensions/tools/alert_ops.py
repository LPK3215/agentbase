"""Alert tools — expose alert rule/event reads to agents (read-only).

Tools provided:
- ``alert_list_rules``   — list alert rules with optional filters
- ``alert_list_events``  — list recent alert events (firing/resolved)

Deliberately read-only: creating or mutating alert rules is an admin API
operation; agents may consult the current alert state.

Requires the alert service (``alert.enabled=true``) and the
``alert_manager`` context key provided by the agent factory.
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
    mgr = (context or {}).get("alert_manager")
    if mgr is None:
        raise RuntimeError("alert_manager not available in context")
    return mgr


@register_tool("alert_list_rules", meta=ExtensionMeta(
    name="alert_list_rules",
    kind="tool",
    description="List alert rules (metric threshold monitors) with optional filters (read-only).",
    requires_context=["alert_manager"],
    default_enabled=False,
    tags=["alert", "monitoring"],
))
def build_alert_list_rules_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def alert_list_rules(
        state: str = "",
        metric: str = "",
        limit: int = 20,
    ) -> str:
        """List alert rules sorted by name.

        Args:
            state: Filter by state — "ok" or "firing" ("" = all).
            metric: Filter by metric name ("" = all).
            limit: Max rules to return (default 20).

        Returns:
            JSON list of rules (may be empty).
        """
        rules = mgr.list_rules(
            state=state or None,
            metric=metric or None,
            limit=limit,
        )
        return json.dumps([r.to_dict() for r in rules], ensure_ascii=False)

    return alert_list_rules


@register_tool("alert_list_events", meta=ExtensionMeta(
    name="alert_list_events",
    kind="tool",
    description="List recent alert events (firing/resolved), newest first (read-only).",
    requires_context=["alert_manager"],
    default_enabled=False,
    tags=["alert", "monitoring"],
))
def build_alert_list_events_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def alert_list_events(
        state: str = "",
        severity: str = "",
        limit: int = 20,
    ) -> str:
        """List alert events, newest first.

        Args:
            state: Filter by state — "firing" or "resolved" ("" = all).
            severity: Filter by severity — info/warning/error/critical ("" = all).
            limit: Max events to return (default 20).

        Returns:
            JSON list of events (may be empty).
        """
        events = mgr.list_events(
            state=state or None,
            severity=severity or None,
            limit=limit,
        )
        return json.dumps([e.to_dict() for e in events], ensure_ascii=False)

    return alert_list_events
