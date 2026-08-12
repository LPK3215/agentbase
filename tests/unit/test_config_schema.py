from __future__ import annotations

from agentbase.config.schema import (
    AgentConfig,
    AppConfig,
    CheckpointerConfig,
    RuntimeConfig,
)


def test_checkpointer_default_is_sqlite():
    cfg = CheckpointerConfig()
    assert cfg.type == "sqlite"


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.checkpointer.type == "sqlite"
    assert cfg.runtime.max_concurrency == 4
    assert cfg.runtime.default_agent == "default"


def test_runtime_config_defaults():
    cfg = RuntimeConfig()
    assert cfg.recursion_limit == 50
    assert cfg.max_concurrency == 4
    assert cfg.stream_modes == ["messages", "updates"]


def test_agent_config_ensure_list_string():
    cfg = AgentConfig(name="test", tools="echo")
    assert cfg.tools == ["echo"]


def test_agent_config_ensure_list_none():
    cfg = AgentConfig(name="test", tools=None)
    assert cfg.tools == []


def test_agent_config_ensure_list_list():
    cfg = AgentConfig(name="test", tools=["echo", "get_time"])
    assert cfg.tools == ["echo", "get_time"]