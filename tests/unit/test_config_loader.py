from __future__ import annotations

import pytest

from agentbase.config.loader import _deep_merge, _read_yaml
from agentbase.runtime.errors import ConfigError


def test_deep_merge_simple():
    base = {"a": 1, "b": 2}
    overlay = {"b": 3, "c": 4}
    result = _deep_merge(base, overlay)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested():
    base = {"model": {"name": "gpt", "temp": 0}}
    overlay = {"model": {"temp": 1}}
    result = _deep_merge(base, overlay)
    assert result == {"model": {"name": "gpt", "temp": 1}}


def test_deep_merge_no_mutate():
    base = {"a": {"x": 1}}
    overlay = {"a": {"y": 2}}
    _deep_merge(base, overlay)
    assert base == {"a": {"x": 1}}


def test_read_yaml_not_found(tmp_path):
    with pytest.raises(ConfigError):
        _read_yaml(tmp_path / "nonexistent.yaml")


def test_read_yaml_valid(tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text("key: value\n", encoding="utf-8")
    data = _read_yaml(path)
    assert data == {"key": "value"}


def test_read_yaml_empty(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    data = _read_yaml(path)
    assert data == {}


def test_read_yaml_non_dict(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        _read_yaml(path)


# ---------------------------------------------------------------------------
# Supplementary tests for missing coverage
# ---------------------------------------------------------------------------


def test_load_profile_overlay_existing(tmp_path):
    from agentbase.config.loader import _load_profile_overlay

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.staging.yaml").write_text("model:\n  name: gpt-4o\n", encoding="utf-8")
    result = _load_profile_overlay(tmp_path, "staging")
    assert result["model"]["name"] == "gpt-4o"


def test_load_profile_overlay_nonexistent(tmp_path):
    from agentbase.config.loader import _load_profile_overlay

    result = _load_profile_overlay(tmp_path, "nonexistent")
    assert result == {}


def test_load_app_config_with_profile(tmp_path):
    from agentbase.config.loader import load_app_config
    from unittest.mock import patch

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    (configs / "default.staging.yaml").write_text(
        "model:\n  name: gpt-4o\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"AGENTBASE_PROFILE": "staging"}):
        cfg = load_app_config(tmp_path)
        assert cfg.model.name == "gpt-4o"


def test_settings_overlay_values(tmp_path):
    from agentbase.config.loader import _settings_overlay, load_app_config
    from agentbase.config.settings import EnvSettings
    from unittest.mock import patch

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {
        "AGENTBASE_APP__ENV": "prod",
        "AGENTBASE_MODEL__NAME": "claude-3",
        "AGENTBASE_STORAGE__TYPE": "sqlite",
    }):
        cfg = load_app_config(tmp_path)
        assert cfg.app.env == "prod"
        assert cfg.model.name == "claude-3"
        assert cfg.storage.type == "sqlite"


def test_load_app_config_env_fallback_api_key(tmp_path):
    from agentbase.config.loader import load_app_config
    from unittest.mock import patch

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        cfg = load_app_config(tmp_path)
        assert cfg.model.api_key_env == "OPENAI_API_KEY"


def test_load_app_config_env_fallback_base_url(tmp_path):
    from agentbase.config.loader import load_app_config
    from unittest.mock import patch

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://custom.api.com"}):
        cfg = load_app_config(tmp_path)
        assert cfg.model.base_url == "https://custom.api.com"


def test_load_app_config_checkpointer_dsn_fallback(tmp_path):
    from agentbase.config.loader import load_app_config
    from unittest.mock import patch

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\ncheckpointer:\n  type: memory\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"AGENTBASE_CHECKPOINTER__DSN": "postgresql://test"}):
        cfg = load_app_config(tmp_path)
        assert cfg.checkpointer.dsn == "postgresql://test"


def test_load_agent_config_invalid(tmp_path):
    from agentbase.config.loader import load_agent_config

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    # Invalid agent config: temperature is string
    (agents_dir / "bad.yaml").write_text(
        "name: bad\nmodel:\n  temperature: not_a_number\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Invalid agent config"):
        load_agent_config(tmp_path, "bad")


def test_validate_config_dir_success(tmp_path):
    from agentbase.config.loader import validate_config_dir

    configs = tmp_path / "configs"
    agents = configs / "agents"
    workspace = tmp_path / "workspace"
    agents.mkdir(parents=True)
    workspace.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    (agents / "helper.yaml").write_text("name: helper\n", encoding="utf-8")

    result = validate_config_dir(tmp_path)
    assert result["valid"] is True
    assert len(result["errors"]) == 0
    assert "helper" in result["agents"]
    assert result["app_config"] is not None


def test_validate_config_dir_missing_default_yaml(tmp_path):
    from agentbase.config.loader import validate_config_dir

    result = validate_config_dir(tmp_path)
    assert result["valid"] is False
    assert any("Missing config" in e for e in result["errors"])


def test_validate_config_dir_no_agents_dir(tmp_path):
    from agentbase.config.loader import validate_config_dir

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )

    result = validate_config_dir(tmp_path)
    assert result["valid"] is True
    assert any("agents directory" in w for w in result["warnings"])


def test_validate_config_dir_no_agent_files(tmp_path):
    from agentbase.config.loader import validate_config_dir

    configs = tmp_path / "configs"
    agents = configs / "agents"
    agents.mkdir(parents=True)
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )

    result = validate_config_dir(tmp_path)
    assert result["valid"] is True
    assert any("No agent configs" in w for w in result["warnings"])


def test_validate_config_dir_no_workspace(tmp_path):
    from agentbase.config.loader import validate_config_dir

    configs = tmp_path / "configs"
    agents = configs / "agents"
    agents.mkdir(parents=True)
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    (agents / "helper.yaml").write_text("name: helper\n", encoding="utf-8")

    result = validate_config_dir(tmp_path)
    assert any("Workspace" in w for w in result["warnings"])


def test_validate_config_dir_agent_error(tmp_path):
    from agentbase.config.loader import validate_config_dir

    configs = tmp_path / "configs"
    agents = configs / "agents"
    agents.mkdir(parents=True)
    (configs / "default.yaml").write_text(
        "app:\n  name: test\nmodel:\n  provider: openai\n  name: gpt-4o-mini\n",
        encoding="utf-8",
    )
    # Invalid agent config
    (agents / "bad.yaml").write_text(
        "name: bad\nmodel:\n  temperature: not_a_number\n",
        encoding="utf-8",
    )

    result = validate_config_dir(tmp_path)
    assert result["valid"] is False
    assert any("bad" in e for e in result["errors"])