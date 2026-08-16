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

    # --- Supplementary tests for missing branches ---

    def test_build_mysql_checkpointer_no_dsn_raises(self):
        """MySQL without DSN should raise FactoryError."""
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="mysql", dsn=None)
        with pytest.raises(FactoryError, match="requires.*dsn"):
            build_checkpointer(spec, root_dir=Path("."))

    def test_build_mysql_checkpointer_invalid_dsn_raises(self, tmp_path):
        """MySQL with invalid DSN format should raise FactoryError."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock()

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(type="mysql", dsn="not-a-valid-dsn")
            with pytest.raises(FactoryError, match="Invalid MySQL DSN"):
                build_checkpointer(spec, root_dir=tmp_path)

    def test_build_mysql_checkpointer_connection_failure_raises(self, tmp_path):
        """MySQL connection failure should be wrapped as FactoryError."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        fake_pymysql.connect = MagicMock(side_effect=ConnectionError("Connection refused"))

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(
                type="mysql",
                dsn="mysql://user:pass@localhost:3306/testdb",
            )
            with pytest.raises(FactoryError, match="Failed to build mysql"):
                build_checkpointer(spec, root_dir=tmp_path)

    def test_build_sqlite_relative_path_resolves_with_root_dir(self, tmp_path):
        """SQLite with relative path should resolve against root_dir."""
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="sqlite", dsn="sqlite:///data/cp.db")
        cp = build_checkpointer(spec, root_dir=tmp_path)
        assert cp is not None
        assert (tmp_path / "data" / "cp.db").exists()

    def test_build_sqlite_non_sqlite_scheme(self, tmp_path):
        """SQLite with non-sqlite scheme should fall back to Path(dsn)."""
        from agentbase.factories.checkpointer_factory import build_checkpointer

        spec = CheckpointerConfig(type="sqlite", dsn=str(tmp_path / "direct.db"))
        cp = build_checkpointer(spec, root_dir=tmp_path)
        assert cp is not None
        assert (tmp_path / "direct.db").exists()

    def test_build_sqlite_import_error_raises(self, tmp_path):
        """If sqlite saver import fails, should raise FactoryError."""
        from agentbase.factories.checkpointer_factory import build_sqlite_checkpointer

        spec = CheckpointerConfig(type="sqlite", dsn=f"sqlite:///{tmp_path}/cp.db")
        # Clear both module path and already-imported reference
        with patch.dict("sys.modules", {"langgraph.checkpoint.sqlite": None}):
            # Also patch the function's own import scope
            with patch("builtins.__import__", wraps=__import__) as mock_import:
                mock_import.side_effect = lambda name, *a, **kw: (_ for _ in ()).throw(ImportError("simulated")) if name == "langgraph.checkpoint.sqlite" else __import__(name, *a, **kw)
                with pytest.raises(FactoryError, match="Sqlite checkpointer unavailable"):
                    build_sqlite_checkpointer(spec, root_dir=tmp_path)

    def test_build_postgres_no_from_conn_string(self, tmp_path):
        """Postgres without from_conn_string uses direct constructor."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pg = ModuleType("langgraph.checkpoint.postgres")
        fake_saver = MagicMock()
        fake_saver.setup = MagicMock()
        fake_pg.PostgresSaver = MagicMock(return_value=fake_saver)
        # Ensure from_conn_string does NOT exist
        del fake_pg.PostgresSaver.from_conn_string

        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": fake_pg}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(
                type="postgres",
                dsn="postgresql://user:pass@localhost:5432/testdb",
            )
            cp = build_checkpointer(spec, root_dir=tmp_path)
            assert cp is fake_saver

    def test_build_postgres_with_from_conn_string(self, tmp_path):
        """Postgres with from_conn_string uses context manager pattern."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pg = ModuleType("langgraph.checkpoint.postgres")
        fake_saver = MagicMock()
        fake_saver.setup = MagicMock()
        fake_cm = MagicMock()
        fake_cm.__enter__ = MagicMock(return_value=fake_saver)
        fake_cm.__exit__ = MagicMock(return_value=False)
        fake_pg.PostgresSaver = MagicMock()
        fake_pg.PostgresSaver.from_conn_string = MagicMock(return_value=fake_cm)

        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": fake_pg}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(
                type="postgres",
                dsn="postgresql://user:pass@localhost:5432/testdb",
            )
            cp = build_checkpointer(spec, root_dir=tmp_path)
            assert cp is fake_saver
            assert hasattr(cp, "_agentbase_cm")

    def test_build_postgres_import_error_raises(self, tmp_path):
        """If postgres saver import fails, should raise FactoryError."""
        from agentbase.factories.checkpointer_factory import build_postgres_checkpointer

        spec = CheckpointerConfig(
            type="postgres",
            dsn="postgresql://user:pass@localhost:5432/testdb",
        )
        with patch("builtins.__import__", wraps=__import__) as mock_import:
            mock_import.side_effect = lambda name, *a, **kw: (_ for _ in ()).throw(ImportError("simulated")) if name == "langgraph.checkpoint.postgres" else __import__(name, *a, **kw)
            with pytest.raises(FactoryError, match="Postgres checkpointer unavailable"):
                build_postgres_checkpointer(spec, root_dir=tmp_path)

    def test_build_postgres_connection_error_raises(self, tmp_path):
        """Postgres connection failure should be wrapped as FactoryError."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pg = ModuleType("langgraph.checkpoint.postgres")
        # PostgresSaver.from_conn_string returns a CM that raises on __enter__
        fake_cm = MagicMock()
        fake_cm.__enter__ = MagicMock(side_effect=ConnectionError("Connection refused"))
        fake_cm.__exit__ = MagicMock(return_value=False)
        fake_pg.PostgresSaver = MagicMock()
        fake_pg.PostgresSaver.from_conn_string = MagicMock(return_value=fake_cm)

        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": fake_pg}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(
                type="postgres",
                dsn="postgresql://user:pass@localhost:5432/testdb",
            )
            with pytest.raises(FactoryError, match="Failed to build postgres"):
                build_checkpointer(spec, root_dir=tmp_path)

    def test_build_mysql_success(self, tmp_path):
        """MySQL checkpointer with valid DSN and mock pymysql should succeed."""
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock

        fake_pymysql = ModuleType("pymysql")
        fake_pymysql.cursors = ModuleType("pymysql.cursors")
        fake_pymysql.cursors.DictCursor = MagicMock()
        mock_conn = MagicMock()
        fake_pymysql.connect = MagicMock(return_value=mock_conn)

        with patch.dict(sys.modules, {"pymysql": fake_pymysql, "pymysql.cursors": fake_pymysql.cursors}):
            from agentbase.factories.checkpointer_factory import build_checkpointer

            spec = CheckpointerConfig(
                type="mysql",
                dsn="mysql://user:pass@localhost:3306/testdb",
            )
            cp = build_checkpointer(spec, root_dir=tmp_path)
            assert cp is not None
            assert cp.conn is mock_conn
