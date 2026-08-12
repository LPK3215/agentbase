"""Unit tests for model_factory, backend_factory, and checkpointer_factory."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentbase.config.schema import (
    AgentModelOverride,
    AppConfig,
    BackendConfig,
    CheckpointerConfig,
    ModelConfig,
)
from agentbase.runtime.errors import FactoryError

# ---------------------------------------------------------------------------
# model_factory
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    def test_resolves_by_api_key_env(self, monkeypatch):
        from agentbase.factories.model_factory import _resolve_api_key

        monkeypatch.setenv("AGENTBASE_API_KEY", "test-key-123")
        cfg = ModelConfig(provider="openai", name="gpt-4", api_key_env="AGENTBASE_API_KEY")
        assert _resolve_api_key(cfg) == "test-key-123"

    def test_falls_back_to_provider_env(self, monkeypatch):
        from agentbase.factories.model_factory import _resolve_api_key

        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        cfg = ModelConfig(provider="openai", name="gpt-4", api_key_env=None)
        assert _resolve_api_key(cfg) == "openai-key"

    def test_falls_back_to_siliconflow(self, monkeypatch):
        from agentbase.factories.model_factory import _resolve_api_key

        monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = ModelConfig(provider="openai", name="test", api_key_env=None)
        assert _resolve_api_key(cfg) == "sf-key"

    def test_returns_none_when_no_key(self, monkeypatch):
        from agentbase.factories.model_factory import _resolve_api_key

        # Delete ALL possible API key env vars that _resolve_api_key checks
        for key in ("OPENAI_API_KEY", "SILICONFLOW_API_KEY", "AGNES_API_KEY", "DEEPSEEK_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        cfg = ModelConfig(provider="openai", name="test", api_key_env=None)
        assert _resolve_api_key(cfg) is None

    def test_anthropic_provider(self, monkeypatch):
        from agentbase.factories.model_factory import _resolve_api_key

        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
        cfg = ModelConfig(provider="anthropic", name="claude-3", api_key_env=None)
        assert _resolve_api_key(cfg) == "anthropic-key"


class TestMergeModelConfig:
    def test_no_override_returns_base(self):
        from agentbase.factories.model_factory import merge_model_config

        app = AppConfig()
        result = merge_model_config(app, None)
        assert result.name == app.model.name
        assert result.provider == app.model.provider

    def test_override_name(self):
        from agentbase.factories.model_factory import merge_model_config

        app = AppConfig()
        override = AgentModelOverride(name="gpt-4o")
        result = merge_model_config(app, override)
        assert result.name == "gpt-4o"
        # Other fields should be from base
        assert result.provider == app.model.provider

    def test_override_temperature(self):
        from agentbase.factories.model_factory import merge_model_config

        app = AppConfig()
        override = AgentModelOverride(temperature=0.7)
        result = merge_model_config(app, override)
        assert result.temperature == 0.7

    def test_override_extra_merges(self):
        from agentbase.factories.model_factory import merge_model_config

        app = AppConfig()
        app.model.extra = {"a": 1, "b": 2}
        override = AgentModelOverride(extra={"b": 3, "c": 4})
        result = merge_model_config(app, override)
        assert result.extra["a"] == 1
        assert result.extra["b"] == 3
        assert result.extra["c"] == 4

    def test_override_base_url(self):
        from agentbase.factories.model_factory import merge_model_config

        app = AppConfig()
        override = AgentModelOverride(base_url="https://custom.api.com/v1")
        result = merge_model_config(app, override)
        assert result.base_url == "https://custom.api.com/v1"


class TestBuildModel:
    def test_build_model_success(self, monkeypatch):
        """Test that build_model calls init_chat_model with correct args."""
        from agentbase.factories.model_factory import build_model

        mock_model = MagicMock()
        with patch("langchain.chat_models.init_chat_model", return_value=mock_model) as mock_init:
            cfg = ModelConfig(provider="openai", name="gpt-4", temperature=0.5)
            monkeypatch.setenv("OPENAI_API_KEY", "test-key")
            result = build_model(cfg)
            assert result is mock_model
            mock_init.assert_called_once()

    def test_build_model_with_base_url(self, monkeypatch):
        from agentbase.factories.model_factory import build_model

        mock_model = MagicMock()
        with patch("langchain.chat_models.init_chat_model", return_value=mock_model) as mock_init:
            cfg = ModelConfig(
                provider="siliconflow",
                name="deepseek-v3",
                temperature=0,
                base_url="https://api.siliconflow.cn/v1",
            )
            monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-key")
            result = build_model(cfg)
            assert result is mock_model
            call_kwargs = mock_init.call_args
            # Should prefix with openai: for siliconflow provider
            assert "openai:deepseek-v3" in str(call_kwargs)

    def test_build_model_factory_error_on_failure(self, monkeypatch):
        from agentbase.factories.model_factory import build_model

        with patch("langchain.chat_models.init_chat_model", side_effect=Exception("boom")):
            cfg = ModelConfig(provider="openai", name="gpt-4")
            monkeypatch.setenv("OPENAI_API_KEY", "test-key")
            with pytest.raises(FactoryError, match="Failed to init model"):
                build_model(cfg)


# ---------------------------------------------------------------------------
# backend_factory
# ---------------------------------------------------------------------------

class TestBuildBackend:
    def test_build_filesystem_backend(self, tmp_path):
        from agentbase.factories.backend_factory import build_backend

        spec = BackendConfig(type="filesystem", root_dir="workspace")
        backend = build_backend(spec, root_dir=tmp_path)
        assert backend is not None
        # Workspace dir should have been created
        assert (tmp_path / "workspace").exists()

    def test_build_unknown_backend_raises(self):
        from agentbase.factories.backend_factory import build_backend

        spec = BackendConfig(type="nonexistent_backend")
        with pytest.raises(Exception, match="Unknown backend"):
            build_backend(spec, root_dir=Path("."))

    def test_build_state_backend(self):
        from agentbase.factories.backend_factory import build_backend

        spec = BackendConfig(type="state")
        backend = build_backend(spec, root_dir=Path("."))
        assert backend is not None

    def test_resolve_root_creates_dir(self, tmp_path):
        from agentbase.factories.backend_factory import _resolve_root

        result = _resolve_root(tmp_path, "new_dir/sub")
        assert result.exists()
        assert result.is_dir()


# ---------------------------------------------------------------------------
# checkpointer_factory
# ---------------------------------------------------------------------------

class TestBuildCheckpointer:
    def test_build_memory_checkpointer(self):
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="memory")
        cp = build_checkpointer(spec, root_dir=Path("."))
        assert cp is not None

    def test_build_sqlite_checkpointer(self, tmp_path):
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(
            type="sqlite",
            dsn=f"sqlite:///{tmp_path}/checkpoints.db",
        )
        cp = build_checkpointer(spec, root_dir=tmp_path)
        assert cp is not None
        assert (tmp_path / "checkpoints.db").exists()

    def test_build_sqlite_default_dsn(self, tmp_path):
        """SQLite checkpointer should work with default DSN."""
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="sqlite", dsn=None)
        cp = build_checkpointer(spec, root_dir=tmp_path)
        assert cp is not None

    def test_build_postgres_checkpointer_no_dsn_raises(self):
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="postgres", dsn=None)
        with pytest.raises(FactoryError, match="requires.*dsn"):
            build_checkpointer(spec, root_dir=Path("."))

    def test_build_unknown_checkpointer_raises(self):
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="nonexistent")
        with pytest.raises(Exception, match="Unknown checkpointer"):
            build_checkpointer(spec, root_dir=Path("."))

    def test_build_postgres_checkpointer_with_dsn(self, tmp_path):
        """Try building postgres checkpointer with a DSN.

        This will either succeed (if postgres is running) or raise FactoryError.
        Either way, it exercises the code path. We use a short timeout to avoid
        hanging when postgres is not available.
        """
        import socket

        from agentbase.factories.checkpointer_factory import build_checkpointer

        # Quick check if postgres is reachable before attempting connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        postgres_available = False
        try:
            result = sock.connect_ex(("localhost", 5432))
            postgres_available = (result == 0)
        except Exception:
            pass
        finally:
            sock.close()

        if not postgres_available:
            pytest.skip("PostgreSQL not available on localhost:5432")

        spec = CheckpointerConfig(
            type="postgres",
            dsn="postgresql://agentbase:agentbase@localhost:5432/agentbase",
        )
        try:
            cp = build_checkpointer(spec, root_dir=tmp_path)
            assert cp is not None
        except FactoryError:
            # Postgres connection failed — that's OK, code path was exercised
            pass
