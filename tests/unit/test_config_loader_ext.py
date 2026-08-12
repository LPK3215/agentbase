"""Unit tests for config loader functions (load_app_config, load_agent_config, list_agent_names)."""
from __future__ import annotations

import pytest

from agentbase.config.loader import (
    _deep_merge,
    list_agent_names,
    load_agent_config,
    load_app_config,
)
from agentbase.runtime.errors import ConfigError


class TestLoadAppConfig:
    def test_load_valid_config(self, tmp_path, isolated_env):
        """Load a minimal valid app config."""
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\n  env: dev\n  log_level: INFO\n",
            encoding="utf-8",
        )
        config = load_app_config(tmp_path)
        assert config.app.name == "test"
        assert config.app.env == "dev"

    def test_load_full_config(self, tmp_path, isolated_env):
        """Load a config with all sections."""
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            """
app:
  name: full_test
  env: test
  log_level: DEBUG

model:
  provider: openai
  name: gpt-4
  temperature: 0.5
  base_url: https://api.example.com/v1
  api_key_env: TEST_API_KEY

storage:
  type: sqlite
  db_dir: data

embedding:
  provider: hash

web_search:
  provider: duckduckgo

runtime:
  default_agent: default
  workspace_dir: workspace
""",
            encoding="utf-8",
        )
        config = load_app_config(tmp_path)
        assert config.app.name == "full_test"
        assert config.model.name == "gpt-4"
        assert config.storage.type == "sqlite"
        assert config.embedding.provider == "hash"
        assert config.web_search.provider == "duckduckgo"

    def test_load_redaction_config(self, tmp_path, isolated_env):
        """Load config with the redaction section."""
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\n  env: dev\n"
            "redaction:\n  enabled: true\n  provider: regex\n",
            encoding="utf-8",
        )
        config = load_app_config(tmp_path)
        assert config.redaction.enabled is True
        assert config.redaction.provider == "regex"

    def test_load_redaction_defaults(self, tmp_path, isolated_env):
        """Redaction section should default to disabled/regex."""
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "app:\n  name: test\n  env: dev\n",
            encoding="utf-8",
        )
        config = load_app_config(tmp_path)
        assert config.redaction.enabled is False
        assert config.redaction.provider == "regex"

    def test_load_missing_config_raises(self, tmp_path):
        """Should raise ConfigError when default.yaml doesn't exist."""
        with pytest.raises(ConfigError, match="not found"):
            load_app_config(tmp_path)

    def test_load_invalid_config_raises(self, tmp_path):
        """Should raise ConfigError for invalid config."""
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "default.yaml").write_text(
            "model:\n  temperature: not_a_number\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="Invalid"):
            load_app_config(tmp_path)


class TestLoadAgentConfig:
    def test_load_valid_agent(self, tmp_path):
        """Load a valid agent config."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "test_agent.yaml").write_text(
            """
name: test_agent
description: Test agent
system_prompt: "You are a test agent."
tools:
  - echo
  - get_time
""",
            encoding="utf-8",
        )
        config = load_agent_config(configs, "test_agent")
        assert config.name == "test_agent"
        assert config.description == "Test agent"
        assert "echo" in config.tools
        assert "get_time" in config.tools

    def test_load_agent_missing_name_auto_fills(self, tmp_path):
        """Agent config without name field should auto-fill from filename."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "auto_name.yaml").write_text(
            'description: Auto named\n',
            encoding="utf-8",
        )
        config = load_agent_config(configs, "auto_name")
        assert config.name == "auto_name"

    def test_load_agent_not_found(self, tmp_path):
        """Should raise ConfigError when agent config doesn't exist."""
        configs = tmp_path / "configs"
        with pytest.raises(ConfigError, match="not found"):
            load_agent_config(configs, "nonexistent")

    def test_load_agent_tools_as_string(self, tmp_path):
        """Agent tools field should accept a single string."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "single_tool.yaml").write_text(
            """
name: single_tool
tools: echo
""",
            encoding="utf-8",
        )
        config = load_agent_config(configs, "single_tool")
        assert config.tools == ["echo"]

    def test_load_agent_with_permissions(self, tmp_path):
        """Load agent with permission rules."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "restricted.yaml").write_text(
            """
name: restricted
permissions:
  - operations: [read, write]
    paths: ["workspace/**"]
    mode: allow
  - operations: [delete]
    paths: ["**/secrets/**"]
    mode: deny
""",
            encoding="utf-8",
        )
        config = load_agent_config(configs, "restricted")
        assert len(config.permissions) == 2
        assert config.permissions[0].operations == ["read", "write"]
        assert config.permissions[0].mode == "allow"
        assert config.permissions[1].mode == "deny"

    def test_load_agent_with_interrupt_on(self, tmp_path):
        """Load agent with interrupt configuration."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "interrupt.yaml").write_text(
            """
name: interrupt
interrupt_on:
  tool_call:
    - write_file
""",
            encoding="utf-8",
        )
        config = load_agent_config(configs, "interrupt")
        assert config.interrupt_on["tool_call"] == ["write_file"]


class TestListAgentNames:
    def test_list_multiple_agents(self, tmp_path):
        """List multiple agent configs."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "alpha.yaml").write_text("name: alpha\n", encoding="utf-8")
        (agents / "beta.yaml").write_text("name: beta\n", encoding="utf-8")
        (agents / "gamma.yaml").write_text("name: gamma\n", encoding="utf-8")
        names = list_agent_names(configs)
        assert names == ["alpha", "beta", "gamma"]

    def test_list_empty_dir(self, tmp_path):
        """List agents from empty directory."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        names = list_agent_names(configs)
        assert names == []

    def test_list_missing_dir(self, tmp_path):
        """List agents when agents directory doesn't exist."""
        configs = tmp_path / "configs"
        names = list_agent_names(configs)
        assert names == []

    def test_list_only_yaml_files(self, tmp_path):
        """Should only list .yaml files."""
        configs = tmp_path / "configs"
        agents = configs / "agents"
        agents.mkdir(parents=True)
        (agents / "real.yaml").write_text("name: real\n", encoding="utf-8")
        (agents / "not_yaml.txt").write_text("name: not_yaml\n", encoding="utf-8")
        (agents / "README.md").write_text("# Readme\n", encoding="utf-8")
        names = list_agent_names(configs)
        assert names == ["real"]


class TestDeepMerge:
    def test_deep_merge_preserves_base(self):
        base = {"a": 1, "b": {"x": 1, "y": 2}}
        overlay = {"b": {"y": 3}}
        result = _deep_merge(base, overlay)
        assert result == {"a": 1, "b": {"x": 1, "y": 3}}

    def test_deep_merge_replaces_non_dicts(self):
        base = {"a": [1, 2]}
        overlay = {"a": [3]}
        result = _deep_merge(base, overlay)
        assert result == {"a": [3]}

    def test_deep_merge_empty_overlay(self):
        base = {"a": 1}
        result = _deep_merge(base, {})
        assert result == {"a": 1}
