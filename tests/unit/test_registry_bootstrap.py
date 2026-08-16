"""Unit tests for registry/bootstrap.py — _resolve_load_order, error paths, ensure_modules."""
from __future__ import annotations

import pytest

from agentbase.registry.bootstrap import (
    _import_module,
    _resolve_load_order,
    bootstrap_registries,
    ensure_modules,
)
from agentbase.config.schema import ExtensionsConfig


# ---------------------------------------------------------------------------
# _resolve_load_order
# ---------------------------------------------------------------------------


class TestResolveLoadOrder:
    def test_no_dependencies(self):
        result = _resolve_load_order(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_with_dependencies(self):
        result = _resolve_load_order(
            ["c", "a", "b"],
            dependencies={"c": ["a", "b"]},
        )
        # a and b should come before c
        assert result.index("a") < result.index("c")
        assert result.index("b") < result.index("c")

    def test_empty_modules(self):
        assert _resolve_load_order([]) == []

    def test_empty_dependencies(self):
        assert _resolve_load_order(["a", "b"], dependencies={}) == ["a", "b"]

    def test_none_dependencies(self):
        assert _resolve_load_order(["a", "b"], dependencies=None) == ["a", "b"]

    def test_circular_dependency(self):
        # a depends on b, b depends on a → circular
        result = _resolve_load_order(
            ["a", "b"],
            dependencies={"a": ["b"], "b": ["a"]},
        )
        # Should still return all modules (best-effort)
        assert set(result) == {"a", "b"}
        assert len(result) == 2

    def test_dependency_not_in_modules(self):
        # a depends on "external" which is not in the module list
        result = _resolve_load_order(
            ["a", "b"],
            dependencies={"a": ["external"]},
        )
        # "external" should be ignored; a and b still present
        assert set(result) == {"a", "b"}

    def test_chain_dependencies(self):
        result = _resolve_load_order(
            ["d", "c", "b", "a"],
            dependencies={"d": ["c"], "c": ["b"], "b": ["a"]},
        )
        # a → b → c → d
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")
        assert result.index("c") < result.index("d")


# ---------------------------------------------------------------------------
# _import_module
# ---------------------------------------------------------------------------


class TestImportModule:
    def test_import_existing_module(self):
        # Import a known module
        _import_module("agentbase.runtime.errors")
        import sys
        assert "agentbase.runtime.errors" in sys.modules

    def test_import_nonexistent_raises(self):
        with pytest.raises(ModuleNotFoundError):
            _import_module("nonexistent.module.xyz")


# ---------------------------------------------------------------------------
# bootstrap_registries — error paths
# ---------------------------------------------------------------------------


class TestBootstrapErrorPaths:
    def test_module_failure_continue_on_error(self):
        """When continue_on_error=True, failing modules are skipped."""
        from unittest.mock import patch
        import agentbase.registry.bootstrap as mod

        # Reset bootstrap state
        old = mod._BOOTSTRAPPED
        mod._BOOTSTRAPPED = False

        try:
            ext = ExtensionsConfig(extra_modules=["nonexistent.module.fail"])
            # Mock logger to avoid logging 'module' key conflict
            with patch.object(mod.logger, "warning"):
                # Should not raise
                bootstrap_registries(ext, force=True, continue_on_error=True)
        finally:
            mod._BOOTSTRAPPED = old

    def test_module_failure_raise_on_error(self):
        """When continue_on_error=False, failing modules raise."""
        import agentbase.registry.bootstrap as mod

        old = mod._BOOTSTRAPPED
        mod._BOOTSTRAPPED = False

        try:
            ext = ExtensionsConfig(extra_modules=["nonexistent.module.fail"])
            with pytest.raises(ModuleNotFoundError):
                bootstrap_registries(ext, force=True, continue_on_error=False)
        finally:
            mod._BOOTSTRAPPED = old

    def test_already_bootstrapped_skips(self):
        """When _BOOTSTRAPPED is True and force=False, returns early."""
        import agentbase.registry.bootstrap as mod

        old = mod._BOOTSTRAPPED
        mod._BOOTSTRAPPED = True

        try:
            ext = ExtensionsConfig()
            # Should be a no-op
            bootstrap_registries(ext, force=False)
        finally:
            mod._BOOTSTRAPPED = old

    def test_force_reruns(self):
        """When force=True, bootstrap runs even if already bootstrapped."""
        import agentbase.registry.bootstrap as mod

        old = mod._BOOTSTRAPPED
        mod._BOOTSTRAPPED = True

        try:
            ext = ExtensionsConfig()
            bootstrap_registries(ext, force=True)
            assert mod._BOOTSTRAPPED is True
        finally:
            mod._BOOTSTRAPPED = old


# ---------------------------------------------------------------------------
# ensure_modules
# ---------------------------------------------------------------------------


class TestEnsureModules:
    def test_ensure_existing_modules(self):
        ensure_modules(["agentbase.runtime.errors"])

    def test_ensure_nonexistent_raises(self):
        with pytest.raises(ModuleNotFoundError):
            ensure_modules(["nonexistent.module.xyz"])

    def test_ensure_empty_list(self):
        ensure_modules([])

    def test_ensure_multiple_modules(self):
        ensure_modules([
            "agentbase.runtime.errors",
            "agentbase.runtime.logging",
        ])
