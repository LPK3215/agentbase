from __future__ import annotations

from typing import TYPE_CHECKING

from agentbase.registry.base import Builder, Registry

if TYPE_CHECKING:
    from agentbase.extensions._meta import ExtensionMeta

tool_registry: Registry[Builder] = Registry("tool")


def register_tool(name: str, *, override: bool = False, meta: ExtensionMeta | None = None):
    return tool_registry.decorator(name, override=override, meta=meta)
