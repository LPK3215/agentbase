"""Memory management tools — expose MemoryManager CRUD to agents.

Tools provided:
- ``memory_save``       — create or update a memory by key
- ``memory_get``        — retrieve a memory by key
- ``memory_list``       — list memories (optionally filtered by tag)
- ``memory_search``     — full-text search across memory content
- ``memory_delete``     — delete a memory by key
- ``memory_count``      — count memories (optionally by agent)
- ``memory_batch_save`` — batch save multiple memories in one transaction

The ``agent`` tool argument is kept for schema compatibility. When the
factory binds ``agent_name`` / ``agent_config.name`` in context, that name
always wins and the model-supplied ``agent`` is ignored. Search / list /
count never omit ``agent_name`` (no cross-agent scan).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from agentbase.core.memory import MemoryManager
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool


def _get_mgr(context: dict[str, Any] | None) -> MemoryManager:
    mgr = (context or {}).get("memory_manager")
    if mgr is None:
        raise RuntimeError("memory_manager not available in context")
    return mgr


def _has_agent_binding(context: dict[str, Any] | None) -> bool:
    ctx = context or {}
    name = ctx.get("agent_name")
    if isinstance(name, str) and name.strip():
        return True
    cfg_name = getattr(ctx.get("agent_config"), "name", None)
    return isinstance(cfg_name, str) and bool(cfg_name.strip())


def _bound_agent_name(context: dict[str, Any] | None, requested: str = "") -> str:
    """Resolve the agent namespace for a memory tool call.

    Bound context (factory) always wins over the model-supplied ``agent``.
    Unbound fixtures (unit tests) fall back to ``requested`` or ``default``.
    """
    ctx = context or {}
    name = ctx.get("agent_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    cfg_name = getattr(ctx.get("agent_config"), "name", None)
    if isinstance(cfg_name, str) and cfg_name.strip():
        return cfg_name.strip()
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    return "default"


@register_tool("memory_save", meta=ExtensionMeta(
    name="memory_save", kind="tool", description="Save or update a persistent memory by key.", requires_context=["memory_manager"]
))
def build_memory_save_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_save(
        key: str,
        content: str,
        agent: str = "default",
        tags: str = "",
        metadata: str = "",
    ) -> str:
        """Save a memory. ``tags`` is comma-separated. ``metadata`` is a JSON string."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        meta_dict = {}
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError:
                meta_dict = {"raw": metadata}
        mem = mgr.save(agent_name=agent_name, key=key, content=content, tags=tag_list, metadata=meta_dict)
        return f"Saved memory: key={mem.key} agent={mem.agent_name}"

    return memory_save


@register_tool("memory_get", meta=ExtensionMeta(
    name="memory_get", kind="tool", description="Retrieve a memory by key.", requires_context=["memory_manager"]
))
def build_memory_get_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_get(key: str, agent: str = "default") -> str:
        """Retrieve a memory by key."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        try:
            mem = mgr.get(agent_name=agent_name, key=key)
        except KeyError as exc:
            return str(exc)
        return json.dumps(mem.to_dict(), ensure_ascii=False)

    return memory_get


@register_tool("memory_list", meta=ExtensionMeta(
    name="memory_list", kind="tool", description="List memories, optionally filtered by agent or tag.", requires_context=["memory_manager"]
))
def build_memory_list_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_list(agent: str = "", tag: str = "") -> str:
        """List memories for the bound agent. Filter by tag. ``agent`` is ignored when bound."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        kwargs: dict[str, Any] = {"agent_name": agent_name}
        if tag:
            kwargs["tag"] = tag
        memories = mgr.list(**kwargs)
        if not memories:
            return "<no memories>"
        lines = [f"- [{m.agent_name}/{m.key}] tags={m.tags}: {m.content[:100]}" for m in memories]
        return "\n".join(lines)

    return memory_list


@register_tool("memory_search", meta=ExtensionMeta(
    name="memory_search", kind="tool", description="Full-text search across memory content.", requires_context=["memory_manager"]
))
def build_memory_search_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_search(query: str, agent: str = "") -> str:
        """Search memories by text within the bound agent. ``agent`` is ignored when bound."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        results = mgr.search(query=query, agent_name=agent_name)
        if not results:
            return f"<no memories matching '{query}'>"
        lines = [f"- [{m.agent_name}/{m.key}] {m.content[:100]}" for m in results]
        return "\n".join(lines)

    return memory_search


@register_tool("memory_delete", meta=ExtensionMeta(
    name="memory_delete", kind="tool", description="Delete a memory by key.", requires_context=["memory_manager"]
))
def build_memory_delete_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_delete(key: str, agent: str = "default") -> str:
        """Delete a memory by key."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        if mgr.delete(agent_name=agent_name, key=key):
            return f"Deleted memory: key={key} agent={agent_name}"
        return f"Memory not found: key={key} agent={agent_name}"

    return memory_delete


@register_tool("memory_count", meta=ExtensionMeta(
    name="memory_count", kind="tool", description="Count memories, optionally filtered by agent.", requires_context=["memory_manager"]
))
def build_memory_count_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_count(agent: str = "") -> str:
        """Count memories for the bound agent. ``agent`` is ignored when bound."""
        agent_name = _bound_agent_name(context, "" if bound else agent)
        count = mgr.count(agent_name=agent_name)
        return f"Memory count: {count} (agent={agent_name})"

    return memory_count


@register_tool("memory_batch_save", meta=ExtensionMeta(
    name="memory_batch_save", kind="tool", description="Batch save multiple memories in one transaction.", requires_context=["memory_manager"]
))
def build_memory_batch_save_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)
    bound = _has_agent_binding(context)

    @tool
    def memory_batch_save(
        items: str,
        agent: str = "default",
    ) -> str:
        """Batch save multiple memories.

        Args:
            items: JSON array of objects, each with "key", "content",
                   and optional "tags" (comma-separated) and "metadata" (JSON string).
            agent: Agent name for all memories. Ignored when the runtime binds one.

        Example items::

            [{"key": "pref1", "content": "likes dark mode"},
             {"key": "pref2", "content": "prefers Python", "tags": "lang,preference"}]
        """
        agent_name = _bound_agent_name(context, "" if bound else agent)
        try:
            data = json.loads(items)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON: {exc}"
        if not isinstance(data, list):
            return "items must be a JSON array"

        entries: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict) or "key" not in item or "content" not in item:
                continue
            tag_str = item.get("tags", "")
            tags = [t.strip() for t in tag_str.split(",") if t.strip()] if tag_str else []
            meta_str = item.get("metadata", "")
            meta = {}
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                except json.JSONDecodeError:
                    meta = {"raw": meta_str}
            entries.append({
                "key": item["key"],
                "content": item["content"],
                "tags": tags,
                "metadata": meta,
            })

        if not entries:
            return "No valid entries to save"

        saved = mgr.batch_save(agent_name=agent_name, entries=entries)
        return f"Batch saved {saved} memories for agent={agent_name}"

    return memory_batch_save
