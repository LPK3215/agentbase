from __future__ import annotations

from agentbase.registry.base import Builder, Registry

backend_registry: Registry[Builder] = Registry("backend")


def register_backend(name: str, *, override: bool = False):
    return backend_registry.decorator(name, override=override)
