"""Skill management tools — expose SkillManager CRUD to agents.

Tools provided:
- ``skill_list``   — list all skills
- ``skill_get``    — read a specific skill
- ``skill_create`` — create a new skill
- ``skill_update`` — update an existing skill
- ``skill_delete`` — delete a skill
- ``skill_search`` — search skills by text
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from agentbase.core.skills import SkillManager
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool

_META = ExtensionMeta(
    name="skill_ops",
    kind="tool",
    description="Skill management tools: list, get, create, update, delete, search.",
    requires_context=["skill_manager"],
)


def _get_mgr(context: dict[str, Any] | None) -> SkillManager:
    mgr = (context or {}).get("skill_manager")
    if mgr is None:
        raise RuntimeError("skill_manager not available in context")
    return mgr


@register_tool("skill_list", meta=ExtensionMeta(
    name="skill_list", kind="tool", description="List all registered skills.", requires_context=["skill_manager"]
))
def build_skill_list_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_list() -> str:
        """List all available skills with name and description."""
        skills = mgr.list()
        if not skills:
            return "<no skills>"
        lines = [f"- {s.name}: {s.description}" for s in skills]
        return "\n".join(lines)

    return skill_list


@register_tool("skill_get", meta=ExtensionMeta(
    name="skill_get", kind="tool", description="Read a skill's full content by name.", requires_context=["skill_manager"]
))
def build_skill_get_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_get(name: str) -> str:
        """Read the full content of a skill by name."""
        try:
            skill = mgr.get(name)
        except KeyError as exc:
            return str(exc)
        return skill.content

    return skill_get


@register_tool("skill_create", meta=ExtensionMeta(
    name="skill_create", kind="tool", description="Create a new skill file.", requires_context=["skill_manager"]
))
def build_skill_create_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_create(name: str, description: str, body: str, triggers: str = "") -> str:
        """Create a new skill. ``triggers`` is comma-separated."""
        trigger_list = [t.strip() for t in triggers.split(",") if t.strip()] if triggers else []
        try:
            skill = mgr.create(name, description=description, body=body, triggers=trigger_list)
        except ValueError as exc:
            return str(exc)
        return f"Created skill: {skill.name}"

    return skill_create


@register_tool("skill_update", meta=ExtensionMeta(
    name="skill_update", kind="tool", description="Update an existing skill.", requires_context=["skill_manager"]
))
def build_skill_update_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_update(
        name: str,
        description: str = "",
        body: str = "",
        triggers: str = "",
    ) -> str:
        """Update a skill. Pass empty string to skip a field."""
        kwargs: dict[str, Any] = {}
        if description:
            kwargs["description"] = description
        if body:
            kwargs["body"] = body
        if triggers:
            kwargs["triggers"] = [t.strip() for t in triggers.split(",") if t.strip()]
        try:
            mgr.update(name, **kwargs)
        except KeyError as exc:
            return str(exc)
        return f"Updated skill: {name}"

    return skill_update


@register_tool("skill_delete", meta=ExtensionMeta(
    name="skill_delete", kind="tool", description="Delete a skill by name.", requires_context=["skill_manager"]
))
def build_skill_delete_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_delete(name: str) -> str:
        """Delete a skill by name."""
        if mgr.delete(name):
            return f"Deleted skill: {name}"
        return f"Skill not found: {name}"

    return skill_delete


@register_tool("skill_search", meta=ExtensionMeta(
    name="skill_search", kind="tool", description="Search skills by text query.", requires_context=["skill_manager"]
))
def build_skill_search_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def skill_search(query: str) -> str:
        """Search skills by text. Matches name, description, and body."""
        results = mgr.search(query)
        if not results:
            return f"<no skills matching '{query}'>"
        lines = [f"- {s.name}: {s.description}" for s in results]
        return "\n".join(lines)

    return skill_search
