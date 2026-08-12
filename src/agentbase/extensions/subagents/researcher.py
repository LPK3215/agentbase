from __future__ import annotations

from typing import Any

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.subagents import register_subagent

_RESEARCHER_META = ExtensionMeta(
    name="researcher", kind="subagent", description="Research a topic and return a concise summary.", requires_context=[]
)


@register_subagent("researcher", meta=_RESEARCHER_META)
def build_researcher(context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": "researcher",
        "description": "Researches a given topic using available tools and returns a concise summary.",
        "system_prompt": (
            "You are a research subagent. Investigate the assigned topic, "
            "gather relevant information using available tools, and return "
            "a concise, well-structured summary with key findings."
        ),
        "tools": ["echo", "get_time"],
    }