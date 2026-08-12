"""MCP tools — expose MCP server tools to agents.

Tools provided:
- ``mcp_list_tools`` — list available MCP tools
- ``mcp_call_tool``  — call a tool on an MCP server
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from agentbase.core.mcp import MCPManager
from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool


def _get_mcp_manager(context: dict[str, Any] | None) -> MCPManager:
    mgr = (context or {}).get("mcp_manager")
    if mgr is None:
        raise RuntimeError("mcp_manager not available in context")
    return mgr


@register_tool("mcp_list_tools", meta=ExtensionMeta(
    name="mcp_list_tools", kind="tool",
    description="List tools available on connected MCP servers.",
    requires_context=["mcp_manager"],
))
def build_mcp_list_tools(context: dict[str, Any] | None = None):
    mgr = _get_mcp_manager(context)

    @tool
    def mcp_list_tools() -> str:
        """List all tools from connected MCP servers."""
        tools = mgr.list_all_tools()
        if not tools:
            return "<no MCP tools available>"
        lines: list[str] = []
        for t in tools:
            lines.append(f"- [{t.server_name}] {t.name}: {t.description}")
        return "\n".join(lines)

    return mcp_list_tools


@register_tool("mcp_call_tool", meta=ExtensionMeta(
    name="mcp_call_tool", kind="tool",
    description="Call a tool on an MCP server.",
    requires_context=["mcp_manager"],
))
def build_mcp_call_tool(context: dict[str, Any] | None = None):
    mgr = _get_mcp_manager(context)

    @tool
    def mcp_call_tool(tool_name: str, arguments: str = "") -> str:
        """Call a tool on an MCP server.

        Args:
            tool_name: The name of the MCP tool to call.
            arguments: JSON string of arguments to pass to the tool.

        Returns:
            The tool's output content.
        """
        args: dict[str, Any] = {}
        if arguments:
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                args = {"input": arguments}

        result = mgr.call_tool(tool_name, args)
        if result.is_error:
            return f"MCP tool error: {result.content}"
        return result.content

    return mcp_call_tool
