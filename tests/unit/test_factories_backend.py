"""Unit tests for backend_factory — _resolve_root, build_state_backend,
build_filesystem_backend, build_store_backend, build_backend.

Tests cover:
- _resolve_root: absolute, relative, mkdir
- build_state_backend: success, import error
- build_filesystem_backend: success, import error, TypeError fallbacks
- build_store_backend: success, import error
- build_backend: dispatch
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agentbase.config.schema import BackendConfig
from agentbase.factories.backend_factory import (
    _resolve_root,
    build_backend,
    build_filesystem_backend,
    build_state_backend,
    build_store_backend,
)
from agentbase.runtime.errors import FactoryError


class TestResolveRoot:
    def test_absolute_path(self, tmp_path):
        p = tmp_path / "data"
        result = _resolve_root(tmp_path, str(p))
        assert result == p.resolve()
        assert p.exists()

    def test_relative_path(self, tmp_path):
        result = _resolve_root(tmp_path, "data/sub")
        assert result == (tmp_path / "data" / "sub").resolve()
        assert (tmp_path / "data" / "sub").exists()

    def test_existing_dir_not_error(self, tmp_path):
        p = tmp_path / "existing"
        p.mkdir()
        result = _resolve_root(tmp_path, "existing")
        assert result == p.resolve()


class TestBuildStateBackend:
    def test_success(self):
        fake_mod = ModuleType("deepagents.backends")
        fake_mod.StateBackend = MagicMock(return_value="state_backend_instance")
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="state")
            result = build_state_backend(spec, root_dir=Path("."))
            assert result == "state_backend_instance"

    def test_import_fallback_to_state_module(self):
        # First import fails, second succeeds
        call_count = [0]

        def fake_import(name, *args, **kwargs):
            call_count[0] += 1
            if name == "deepagents.backends":
                raise ImportError("not found")
            if name == "deepagents.backends.state":
                mod = ModuleType("deepagents.backends.state")
                mod.StateBackend = MagicMock(return_value="fallback_state")
                return mod
            raise ImportError("unexpected")

        with patch("builtins.__import__", side_effect=fake_import):
            spec = BackendConfig(type="state")
            result = build_state_backend(spec, root_dir=Path("."))
            assert result == "fallback_state"

    def test_import_error_raises(self):
        with patch("builtins.__import__", side_effect=ImportError("not available")):
            spec = BackendConfig(type="state")
            with pytest.raises(FactoryError, match="StateBackend unavailable"):
                build_state_backend(spec, root_dir=Path("."))


class TestBuildFilesystemBackend:
    def test_success_str_root(self):
        fake_mod = ModuleType("deepagents.backends")
        mock_instance = MagicMock()
        mock_mod_cls = MagicMock(return_value=mock_instance)
        fake_mod.FilesystemBackend = mock_mod_cls
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="filesystem", root_dir="data")
            result = build_filesystem_backend(spec, root_dir=Path("."))
            assert result is mock_instance

    def test_import_error_raises(self):
        with patch("builtins.__import__", side_effect=ImportError("not available")):
            spec = BackendConfig(type="filesystem", root_dir="data")
            with pytest.raises(FactoryError, match="FilesystemBackend unavailable"):
                build_filesystem_backend(spec, root_dir=Path("."))

    def test_typeerror_fallback_to_path_obj(self):
        fake_mod = ModuleType("deepagents.backends")

        class FB:
            def __init__(self, root_dir=None):
                if isinstance(root_dir, str):
                    raise TypeError("expected Path not str")
                self.root_dir = root_dir

        fake_mod.FilesystemBackend = FB
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="filesystem", root_dir="data")
            result = build_filesystem_backend(spec, root_dir=Path("."))
            assert isinstance(result, FB)

    def test_typeerror_fallback_to_positional(self):
        fake_mod = ModuleType("deepagents.backends")

        class FB:
            def __init__(self, *args, **kwargs):
                if kwargs:
                    raise TypeError("no kwargs accepted")
                self.root_dir = args[0] if args else None

        fake_mod.FilesystemBackend = FB
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="filesystem", root_dir="data")
            result = build_filesystem_backend(spec, root_dir=Path("."))
            assert isinstance(result, FB)


class TestBuildStoreBackend:
    def test_success(self):
        fake_mod = ModuleType("deepagents.backends")
        mock_instance = MagicMock()
        fake_mod.StoreBackend = MagicMock(return_value=mock_instance)
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="store", options={"key": "value"})
            result = build_store_backend(spec, root_dir=Path("."))
            assert result is mock_instance

    def test_no_options(self):
        fake_mod = ModuleType("deepagents.backends")
        mock_instance = MagicMock()
        fake_mod.StoreBackend = MagicMock(return_value=mock_instance)
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="store")
            result = build_store_backend(spec, root_dir=Path("."))
            assert result is mock_instance

    def test_import_error_raises(self):
        with patch("builtins.__import__", side_effect=ImportError("not available")):
            spec = BackendConfig(type="store")
            with pytest.raises(FactoryError, match="StoreBackend unavailable"):
                build_store_backend(spec, root_dir=Path("."))


class TestBuildBackend:
    def test_dispatch_to_state(self):
        fake_mod = ModuleType("deepagents.backends")
        fake_mod.StateBackend = MagicMock(return_value="state")
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="state")
            result = build_backend(spec, root_dir=Path("."))
            assert result == "state"

    def test_dispatch_to_filesystem(self):
        fake_mod = ModuleType("deepagents.backends")
        fake_mod.FilesystemBackend = MagicMock(return_value="fs")
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="filesystem", root_dir="data")
            result = build_backend(spec, root_dir=Path("."))
            assert result == "fs"

    def test_dispatch_to_store(self):
        fake_mod = ModuleType("deepagents.backends")
        fake_mod.StoreBackend = MagicMock(return_value="store")
        with patch.dict(sys.modules, {"deepagents.backends": fake_mod}):
            spec = BackendConfig(type="store")
            result = build_backend(spec, root_dir=Path("."))
            assert result == "store"
