"""Unit tests for rbac_ops tools."""
from __future__ import annotations

import json

import pytest

from agentbase.core.rbac import RbacManager
from agentbase.extensions.tools import rbac_ops  # noqa: F401 — triggers registration
from agentbase.registry.tools import tool_registry


@pytest.fixture
def rbac_mgr():
    mgr = RbacManager(provider="memory", enabled=True)
    mgr.create_role("editor", permissions=["agents:invoke", "kb:write"])
    mgr.assign_role("alice", "editor")
    return mgr


@pytest.fixture
def ctx(rbac_mgr):
    return {"rbac_manager": rbac_mgr}


class TestRegistration:
    def test_both_tools_registered(self):
        assert tool_registry.has("rbac_check_permission")
        assert tool_registry.has("rbac_list_roles")

    def test_missing_context_raises(self):
        from agentbase.extensions.tools.rbac_ops import build_rbac_check_permission_tool

        with pytest.raises(RuntimeError, match="rbac_manager not available"):
            build_rbac_check_permission_tool(context={})


class TestCheckPermissionTool:
    def test_allowed(self, ctx):
        from agentbase.extensions.tools.rbac_ops import build_rbac_check_permission_tool

        tool_fn = build_rbac_check_permission_tool(context=ctx)
        data = json.loads(tool_fn.invoke({"username": "alice", "resource": "agents", "action": "invoke"}))
        assert data["allowed"] is True

    def test_denied(self, ctx):
        from agentbase.extensions.tools.rbac_ops import build_rbac_check_permission_tool

        tool_fn = build_rbac_check_permission_tool(context=ctx)
        data = json.loads(tool_fn.invoke({"username": "alice", "resource": "users", "action": "delete"}))
        assert data["allowed"] is False

    def test_unknown_user_denied(self, ctx):
        from agentbase.extensions.tools.rbac_ops import build_rbac_check_permission_tool

        tool_fn = build_rbac_check_permission_tool(context=ctx)
        data = json.loads(tool_fn.invoke({"username": "ghost", "resource": "agents", "action": "read"}))
        assert data["allowed"] is False


class TestListRolesTool:
    def test_list_all_includes_system(self, ctx):
        from agentbase.extensions.tools.rbac_ops import build_rbac_list_roles_tool

        tool_fn = build_rbac_list_roles_tool(context=ctx)
        roles = json.loads(tool_fn.invoke({}))
        names = [r["name"] for r in roles]
        assert names == ["admin", "editor", "readonly", "user"]

    def test_list_custom_only(self, ctx):
        from agentbase.extensions.tools.rbac_ops import build_rbac_list_roles_tool

        tool_fn = build_rbac_list_roles_tool(context=ctx)
        roles = json.loads(tool_fn.invoke({"include_system": False}))
        assert [r["name"] for r in roles] == ["editor"]

    def test_list_empty_when_disabled(self):
        mgr = RbacManager()  # disabled
        from agentbase.extensions.tools.rbac_ops import build_rbac_list_roles_tool

        tool_fn = build_rbac_list_roles_tool(context={"rbac_manager": mgr})
        assert json.loads(tool_fn.invoke({})) == []
