from __future__ import annotations

from typing import Any

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.subagents import register_subagent

_GENERAL_HELPER_META = ExtensionMeta(
    name="general_helper",
    kind="subagent",
    description="Handle bounded helper tasks and return a concise summary.",
    requires_context=[],
    default_enabled=True,
)


@register_subagent("general_helper", meta=_GENERAL_HELPER_META)
def build_general_helper(context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": "general_helper",
        "description": "Handles bounded helper tasks and returns a concise summary.",
        "system_prompt": (
            "You are a focused helper subagent. Complete the assigned task, "
            "avoid unnecessary tool calls, and return a concise result."
        ),
        "tools": ["echo", "get_time"],
    }
