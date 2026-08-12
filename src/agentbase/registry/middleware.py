from __future__ import annotations

from typing import TYPE_CHECKING

from agentbase.registry.base import Builder, Registry

if TYPE_CHECKING:
    from agentbase.extensions._meta import ExtensionMeta

middleware_registry: Registry[Builder] = Registry("middleware")


def register_middleware(name: str, *, override: bool = False, meta: ExtensionMeta | None = None):
    return middleware_registry.decorator(name, override=override, meta=meta)
