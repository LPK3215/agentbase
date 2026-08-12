from __future__ import annotations

from agentbase.registry.base import Builder, Registry

checkpointer_registry: Registry[Builder] = Registry("checkpointer")


def register_checkpointer(name: str, *, override: bool = False):
    return checkpointer_registry.decorator(name, override=override)
