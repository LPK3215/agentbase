from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.extensions.tools._workspace import resolve_within_workspace
from agentbase.registry.tools import register_tool

_ECHO_META = ExtensionMeta(
    name="echo", kind="tool", description="Echo text back unchanged.", requires_context=[], default_enabled=True
)
_GET_TIME_META = ExtensionMeta(
    name="get_time",
    kind="tool",
    description="Return current UTC timestamp in ISO format.",
    requires_context=[],
    default_enabled=True,
)
_LIST_WS_META = ExtensionMeta(
    name="list_workspace",
    kind="tool",
    description="List files under the configured workspace directory.",
    requires_context=["workspace_dir"],
    default_enabled=True,
)


@register_tool("echo", meta=_ECHO_META)
def build_echo_tool(context: dict[str, Any] | None = None):
    @tool
    def echo(text: str) -> str:
        """Echo text back unchanged."""
        return text

    return echo


@register_tool("get_time", meta=_GET_TIME_META)
def build_get_time_tool(context: dict[str, Any] | None = None):
    @tool
    def get_time() -> str:
        """Return the current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    return get_time


@register_tool("list_workspace", meta=_LIST_WS_META)
def build_list_workspace_tool(context: dict[str, Any] | None = None):
    context = context or {}
    workspace = context.get("workspace_dir")
    if workspace is None:
        root_dir = context.get("root_dir")
        workspace = Path(root_dir) / "workspace" if root_dir else Path("workspace")
    workspace_path = Path(workspace)

    @tool
    def list_workspace(relative_path: str = ".") -> str:
        """List files under the configured workspace directory."""
        try:
            target = resolve_within_workspace(workspace_path, relative_path)
        except ValueError as exc:
            return str(exc)

        if not target.exists():
            return f"Path not found: {relative_path}"
        if target.is_file():
            return f"FILE\t{target.name}"

        lines: list[str] = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            kind = "DIR" if item.is_dir() else "FILE"
            lines.append(f"{kind}\t{item.name}")
        return "\n".join(lines) if lines else "<empty>"

    return list_workspace
