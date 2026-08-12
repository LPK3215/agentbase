from __future__ import annotations

from agentbase.registry.middleware import middleware_registry
from agentbase.registry.subagents import subagent_registry
from agentbase.registry.tools import tool_registry

EXPECTED_TOOLS = {"echo", "get_time", "list_workspace", "read_file", "write_file", "grep", "now_local"}
EXPECTED_MIDDLEWARE = {"request_logger", "retry", "timeout"}
EXPECTED_SUBAGENTS = {"general_helper", "researcher"}


def test_all_tools_have_meta(bootstrapped):
    for name in EXPECTED_TOOLS:
        assert tool_registry.has(name), f"Tool not registered: {name}"
        meta = tool_registry.get_meta(name)
        assert meta is not None, f"No meta for tool: {name}"
        assert len(meta.description) <= 80, f"Description too long for {name}: {len(meta.description)}"


def test_all_middleware_have_meta(bootstrapped):
    for name in EXPECTED_MIDDLEWARE:
        assert middleware_registry.has(name), f"Middleware not registered: {name}"
        meta = middleware_registry.get_meta(name)
        assert meta is not None, f"No meta for middleware: {name}"
        assert len(meta.description) <= 80


def test_all_subagents_have_meta(bootstrapped):
    for name in EXPECTED_SUBAGENTS:
        assert subagent_registry.has(name), f"Subagent not registered: {name}"
        meta = subagent_registry.get_meta(name)
        assert meta is not None, f"No meta for subagent: {name}"
        assert len(meta.description) <= 80


def test_meta_kinds(bootstrapped):
    for name in EXPECTED_TOOLS:
        assert tool_registry.get_meta(name).kind == "tool"
    for name in EXPECTED_MIDDLEWARE:
        assert middleware_registry.get_meta(name).kind == "middleware"
    for name in EXPECTED_SUBAGENTS:
        assert subagent_registry.get_meta(name).kind == "subagent"