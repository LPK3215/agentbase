"""RBAC (role-based access control) service — runtime-managed roles & permissions.

Complements the static auth config (hardcoded admin/user/readonly roles in
``extensions/auth.py``) with a **runtime-manageable** role/permission store:

- Create / update / delete custom roles with fine-grained permissions
- Assign / revoke roles to users (by username)
- Check permissions with wildcard matching (``*``, ``agents:*``, ``*:read``)
- Built-in system roles (``admin`` / ``user`` / ``readonly``) aligned with
  ``DEFAULT_ROLE_PERMISSIONS`` and protected from deletion

Permission format: ``resource:action`` (e.g. ``agents:invoke``,
``calendar:write``). Wildcards:
- ``*``            — full access (matches anything)
- ``agents:*``     — all actions on the ``agents`` resource
- ``*:read``       — read access to every resource

The coarse-grained legacy permissions (``read`` / ``write`` / ``invoke`` /
``delete`` / ``admin``) keep working: they are normalized to ``*:<action>``
so ``*:*`` covers them.

Pluggable backends:
- ``InMemoryRbacProvider`` (default) — zero-config, thread-safe, seeds the
  three system roles
- ``NullRbacProvider`` — no-op, zero overhead when disabled
- Register custom providers with ``@register_rbac_provider("name")``

Usage::

    from agentbase.core.rbac import RbacManager

    manager = RbacManager(provider="memory", enabled=True)

    manager.create_role("editor", permissions=["agents:invoke", "kb:write"])
    manager.assign_role("alice", "editor")
    manager.check_permission("alice", "agents", "invoke")   # -> True
    manager.check_permission("alice", "users", "delete")    # -> False
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agentbase.runtime.errors import RegistryError
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "SYSTEM_ROLES",
    "RoleDefinition",
    "RbacStats",
    "RbacProvider",
    "NullRbacProvider",
    "InMemoryRbacProvider",
    "RbacRegistry",
    "rbac_registry",
    "register_rbac_provider",
    "RbacManager",
    "get_rbac_manager",
    "set_rbac_manager",
    "reset_rbac_manager",
    "normalize_permission",
    "permission_matches",
]

# ---------------------------------------------------------------------------
# Constants and validation limits
# ---------------------------------------------------------------------------

SYSTEM_ROLES: dict[str, list[str]] = {
    "admin": ["*"],
    "user": ["*:read", "*:write", "*:invoke"],
    "readonly": ["*:read"],
}

_MAX_ROLES = 500
_MAX_USERS = 10_000
_MAX_ROLE_NAME = 64
_MAX_DESCRIPTION = 500
_MAX_PERMISSIONS_PER_ROLE = 100
_MAX_ROLES_PER_USER = 20

_LEGACY_ACTIONS = {"read", "write", "invoke", "delete", "admin"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_permission(permission: str) -> str:
    """Normalize a permission string to ``resource:action`` form.

    Legacy bare actions (``read`` / ``write`` / ...) become ``*:<action>``.
    A bare ``*`` stays ``*`` (full access).
    """
    permission = (permission or "").strip().lower()
    if not permission:
        raise RegistryError("Permission cannot be empty")
    if permission == "*":
        return "*"
    if ":" not in permission:
        if permission in _LEGACY_ACTIONS:
            return f"*:{permission}"
        raise RegistryError(
            f"Invalid permission: {permission!r} (expected 'resource:action' or '*')"
        )
    resource, action = permission.split(":", 1)
    if not resource or not action:
        raise RegistryError(
            f"Invalid permission: {permission!r} (empty resource or action)"
        )
    return permission


def permission_matches(granted: str, resource: str, action: str) -> bool:
    """Return True when a granted permission covers ``resource:action``.

    Wildcards:
    - ``*``           — matches everything
    - ``<res>:*``     — matches every action on ``<res>``
    - ``*:<action>``  — matches ``<action>`` on every resource
    """
    if granted == "*":
        return True
    if ":" not in granted:
        return False
    g_res, g_act = granted.split(":", 1)
    res_ok = g_res == "*" or g_res == resource
    act_ok = g_act == "*" or g_act == action
    return res_ok and act_ok


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RoleDefinition:
    """A role with a set of granted permissions."""

    name: str
    permissions: list[str] = field(default_factory=list)
    description: str = ""
    is_system: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "permissions": list(self.permissions),
            "description": self.description,
            "is_system": self.is_system,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RbacStats:
    """Aggregate statistics over the RBAC store."""

    total_roles: int = 0
    system_roles: int = 0
    custom_roles: int = 0
    assigned_users: int = 0
    total_assignments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_roles": self.total_roles,
            "system_roles": self.system_roles,
            "custom_roles": self.custom_roles,
            "assigned_users": self.assigned_users,
            "total_assignments": self.total_assignments,
        }


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class RbacProvider(Protocol):
    """Storage contract for roles and user-role assignments."""

    def create_role(self, role: RoleDefinition) -> RoleDefinition: ...
    def get_role(self, name: str) -> RoleDefinition | None: ...
    def list_roles(self) -> list[RoleDefinition]: ...
    def update_role(self, name: str, role: RoleDefinition) -> RoleDefinition | None: ...
    def delete_role(self, name: str) -> bool: ...
    def assign_role(self, username: str, role_name: str) -> None: ...
    def revoke_role(self, username: str, role_name: str) -> bool: ...
    def get_user_roles(self, username: str) -> list[str]: ...
    def get_assigned_users(self, role_name: str) -> list[str]: ...
    def get_stats(self) -> RbacStats: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Null provider (disabled mode)
# ---------------------------------------------------------------------------

class NullRbacProvider:
    """No-op RBAC provider — no roles, no assignments, deny everything.

    Used when the RBAC service is disabled (``rbac.enabled=false``).
    """

    def create_role(self, role: RoleDefinition) -> RoleDefinition:
        return role

    def get_role(self, name: str) -> RoleDefinition | None:
        return None

    def list_roles(self) -> list[RoleDefinition]:
        return []

    def update_role(self, name: str, role: RoleDefinition) -> RoleDefinition | None:
        return None

    def delete_role(self, name: str) -> bool:
        return False

    def assign_role(self, username: str, role_name: str) -> None:
        pass

    def revoke_role(self, username: str, role_name: str) -> bool:
        return False

    def get_user_roles(self, username: str) -> list[str]:
        return []

    def get_assigned_users(self, role_name: str) -> list[str]:
        return []

    def get_stats(self) -> RbacStats:
        return RbacStats()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory provider (default, zero-config)
# ---------------------------------------------------------------------------

class InMemoryRbacProvider:
    """In-memory RBAC store — thread-safe, seeds the system roles.

    Args:
        seed_system_roles: When True (default) the three built-in roles
            (``admin`` / ``user`` / ``readonly``) are created on init and
            protected from deletion.
    """

    def __init__(self, seed_system_roles: bool = True) -> None:
        self._roles: dict[str, RoleDefinition] = {}
        self._assignments: dict[str, set[str]] = {}  # username -> role names
        self._lock = threading.RLock()
        if seed_system_roles:
            for name, perms in SYSTEM_ROLES.items():
                self._roles[name] = RoleDefinition(
                    name=name,
                    permissions=list(perms),
                    description=f"Built-in system role: {name}",
                    is_system=True,
                )

    # -- RbacProvider ---------------------------------------------------------

    def create_role(self, role: RoleDefinition) -> RoleDefinition:
        with self._lock:
            if role.name in self._roles:
                raise RegistryError(f"Role already exists: {role.name}")
            if len(self._roles) >= _MAX_ROLES:
                raise RegistryError(
                    f"Too many roles: {len(self._roles)} (max {_MAX_ROLES})"
                )
            self._roles[role.name] = role
        logger.info(
            "RBAC role created: %s",
            role.name,
            extra={"event": "rbac.role_created", "role": role.name},
        )
        return role

    def get_role(self, name: str) -> RoleDefinition | None:
        with self._lock:
            return self._roles.get(name)

    def list_roles(self) -> list[RoleDefinition]:
        with self._lock:
            return sorted(self._roles.values(), key=lambda r: r.name)

    def update_role(self, name: str, role: RoleDefinition) -> RoleDefinition | None:
        with self._lock:
            existing = self._roles.get(name)
            if existing is None:
                return None
            # system flag is preserved; permissions/description updated
            role.is_system = existing.is_system
            role.created_at = existing.created_at
            role.updated_at = _now_iso()
            self._roles[name] = role
        logger.info(
            "RBAC role updated: %s",
            name,
            extra={"event": "rbac.role_updated", "role": name},
        )
        return role

    def delete_role(self, name: str) -> bool:
        with self._lock:
            existing = self._roles.get(name)
            if existing is None:
                return False
            if existing.is_system:
                raise RegistryError(f"Cannot delete system role: {name}")
            self._roles.pop(name, None)
            for roles in self._assignments.values():
                roles.discard(name)
        logger.info(
            "RBAC role deleted: %s",
            name,
            extra={"event": "rbac.role_deleted", "role": name},
        )
        return True

    def assign_role(self, username: str, role_name: str) -> None:
        with self._lock:
            if role_name not in self._roles:
                raise RegistryError(f"Unknown role: {role_name}")
            roles = self._assignments.setdefault(username, set())
            if len(roles) >= _MAX_ROLES_PER_USER and role_name not in roles:
                raise RegistryError(
                    f"Too many roles for user {username!r} "
                    f"(max {_MAX_ROLES_PER_USER})"
                )
            roles.add(role_name)

    def revoke_role(self, username: str, role_name: str) -> bool:
        with self._lock:
            roles = self._assignments.get(username)
            if not roles or role_name not in roles:
                return False
            roles.discard(role_name)
            if not roles:
                self._assignments.pop(username, None)
            return True

    def get_user_roles(self, username: str) -> list[str]:
        with self._lock:
            return sorted(self._assignments.get(username, set()))

    def get_assigned_users(self, role_name: str) -> list[str]:
        with self._lock:
            return sorted(
                user for user, roles in self._assignments.items() if role_name in roles
            )

    def get_stats(self) -> RbacStats:
        with self._lock:
            stats = RbacStats(
                total_roles=len(self._roles),
                assigned_users=len(self._assignments),
                total_assignments=sum(len(r) for r in self._assignments.values()),
            )
            for role in self._roles.values():
                if role.is_system:
                    stats.system_roles += 1
                else:
                    stats.custom_roles += 1
            return stats

    def close(self) -> None:
        with self._lock:
            self._roles.clear()
            self._assignments.clear()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class RbacRegistry:
    """Thread-safe registry for RBAC providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., RbacProvider]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., RbacProvider],
        *,
        override: bool = False,
    ) -> None:
        key = name.strip().lower()
        with self._lock:
            if not key:
                raise RegistryError("Cannot register empty RBAC provider name")
            if key in self._factories and not override:
                raise RegistryError(f"RBAC provider already registered: {key}")
            self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> RbacProvider:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                available = ", ".join(sorted(self._factories)) or "<empty>"
                raise RegistryError(
                    f"Unknown RBAC provider: {key}. Available: {available}"
                )
            factory = self._factories[key]
        return factory(**kwargs)

    def has(self, name: str) -> bool:
        with self._lock:
            return name.strip().lower() in self._factories

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._factories)

    def unregister(self, name: str) -> bool:
        key = name.strip().lower()
        with self._lock:
            if key not in self._factories:
                return False
            self._factories.pop(key, None)
            return True


# Global singleton
rbac_registry = RbacRegistry()

# Register defaults
rbac_registry.register("null", NullRbacProvider)
rbac_registry.register("memory", InMemoryRbacProvider)


def register_rbac_provider(name: str, *, override: bool = False):
    """Decorator: register an RBAC provider class.

    Usage::

        @register_rbac_provider("postgres")
        class PostgresRbacProvider:
            def create_role(self, role): ...
    """
    def decorator(factory: Callable[..., RbacProvider]):
        rbac_registry.register(name, factory, override=override)
        return factory
    return decorator


# ---------------------------------------------------------------------------
# Manager — high-level facade
# ---------------------------------------------------------------------------

class RbacManager:
    """High-level RBAC manager.

    Wraps an ``RbacProvider`` for role CRUD, user-role assignment, and
    permission checks (with wildcard matching), with input validation.
    When ``enabled=False`` it wraps a ``NullRbacProvider`` — every
    ``check_permission`` call returns False (deny by default).

    Usage::

        manager = RbacManager(provider="memory", enabled=True)
        manager.create_role("editor", permissions=["agents:invoke"])
        manager.assign_role("alice", "editor")
        manager.check_permission("alice", "agents", "invoke")  # True
    """

    def __init__(
        self,
        *,
        provider: str = "null",
        enabled: bool = False,
        **provider_kwargs: Any,
    ) -> None:
        self._enabled = enabled
        if not enabled:
            self._provider: RbacProvider = NullRbacProvider()
        else:
            self._provider = rbac_registry.create(provider, **provider_kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def provider(self) -> RbacProvider:
        return self._provider

    # -- validation -----------------------------------------------------------

    @staticmethod
    def _validate_role_name(name: str) -> str:
        name = (name or "").strip().lower()
        if not name:
            raise RegistryError("Role name is required")
        if len(name) > _MAX_ROLE_NAME:
            raise RegistryError(
                f"Role name too long: {len(name)} (max {_MAX_ROLE_NAME})"
            )
        if not name.replace("-", "").replace("_", "").isalnum():
            raise RegistryError(
                f"Invalid role name: {name!r} (letters/digits/hyphens/underscores)"
            )
        return name

    @staticmethod
    def _validate_username(username: str) -> str:
        username = (username or "").strip()
        if not username:
            raise RegistryError("Username is required")
        if len(username) > 128:
            raise RegistryError(f"Username too long (max 128)")
        return username

    # -- role CRUD --------------------------------------------------------------

    def create_role(
        self,
        name: str,
        *,
        permissions: list[str],
        description: str = "",
    ) -> RoleDefinition:
        """Create a custom role.

        Raises:
            RegistryError: On duplicate name, invalid permissions, or limits.
        """
        name = self._validate_role_name(name)
        if len(description) > _MAX_DESCRIPTION:
            raise RegistryError(
                f"description too long: {len(description)} (max {_MAX_DESCRIPTION})"
            )
        if not permissions:
            raise RegistryError("Role requires at least one permission")
        if len(permissions) > _MAX_PERMISSIONS_PER_ROLE:
            raise RegistryError(
                f"Too many permissions: {len(permissions)} "
                f"(max {_MAX_PERMISSIONS_PER_ROLE})"
            )
        normalized = sorted({normalize_permission(p) for p in permissions})
        role = RoleDefinition(
            name=name, permissions=normalized, description=description
        )
        return self._provider.create_role(role)

    def get_role(self, name: str) -> RoleDefinition | None:
        """Get a role by name (None when missing)."""
        return self._provider.get_role((name or "").strip().lower())

    def list_roles(self) -> list[RoleDefinition]:
        """List all roles (sorted by name)."""
        return self._provider.list_roles()

    def update_role(
        self,
        name: str,
        *,
        permissions: list[str] | None = None,
        description: str | None = None,
    ) -> RoleDefinition | None:
        """Update a role's permissions and/or description.

        Returns the updated role, or None when the role is missing.
        """
        name = (name or "").strip().lower()
        current = self._provider.get_role(name)
        if current is None:
            return None
        new_permissions = current.permissions if permissions is None else permissions
        if permissions is not None:
            if not permissions:
                raise RegistryError("Role requires at least one permission")
            if len(permissions) > _MAX_PERMISSIONS_PER_ROLE:
                raise RegistryError(
                    f"Too many permissions: {len(permissions)} "
                    f"(max {_MAX_PERMISSIONS_PER_ROLE})"
                )
            new_permissions = sorted({normalize_permission(p) for p in permissions})
        new_description = (
            current.description if description is None else description
        )
        if len(new_description) > _MAX_DESCRIPTION:
            raise RegistryError(
                f"description too long (max {_MAX_DESCRIPTION})"
            )
        updated = RoleDefinition(
            name=name,
            permissions=list(new_permissions),
            description=new_description,
            is_system=current.is_system,
            created_at=current.created_at,
            updated_at=_now_iso(),
        )
        return self._provider.update_role(name, updated)

    def delete_role(self, name: str) -> bool:
        """Delete a custom role (system roles are protected).

        Returns True when deleted; raises for system roles.
        """
        name = (name or "").strip().lower()
        role = self._provider.get_role(name)
        if role is not None and role.is_system:
            raise RegistryError(f"Cannot delete system role: {name}")
        return self._provider.delete_role(name)

    # -- user assignment ---------------------------------------------------------

    def assign_role(self, username: str, role_name: str) -> None:
        """Assign a role to a user (idempotent)."""
        username = self._validate_username(username)
        role_name = (role_name or "").strip().lower()
        if self._provider.get_role(role_name) is None:
            raise RegistryError(f"Unknown role: {role_name}")
        self._provider.assign_role(username, role_name)

    def revoke_role(self, username: str, role_name: str) -> bool:
        """Revoke a role from a user. Returns False when not assigned."""
        username = self._validate_username(username)
        return self._provider.revoke_role(username, role_name.strip().lower())

    def get_user_roles(self, username: str) -> list[str]:
        """List role names assigned to a user (sorted)."""
        return self._provider.get_user_roles(self._validate_username(username))

    def get_assigned_users(self, role_name: str) -> list[str]:
        """List usernames assigned to a role (sorted)."""
        return self._provider.get_assigned_users(role_name.strip().lower())

    # -- permission checks ---------------------------------------------------------

    def get_user_permissions(self, username: str) -> list[str]:
        """Union of all permissions across the user's roles (sorted)."""
        roles = self.get_user_roles(username)
        perms: set[str] = set()
        for role_name in roles:
            role = self._provider.get_role(role_name)
            if role is not None:
                perms.update(role.permissions)
        return sorted(perms)

    def check_permission(self, username: str, resource: str, action: str) -> bool:
        """Return True when any of the user's roles grants resource:action.

        Uses wildcard matching (``*`` / ``res:*`` / ``*:act``).
        Denies by default (no roles / unknown user / disabled service).
        """
        resource = (resource or "").strip().lower()
        action = (action or "").strip().lower()
        if not resource or not action:
            return False
        for role_name in self._provider.get_user_roles(username):
            role = self._provider.get_role(role_name)
            if role is None:
                continue
            for granted in role.permissions:
                if permission_matches(granted, resource, action):
                    return True
        return False

    def get_stats(self) -> RbacStats:
        """Aggregate statistics (role counts, assignments)."""
        return self._provider.get_stats()

    def close(self) -> None:
        """Release provider resources."""
        self._provider.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_rbac_manager: RbacManager | None = None
_rbac_manager_lock = threading.Lock()


def get_rbac_manager() -> RbacManager:
    """Get the process-wide RbacManager (creates a disabled one by default)."""
    global _rbac_manager
    if _rbac_manager is None:
        with _rbac_manager_lock:
            if _rbac_manager is None:
                _rbac_manager = RbacManager()
    return _rbac_manager


def set_rbac_manager(manager: RbacManager) -> None:
    """Replace the process-wide RbacManager."""
    global _rbac_manager
    with _rbac_manager_lock:
        _rbac_manager = manager


def reset_rbac_manager() -> None:
    """Reset the process-wide RbacManager (for testing)."""
    global _rbac_manager
    with _rbac_manager_lock:
        _rbac_manager = None
