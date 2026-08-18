"""Unit tests for system_config_ops tools."""
from __future__ import annotations

import json

import pytest

from agentbase.core.system_config import SystemConfigManager
from agentbase.extensions.tools import system_config_ops  # noqa: F401 — triggers registration
from agentbase.registry.tools import tool_registry


@pytest.fixture
def cfg_mgr():
    return SystemConfigManager(provider="memory", enabled=True)


@pytest.fixture
def ctx(cfg_mgr):
    return {"system_config_manager": cfg_mgr}


class TestRegistration:
    def test_both_tools_registered(self):
        assert tool_registry.has("system_config_get")
        assert tool_registry.has("system_config_list")

    def test_default_disabled(self):
        names = ("system_config_get", "system_config_list")
        for name in names:
            meta = tool_registry.get_meta(name) if hasattr(tool_registry, "get_meta") else None
            if meta is not None:
                assert meta.default_enabled is False

    def test_missing_context_raises(self):
        from agentbase.extensions.tools.system_config_ops import (
            build_system_config_get_tool,
        )

        with pytest.raises(RuntimeError, match="system_config_manager not available"):
            build_system_config_get_tool(context={})


class TestGetTool:
    def test_get_existing(self, ctx, cfg_mgr):
        from agentbase.extensions.tools.system_config_ops import build_system_config_get_tool

        cfg_mgr.set("feature.dark_mode", True, category="feature")
        tool_fn = build_system_config_get_tool(context=ctx)
        data = json.loads(tool_fn.invoke({"key": "feature.dark_mode"}))
        assert data["key"] == "feature.dark_mode"
        assert data["value"] is True
        assert data["category"] == "feature"

    def test_get_missing(self, ctx):
        from agentbase.extensions.tools.system_config_ops import build_system_config_get_tool

        tool_fn = build_system_config_get_tool(context=ctx)
        result = tool_fn.invoke({"key": "missing.key"})
        assert result.startswith("Config key not found:")


class TestListTool:
    def test_list_all(self, ctx, cfg_mgr):
        from agentbase.extensions.tools.system_config_ops import build_system_config_list_tool

        cfg_mgr.set("feature.a", 1)
        cfg_mgr.set("quota.b", 2)
        tool_fn = build_system_config_list_tool(context=ctx)
        items = json.loads(tool_fn.invoke({}))
        assert [i["key"] for i in items] == ["feature.a", "quota.b"]

    def test_list_category_filter(self, ctx, cfg_mgr):
        from agentbase.extensions.tools.system_config_ops import build_system_config_list_tool

        cfg_mgr.set("feature.a", 1, category="feature")
        cfg_mgr.set("quota.b", 2, category="quota")
        tool_fn = build_system_config_list_tool(context=ctx)
        items = json.loads(tool_fn.invoke({"category": "quota"}))
        assert [i["key"] for i in items] == ["quota.b"]

    def test_list_prefix_filter_and_limit(self, ctx, cfg_mgr):
        from agentbase.extensions.tools.system_config_ops import build_system_config_list_tool

        for i in range(5):
            cfg_mgr.set(f"feature.k{i}", i)
        cfg_mgr.set("other.x", 0)
        tool_fn = build_system_config_list_tool(context=ctx)
        items = json.loads(tool_fn.invoke({"key_prefix": "feature.", "limit": 3}))
        assert [i["key"] for i in items] == ["feature.k0", "feature.k1", "feature.k2"]

    def test_list_empty(self, ctx):
        from agentbase.extensions.tools.system_config_ops import build_system_config_list_tool

        tool_fn = build_system_config_list_tool(context=ctx)
        assert json.loads(tool_fn.invoke({})) == []


class TestDisabledManager:
    def test_disabled_manager_reads_miss(self):
        mgr = SystemConfigManager()  # disabled
        from agentbase.extensions.tools.system_config_ops import build_system_config_get_tool

        tool_fn = build_system_config_get_tool(context={"system_config_manager": mgr})
        assert tool_fn.invoke({"key": "a.b"}).startswith("Config key not found:")
