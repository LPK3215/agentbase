"""System config tools — expose runtime config reads to agents (read-only).

Tools provided:
- ``system_config_get``    — get a config value by key
- ``system_config_list``   — list config entries with optional filters

Deliberately read-only: agents may consult runtime parameters (feature
flags, tunables) but cannot mutate platform configuration.

Requires the system config service (``system_config.enabled=true``) and
the ``system_config_manager`` context key provided by the agent factory.
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
    mgr = (context or {}).get("system_config_manager")
    if mgr is None:
        raise RuntimeError("system_config_manager not available in context")
    return mgr


@register_tool("system_config_get", meta=ExtensionMeta(
    name="system_config_get",
    kind="tool",
    description="Get a runtime system config value by key (read-only).",
    requires_context=["system_config_manager"],
    default_enabled=False,
    tags=["config", "system"],
))
def build_system_config_get_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def system_config_get(key: str) -> str:
        """Get a runtime config value.

        Args:
            key: Config key, e.g. "feature.daily_report" (required).

        Returns:
            JSON entry dict (key/value/category/description/version),
            or a not-found message.
        """
        item = mgr.get_item(key)
        if item is None:
            return f"Config key not found: {key}"
        return json.dumps(item.to_dict(), ensure_ascii=False)

    return system_config_get


@register_tool("system_config_list", meta=ExtensionMeta(
    name="system_config_list",
    kind="tool",
    description="List runtime system config entries with optional category/prefix filters (read-only).",
    requires_context=["system_config_manager"],
    default_enabled=False,
    tags=["config", "system"],
))
def build_system_config_list_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def system_config_list(
        category: str = "",
        key_prefix: str = "",
        limit: int = 20,
    ) -> str:
        """List runtime config entries sorted by key.

        Args:
            category: Filter by exact category ("" = all).
            key_prefix: Filter by key prefix ("" = all).
            limit: Max entries to return (default 20).

        Returns:
            JSON list of entries (may be empty).
        """
        items = mgr.list_items(
            category=category or None,
            key_prefix=key_prefix or None,
            limit=limit,
        )
        return json.dumps([it.to_dict() for it in items], ensure_ascii=False)

    return system_config_list
