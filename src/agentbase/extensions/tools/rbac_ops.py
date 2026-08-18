"""RBAC tools — expose role/permission reads to agents (read-only).

Tools provided:
- ``rbac_check_permission`` — check whether a user holds a resource:action
- ``rbac_list_roles``       — list role definitions with permissions

Deliberately read-only: agents may consult the permission model but
cannot create roles or grant/revoke assignments (those are admin API
operations).

Requires the RBAC service (``rbac.enabled=true``) and the
``rbac_manager`` context key provided by the agent factory.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)


def _get_mgr(context: dict[str, Any] | None) -> Any:
    mgr = (context or {}).get("rbac_manager")
    if mgr is None:
        raise RuntimeError("rbac_manager not available in context")
    return mgr


@register_tool("rbac_check_permission", meta=ExtensionMeta(
    name="rbac_check_permission",
    kind="tool",
    description="Check whether a user has a resource:action permission (read-only).",
    requires_context=["rbac_manager"],
    default_enabled=False,
    tags=["rbac", "security"],
))
def build_rbac_check_permission_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def rbac_check_permission(username: str, resource: str, action: str) -> str:
        """Check a user's permission.

        Args:
            username: User to check (required).
            resource: Resource name, e.g. "agents" (required).
            action: Action name, e.g. "invoke" (required).

        Returns:
            JSON {"allowed": bool} — False when the service is disabled
            or the user holds no matching role.
        """
        allowed = mgr.check_permission(username, resource, action)
        return json.dumps(
            {"username": username, "resource": resource, "action": action, "allowed": allowed},
            ensure_ascii=False,
        )

    return rbac_check_permission


@register_tool("rbac_list_roles", meta=ExtensionMeta(
    name="rbac_list_roles",
    kind="tool",
    description="List RBAC roles with their permissions (read-only).",
    requires_context=["rbac_manager"],
    default_enabled=False,
    tags=["rbac", "security"],
))
def build_rbac_list_roles_tool(context: dict[str, Any] | None = None):
    mgr = _get_mgr(context)

    @tool
    def rbac_list_roles(include_system: bool = True) -> str:
        """List role definitions sorted by name.

        Args:
            include_system: Include built-in system roles (default true).

        Returns:
            JSON list of {name, permissions, description, is_system}.
        """
        roles = mgr.list_roles()
        if not include_system:
            roles = [r for r in roles if not r.is_system]
        return json.dumps([r.to_dict() for r in roles], ensure_ascii=False)

    return rbac_list_roles
