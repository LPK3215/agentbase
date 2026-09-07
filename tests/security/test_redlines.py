"""Defensive redline tests (R1 / R5). No exploit payloads."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agentbase.api import _ensure_prod_auth
from agentbase.config.schema import AppConfig
from agentbase.extensions.tools._workspace import resolve_within_workspace
from agentbase.extensions.tools.db_query import _validate_query
from agentbase.runtime.errors import ConfigError


def test_workspace_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        resolve_within_workspace(tmp_path, "../../etc/passwd")


def test_workspace_relative_ok(tmp_path):
    resolved = resolve_within_workspace(tmp_path, "notes/hello.txt")
    assert resolved == (tmp_path / "notes" / "hello.txt").resolve()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET name='x'",
        "DROP TABLE users",
        "SELECT 1; DROP TABLE users",
    ],
)
def test_db_query_rejects_non_select(sql):
    assert _validate_query(sql) is not None


def test_db_query_allows_select():
    assert _validate_query("SELECT 1") is None


def test_prod_env_refuses_unauthenticated_start():
    cfg = AppConfig()
    cfg.app.env = "prod"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        with pytest.raises(ConfigError) as exc_info:
            _ensure_prod_auth(cfg)
    assert exc_info.value.code == "AGENTBASE_CONFIG_004"


def test_production_alias_refuses_unauthenticated_start():
    cfg = AppConfig()
    cfg.app.env = "production"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        with pytest.raises(ConfigError) as exc_info:
            _ensure_prod_auth(cfg)
    assert exc_info.value.code == "AGENTBASE_CONFIG_004"


def test_dev_env_allows_open_api():
    cfg = AppConfig()
    assert cfg.app.env == "dev"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        _ensure_prod_auth(cfg)


def test_prod_with_api_key_starts():
    cfg = AppConfig()
    cfg.app.env = "prod"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": "not-a-real-secret"}, clear=False):
        _ensure_prod_auth(cfg)


def test_prod_jwt_with_secret_starts():
    cfg = AppConfig()
    cfg.app.env = "prod"
    cfg.auth.type = "jwt"
    cfg.auth.secret = "explicit-strong-secret-value"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        _ensure_prod_auth(cfg)


def test_jwt_prod_empty_secret_fail_fast():
    cfg = AppConfig()
    cfg.app.env = "prod"
    cfg.auth.type = "jwt"
    cfg.auth.secret = ""
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        with pytest.raises(ConfigError) as exc_info:
            _ensure_prod_auth(cfg)
    assert exc_info.value.code == "AGENTBASE_CONFIG_002"


def test_uvicorn_lifespan_refuses_prod_without_key():
    """Docker CMD uses uvicorn agentbase.api:app; startup must fail closed."""
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from agentbase.api import create_app, reset_runtime

    reset_runtime()
    cfg = AppConfig()
    cfg.app.env = "prod"
    rt = MagicMock()
    rt.app_config = cfg
    try:
        with patch("agentbase.api.build_runtime", return_value=rt):
            with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
                app = create_app()
                with pytest.raises(ConfigError) as exc_info:
                    with TestClient(app):
                        pass
        assert exc_info.value.code == "AGENTBASE_CONFIG_004"
    finally:
        reset_runtime()


def test_prod_with_config_api_key_starts():
    """YAML auth.api_key counts as production authentication (overrides empty env)."""
    from agentbase.api import _is_auth_enabled

    cfg = AppConfig()
    cfg.app.env = "prod"
    cfg.auth.api_key = "from-yaml-not-a-real-secret"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        _ensure_prod_auth(cfg)
        assert _is_auth_enabled(cfg) is True


def test_config_api_key_overrides_env():
    from agentbase.api import _get_api_key

    cfg = AppConfig()
    cfg.auth.api_key = "yaml-key"
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": "env-key"}, clear=False):
        assert _get_api_key(cfg) == "yaml-key"


def test_dev_without_keys_is_fail_open():
    from agentbase.api import _is_auth_enabled

    cfg = AppConfig()
    with patch.dict("os.environ", {"AGENTBASE_API_KEY": ""}, clear=False):
        assert _is_auth_enabled(cfg) is False
        _ensure_prod_auth(cfg)


def test_must_pass_redlines_have_tests():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent / "redlines.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [
        item["id"]
        for item in data.get("redlines", [])
        if item.get("must_pass") and not item.get("tests")
    ]
    assert missing == [], f"must_pass redlines with no tests: {missing}"


def test_interrupt_demo_gates_write_file():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "configs" / "agents" / "interrupt_demo.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    interrupt = data.get("interrupt_on") or {}
    blob = yaml.safe_dump(interrupt)
    assert "write_file" in blob


def test_readonly_profile_has_no_write_or_delete_tools():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "configs" / "agents" / "readonly.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tools = list(data.get("tools") or [])
    forbidden = {
        "write_file",
        "skill_delete",
        "memory_delete",
        "kb_delete",
        "skill_create",
        "skill_update",
        "memory_save",
        "memory_batch_save",
        "kb_ingest",
        "kb_update",
        "kb_delete",
    }
    overlap = forbidden.intersection(tools)
    assert not overlap, f"readonly profile includes mutating tools: {overlap}"
    assert not any(t.endswith("_delete") or t.startswith("write_") for t in tools)
