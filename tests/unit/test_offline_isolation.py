from __future__ import annotations

from agentbase.config.schema import ExtensionsConfig
from agentbase.registry.bootstrap import bootstrap_registries


def test_offline_isolation(isolated_env):
    ext_config = ExtensionsConfig()
    bootstrap_registries(ext_config, force=True)
    from agentbase.registry.middleware import middleware_registry
    from agentbase.registry.subagents import subagent_registry
    from agentbase.registry.tools import tool_registry
    assert len(tool_registry.names()) > 0
    assert len(middleware_registry.names()) > 0
    assert len(subagent_registry.names()) > 0