"""Tests for the RBAC core service.

Covers:
- Permission normalization (resource:action / legacy actions / wildcards)
- Wildcard matching semantics
- Data model (RoleDefinition / RbacStats)
- Null provider (disabled no-op semantics)
- InMemory provider (system role seeding, CRUD, assignment, stats)
- Registry (register / create / duplicate / unknown / unregister)
- Manager (validation, role CRUD, assignment, permission checks)
- Singleton (get / set / reset)
- Concurrency (parallel assignments)
- Protocol compliance
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.rbac import (
    SYSTEM_ROLES,
    InMemoryRbacProvider,
    NullRbacProvider,
    RbacManager,
    RbacProvider,
    RbacRegistry,
    RoleDefinition,
    get_rbac_manager,
    normalize_permission,
    permission_matches,
    register_rbac_provider,
    reset_rbac_manager,
    set_rbac_manager,
)
from agentbase.runtime.errors import RegistryError

# ---------------------------------------------------------------------------
# Permission normalization & matching
# ---------------------------------------------------------------------------


class TestNormalizePermission:
    def test_plain_resource_action(self):
        assert normalize_permission("agents:invoke") == "agents:invoke"

    def test_strips_and_lowercases(self):
        assert normalize_permission("  Agents:Invoke  ") == "agents:invoke"

    def test_wildcard(self):
        assert normalize_permission("*") == "*"

    def test_legacy_actions(self):
        for action in ("read", "write", "invoke", "delete", "admin"):
            assert normalize_permission(action) == f"*:{action}"

    def test_empty_raises(self):
        with pytest.raises(RegistryError, match="empty"):
            normalize_permission("")

    def test_unknown_bare_token_raises(self):
        with pytest.raises(RegistryError, match="Invalid permission"):
            normalize_permission("frobnicate")

    def test_empty_resource_raises(self):
        with pytest.raises(RegistryError, match="empty resource or action"):
            normalize_permission(":read")


class TestPermissionMatching:
    def test_full_wildcard(self):
        assert permission_matches("*", "anything", "everything")

    def test_exact(self):
        assert permission_matches("agents:invoke", "agents", "invoke")
        assert not permission_matches("agents:invoke", "agents", "read")

    def test_resource_wildcard_action(self):
        assert permission_matches("agents:*", "agents", "invoke")
        assert permission_matches("agents:*", "agents", "delete")
        assert not permission_matches("agents:*", "users", "read")

    def test_action_wildcard_resource(self):
        assert permission_matches("*:read", "agents", "read")
        assert permission_matches("*:read", "users", "read")
        assert not permission_matches("*:read", "agents", "write")

    def test_no_colon_never_matches(self):
        assert not permission_matches("agents", "agents", "read")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TestRoleDefinition:
    def test_defaults(self):
        role = RoleDefinition(name="editor", permissions=["agents:invoke"])
        assert role.description == ""
        assert role.is_system is False
        assert role.created_at
        assert role.updated_at

    def test_to_dict(self):
        role = RoleDefinition(name="editor", permissions=["a:b"], is_system=True)
        d = role.to_dict()
        assert d["name"] == "editor"
        assert d["permissions"] == ["a:b"]
        assert d["is_system"] is True


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------


class TestNullProvider:
    def test_all_noop(self):
        p = NullRbacProvider()
        role = p.create_role(RoleDefinition(name="x", permissions=["a:b"]))
        assert role.name == "x"
        assert p.get_role("x") is None
        assert p.list_roles() == []
        assert p.update_role("x", role) is None
        assert p.delete_role("x") is False
        p.assign_role("u", "x")  # no-op, no raise
        assert p.revoke_role("u", "x") is False
        assert p.get_user_roles("u") == []
        assert p.get_assigned_users("x") == []
        assert p.get_stats().total_roles == 0
        p.close()


# ---------------------------------------------------------------------------
# InMemory provider
# ---------------------------------------------------------------------------


class TestInMemoryProvider:
    def test_system_roles_seeded(self):
        p = InMemoryRbacProvider()
        names = [r.name for r in p.list_roles()]
        assert names == ["admin", "readonly", "user"]  # sorted
        admin = p.get_role("admin")
        assert admin is not None
        assert admin.permissions == ["*"]
        assert admin.is_system is True

    def test_no_seed(self):
        p = InMemoryRbacProvider(seed_system_roles=False)
        assert p.list_roles() == []

    def test_system_roles_match_legacy_mapping(self):
        p = InMemoryRbacProvider()
        for name, perms in SYSTEM_ROLES.items():
            role = p.get_role(name)
            assert role is not None
            assert role.permissions == perms

    def test_create_and_get(self):
        p = InMemoryRbacProvider()
        p.create_role(RoleDefinition(name="editor", permissions=["agents:invoke"]))
        role = p.get_role("editor")
        assert role is not None
        assert role.permissions == ["agents:invoke"]

    def test_create_duplicate(self):
        p = InMemoryRbacProvider()
        with pytest.raises(RegistryError, match="already exists"):
            p.create_role(RoleDefinition(name="admin", permissions=["a:b"]))

    def test_update_preserves_system_flag_and_created_at(self):
        p = InMemoryRbacProvider()
        before = p.get_role("admin")
        updated = p.update_role("admin", RoleDefinition(name="admin", permissions=["x:y"]))
        assert updated is not None
        assert updated.is_system is True  # preserved
        assert updated.created_at == before.created_at
        assert updated.permissions == ["x:y"]

    def test_update_missing(self):
        assert InMemoryRbacProvider().update_role("nope", RoleDefinition(name="nope", permissions=[])) is None

    def test_delete_system_role_raises(self):
        p = InMemoryRbacProvider()
        with pytest.raises(RegistryError, match="system role"):
            p.delete_role("admin")

    def test_delete_custom_role_cleans_assignments(self):
        p = InMemoryRbacProvider()
        p.create_role(RoleDefinition(name="editor", permissions=["a:b"]))
        p.assign_role("alice", "editor")
        assert p.delete_role("editor") is True
        assert p.get_user_roles("alice") == []
        assert p.delete_role("editor") is False

    def test_assign_and_revoke(self):
        p = InMemoryRbacProvider()
        p.create_role(RoleDefinition(name="editor", permissions=["a:b"]))
        p.assign_role("alice", "editor")
        p.assign_role("alice", "editor")  # idempotent
        assert p.get_user_roles("alice") == ["editor"]
        assert p.get_assigned_users("editor") == ["alice"]
        assert p.revoke_role("alice", "editor") is True
        assert p.revoke_role("alice", "editor") is False

    def test_assign_unknown_role(self):
        p = InMemoryRbacProvider()
        with pytest.raises(RegistryError, match="Unknown role"):
            p.assign_role("alice", "nope")

    def test_stats(self):
        p = InMemoryRbacProvider()
        p.create_role(RoleDefinition(name="editor", permissions=["a:b"]))
        p.assign_role("alice", "editor")
        p.assign_role("alice", "user")
        p.assign_role("bob", "admin")
        stats = p.get_stats()
        assert stats.total_roles == 4
        assert stats.system_roles == 3
        assert stats.custom_roles == 1
        assert stats.assigned_users == 2
        assert stats.total_assignments == 3

    def test_close_clears(self):
        p = InMemoryRbacProvider()
        p.close()
        assert p.list_roles() == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_defaults_registered(self):
        from agentbase.core.rbac import rbac_registry

        assert rbac_registry.has("memory")
        assert rbac_registry.has("null")

    def test_create_memory(self):
        from agentbase.core.rbac import rbac_registry

        p = rbac_registry.create("memory")
        assert isinstance(p, InMemoryRbacProvider)

    def test_unknown_provider(self):
        with pytest.raises(RegistryError, match="Unknown RBAC provider"):
            RbacRegistry().create("nope")

    def test_duplicate_registration(self):
        reg = RbacRegistry()
        reg.register("a", NullRbacProvider)
        with pytest.raises(RegistryError, match="already registered"):
            reg.register("a", NullRbacProvider)

    def test_empty_name(self):
        with pytest.raises(RegistryError, match="empty"):
            RbacRegistry().register("", NullRbacProvider)

    def test_override(self):
        reg = RbacRegistry()
        reg.register("a", NullRbacProvider)
        reg.register("a", InMemoryRbacProvider, override=True)
        assert isinstance(reg.create("a"), InMemoryRbacProvider)

    def test_unregister(self):
        reg = RbacRegistry()
        reg.register("a", NullRbacProvider)
        assert reg.unregister("a") is True
        assert reg.unregister("a") is False

    def test_decorator(self):
        @register_rbac_provider("test_rbac_prov")
        class Custom:
            def create_role(self, role): return role
            def get_role(self, name): return None
            def list_roles(self): return []
            def update_role(self, name, role): return None
            def delete_role(self, name): return False
            def assign_role(self, u, r): pass
            def revoke_role(self, u, r): return False
            def get_user_roles(self, u): return []
            def get_assigned_users(self, r): return []
            def get_stats(self):
                from agentbase.core.rbac import RbacStats
                return RbacStats()
            def close(self): pass

        from agentbase.core.rbac import rbac_registry

        assert rbac_registry.has("test_rbac_prov")
        rbac_registry.unregister("test_rbac_prov")


# ---------------------------------------------------------------------------
# Manager — validation
# ---------------------------------------------------------------------------


class TestManagerValidation:
    def test_disabled_defaults_to_null(self):
        mgr = RbacManager()
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullRbacProvider)

    def test_invalid_role_name(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="required"):
            mgr.create_role("", permissions=["a:b"])
        with pytest.raises(RegistryError, match="too long"):
            mgr.create_role("r" * 65, permissions=["a:b"])
        with pytest.raises(RegistryError, match="Invalid role name"):
            mgr.create_role("bad name!", permissions=["a:b"])

    def test_empty_permissions(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="at least one permission"):
            mgr.create_role("editor", permissions=[])

    def test_invalid_permission_string(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="Invalid permission"):
            mgr.create_role("editor", permissions=["garbage"])

    def test_description_too_long(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="description too long"):
            mgr.create_role("editor", permissions=["a:b"], description="d" * 501)

    def test_role_name_normalized(self):
        mgr = RbacManager(provider="memory", enabled=True)
        role = mgr.create_role("  Editor  ", permissions=["agents:invoke"])
        assert role.name == "editor"


# ---------------------------------------------------------------------------
# Manager — role CRUD & assignment
# ---------------------------------------------------------------------------


class TestManagerCrud:
    def test_create_get_list(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["agents:invoke", "kb:write"])
        role = mgr.get_role("editor")
        assert role is not None
        assert role.permissions == ["agents:invoke", "kb:write"]  # sorted
        names = [r.name for r in mgr.list_roles()]
        assert "editor" in names
        assert "admin" in names

    def test_permissions_deduplicated_and_legacy_normalized(self):
        mgr = RbacManager(provider="memory", enabled=True)
        role = mgr.create_role("r1", permissions=["read", "read", "agents:invoke"])
        assert role.permissions == ["*:read", "agents:invoke"]

    def test_update_role(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["a:b"])
        updated = mgr.update_role("editor", permissions=["c:d"], description="new")
        assert updated is not None
        assert updated.permissions == ["c:d"]
        assert updated.description == "new"

    def test_update_missing_returns_none(self):
        mgr = RbacManager(provider="memory", enabled=True)
        assert mgr.update_role("nope", permissions=["a:b"]) is None

    def test_update_rejects_empty_permissions(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["a:b"])
        with pytest.raises(RegistryError, match="at least one permission"):
            mgr.update_role("editor", permissions=[])

    def test_delete_protection(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="system role"):
            mgr.delete_role("admin")
        mgr.create_role("editor", permissions=["a:b"])
        assert mgr.delete_role("editor") is True
        assert mgr.delete_role("editor") is False

    def test_assign_revoke(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["agents:invoke"])
        mgr.assign_role("alice", "editor")
        assert mgr.get_user_roles("alice") == ["editor"]
        assert mgr.get_assigned_users("editor") == ["alice"]
        assert mgr.revoke_role("alice", "editor") is True
        assert mgr.get_user_roles("alice") == []

    def test_assign_unknown_role(self):
        mgr = RbacManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="Unknown role"):
            mgr.assign_role("alice", "nope")

    def test_get_user_permissions_union(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("r1", permissions=["agents:invoke", "kb:write"])
        mgr.assign_role("alice", "r1")
        mgr.assign_role("alice", "readonly")  # system role: *:read
        perms = mgr.get_user_permissions("alice")
        assert perms == ["*:read", "agents:invoke", "kb:write"]

    def test_stats(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["a:b"])
        mgr.assign_role("alice", "editor")
        stats = mgr.get_stats()
        assert stats.custom_roles == 1
        assert stats.total_assignments == 1


# ---------------------------------------------------------------------------
# Manager — permission checks
# ---------------------------------------------------------------------------


class TestPermissionChecks:
    def test_exact_match(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["agents:invoke"])
        mgr.assign_role("alice", "editor")
        assert mgr.check_permission("alice", "agents", "invoke") is True
        assert mgr.check_permission("alice", "agents", "delete") is False

    def test_wildcard_full(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.assign_role("alice", "admin")
        assert mgr.check_permission("alice", "anything", "everything") is True

    def test_wildcard_partial(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("viewer", permissions=["agents:*"])
        mgr.assign_role("bob", "viewer")
        assert mgr.check_permission("bob", "agents", "read") is True
        assert mgr.check_permission("bob", "users", "read") is False

    def test_legacy_permission_grants_action(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("reader", permissions=["read"])  # → *:read
        mgr.assign_role("carol", "reader")
        assert mgr.check_permission("carol", "agents", "read") is True
        assert mgr.check_permission("carol", "agents", "write") is False

    def test_unknown_user_denied(self):
        mgr = RbacManager(provider="memory", enabled=True)
        assert mgr.check_permission("ghost", "agents", "read") is False

    def test_empty_resource_or_action_denied(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.assign_role("alice", "admin")
        assert mgr.check_permission("alice", "", "read") is False
        assert mgr.check_permission("alice", "agents", "") is False

    def test_disabled_denies_all(self):
        mgr = RbacManager()  # disabled
        assert mgr.check_permission("alice", "agents", "read") is False
        assert mgr.list_roles() == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_creates_disabled(self):
        reset_rbac_manager()
        mgr = get_rbac_manager()
        assert mgr.enabled is False

    def test_set_and_reset(self):
        mgr = RbacManager(provider="memory", enabled=True)
        set_rbac_manager(mgr)
        assert get_rbac_manager() is mgr
        reset_rbac_manager()
        assert get_rbac_manager() is not mgr
        reset_rbac_manager()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_parallel_assignments(self):
        mgr = RbacManager(provider="memory", enabled=True)
        mgr.create_role("editor", permissions=["a:b"])
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(20):
                    mgr.assign_role(f"u{n}.{i}", "editor")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(mgr.get_assigned_users("editor")) == 160


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_inmemory_satisfies_protocol(self):
        assert isinstance(InMemoryRbacProvider(), RbacProvider)

    def test_null_satisfies_protocol(self):
        assert isinstance(NullRbacProvider(), RbacProvider)
