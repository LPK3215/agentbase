from __future__ import annotations

from typing import TYPE_CHECKING

from agentbase.registry.base import Builder, Registry

if TYPE_CHECKING:
    from agentbase.extensions._meta import ExtensionMeta

subagent_registry: Registry[Builder] = Registry("subagent")


def register_subagent(name: str, *, override: bool = False, meta: ExtensionMeta | None = None):
    return subagent_registry.decorator(name, override=override, meta=meta)
