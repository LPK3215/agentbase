"""Unit tests for the model management service (core.model_manager).

Covers:
- ModelEntry dataclass (to_dict / from_dict)
- InMemoryModelProvider CRUD (register / get / list / update / delete)
- NullModelProvider (no-op behaviour)
- ModelManager (enabled / disabled / test)
- Registry (register_model_provider / get_model_provider / list_model_providers)
- Concurrency (thread-safe operations)
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.model_manager import (
    InMemoryModelProvider,
    ModelEntry,
    ModelManager,
    ModelProvider,
    NullModelProvider,
    _model_registry,
    get_model_manager,
    get_model_provider,
    list_model_providers,
    register_model_provider,
    set_model_manager,
)


# ---------------------------------------------------------------------------
# ModelEntry
# ---------------------------------------------------------------------------

class TestModelEntry:
    def test_to_dict_roundtrip(self):
        entry = ModelEntry(
            name="gpt-4",
            provider="openai",
            model_name="gpt-4.1",
            temperature=0.7,
            api_key_env="OPENAI_API_KEY",
            tags=["chat", "fast"],
            description="GPT-4 model",
        )
        d = entry.to_dict()
        assert d["name"] == "gpt-4"
        assert d["provider"] == "openai"
        assert d["model_name"] == "gpt-4.1"
        assert d["temperature"] == 0.7
        assert d["api_key_env"] == "OPENAI_API_KEY"
        assert d["tags"] == ["chat", "fast"]
        assert d["enabled"] is True
        assert "created_at" in d
        assert "updated_at" in d

    def test_from_dict_ignores_unknown_keys(self):
        data = {
            "name": "claude",
            "provider": "anthropic",
            "model_name": "claude-3-5-sonnet",
            "unknown_field": "should_be_ignored",
        }
        entry = ModelEntry.from_dict(data)
        assert entry.name == "claude"
        assert entry.provider == "anthropic"
        assert not hasattr(entry, "unknown_field")

    def test_defaults(self):
        entry = ModelEntry(name="test")
        assert entry.provider == "openai"
        assert entry.model_name == ""
        assert entry.temperature == 0.0
        assert entry.max_tokens is None
        assert entry.timeout_seconds == 120
        assert entry.enabled is True
        assert entry.tags == []
        assert entry.extra == {}


# ---------------------------------------------------------------------------
# InMemoryModelProvider
# ---------------------------------------------------------------------------

class TestInMemoryModelProvider:
    def test_register_and_get(self):
        provider = InMemoryModelProvider()
        entry = ModelEntry(name="gpt-4", provider="openai", model_name="gpt-4.1")
        stored = provider.register(entry)
        assert stored.name == "gpt-4"
        retrieved = provider.get("gpt-4")
        assert retrieved is not None
        assert retrieved.provider == "openai"
        assert retrieved.model_name == "gpt-4.1"

    def test_register_empty_name_raises(self):
        provider = InMemoryModelProvider()
        with pytest.raises(ValueError, match="name cannot be empty"):
            provider.register(ModelEntry(name=""))

    def test_register_replaces_existing(self):
        provider = InMemoryModelProvider()
        provider.register(ModelEntry(name="gpt-4", provider="openai"))
        provider.register(ModelEntry(name="gpt-4", provider="anthropic"))
        retrieved = provider.get("gpt-4")
        assert retrieved is not None
        assert retrieved.provider == "anthropic"

    def test_register_preserves_created_at(self):
        provider = InMemoryModelProvider()
        entry1 = provider.register(ModelEntry(name="gpt-4", provider="openai"))
        original_created = entry1.created_at
        # Register again with different provider
        entry2 = provider.register(ModelEntry(name="gpt-4", provider="anthropic"))
        # created_at should be preserved, updated_at should change
        assert entry2.created_at == original_created
        assert entry2.updated_at >= entry2.created_at

    def test_get_nonexistent_returns_none(self):
        provider = InMemoryModelProvider()
        assert provider.get("nonexistent") is None

    def test_list(self):
        provider = InMemoryModelProvider()
        provider.register(ModelEntry(name="model-a"))
        provider.register(ModelEntry(name="model-b"))
        models = provider.list()
        assert len(models) == 2
        names = {m.name for m in models}
        assert names == {"model-a", "model-b"}

    def test_list_empty(self):
        provider = InMemoryModelProvider()
        assert provider.list() == []

    def test_update(self):
        provider = InMemoryModelProvider()
        provider.register(ModelEntry(name="gpt-4", provider="openai", temperature=0.0))
        updated = provider.update("gpt-4", {"temperature": 0.8, "description": "Updated"})
        assert updated is not None
        assert updated.temperature == 0.8
        assert updated.description == "Updated"

    def test_update_nonexistent_returns_none(self):
        provider = InMemoryModelProvider()
        assert provider.update("nonexistent", {"temperature": 0.5}) is None

    def test_update_ignores_name_and_created_at(self):
        provider = InMemoryModelProvider()
        entry = provider.register(ModelEntry(name="gpt-4", provider="openai"))
        original_created = entry.created_at
        # Try to update name and created_at — should be ignored
        updated = provider.update("gpt-4", {"name": "changed", "created_at": "2020-01-01"})
        assert updated.name == "gpt-4"  # name not changed
        assert updated.created_at == original_created  # created_at not changed

    def test_delete(self):
        provider = InMemoryModelProvider()
        provider.register(ModelEntry(name="gpt-4"))
        assert provider.delete("gpt-4") is True
        assert provider.get("gpt-4") is None
        assert provider.list() == []

    def test_delete_nonexistent_returns_false(self):
        provider = InMemoryModelProvider()
        assert provider.delete("nonexistent") is False

    def test_close_clears_all(self):
        provider = InMemoryModelProvider()
        provider.register(ModelEntry(name="model-a"))
        provider.register(ModelEntry(name="model-b"))
        provider.close()
        assert provider.list() == []

    def test_concurrent_register(self):
        """Thread-safe concurrent registration should not lose entries."""
        provider = InMemoryModelProvider()
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                provider.register(ModelEntry(name=f"model-{idx}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(provider.list()) == 20


# ---------------------------------------------------------------------------
# NullModelProvider
# ---------------------------------------------------------------------------

class TestNullModelProvider:
    def test_register_returns_entry(self):
        provider = NullModelProvider()
        entry = ModelEntry(name="test")
        result = provider.register(entry)
        assert result is entry

    def test_get_returns_none(self):
        provider = NullModelProvider()
        assert provider.get("anything") is None

    def test_list_returns_empty(self):
        provider = NullModelProvider()
        assert provider.list() == []

    def test_update_returns_none(self):
        provider = NullModelProvider()
        assert provider.update("anything", {}) is None

    def test_delete_returns_false(self):
        provider = NullModelProvider()
        assert provider.delete("anything") is False


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------

class TestModelManager:
    def test_disabled_manager_uses_null_provider(self):
        mgr = ModelManager(enabled=False)
        assert not mgr.enabled
        assert isinstance(mgr.provider, NullModelProvider)

    def test_enabled_manager_uses_in_memory(self):
        mgr = ModelManager(enabled=True, provider="memory")
        assert mgr.enabled
        assert isinstance(mgr.provider, InMemoryModelProvider)

    def test_disabled_manager_operations_are_noop(self):
        mgr = ModelManager(enabled=False)
        result = mgr.register(ModelEntry(name="test"))
        assert result.name == "test"
        assert mgr.get("test") is None
        assert mgr.list() == []

    def test_enabled_manager_crud(self):
        mgr = ModelManager(enabled=True, provider="memory")
        mgr.register(ModelEntry(name="gpt-4", provider="openai"))
        assert mgr.get("gpt-4") is not None
        assert len(mgr.list()) == 1
        updated = mgr.update("gpt-4", {"temperature": 0.8})
        assert updated.temperature == 0.8
        assert mgr.delete("gpt-4") is True
        assert mgr.list() == []

    def test_test_model_not_found(self):
        mgr = ModelManager(enabled=True, provider="memory")
        result = mgr.test("nonexistent")
        assert not result.success
        assert "not found" in result.error

    def test_test_model_disabled(self):
        mgr = ModelManager(enabled=True, provider="memory")
        mgr.register(ModelEntry(name="gpt-4", enabled=False))
        result = mgr.test("gpt-4")
        assert not result.success
        assert "disabled" in result.error

    def test_test_model_failure(self):
        """Test should return failure when model building raises."""
        mgr = ModelManager(enabled=True, provider="memory")
        mgr.register(ModelEntry(name="bad-model", provider="openai", api_key_env="NONEXISTENT_KEY"))
        result = mgr.test("bad-model")
        assert not result.success
        assert result.error != ""
        assert result.duration_ms >= 0

    def test_fallback_to_null_on_unknown_provider(self):
        """ModelManager should fall back to NullModelProvider on unknown provider."""
        mgr = ModelManager(enabled=True, provider="nonexistent_provider")
        assert isinstance(mgr.provider, NullModelProvider)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_list_model_providers_includes_defaults(self):
        providers = list_model_providers()
        assert "memory" in providers
        assert "null" in providers

    def test_get_model_provider_memory(self):
        provider = get_model_provider("memory")
        assert isinstance(provider, InMemoryModelProvider)

    def test_get_model_provider_null(self):
        provider = get_model_provider("null")
        assert isinstance(provider, NullModelProvider)

    def test_get_model_provider_unknown_raises(self):
        from agentbase.runtime.errors import RegistryError

        with pytest.raises(RegistryError, match="Unknown model provider"):
            get_model_provider("definitely_not_registered")

    def test_register_custom_provider(self):
        from agentbase.core.model_manager import _model_registry_lock

        @register_model_provider("test-custom")
        class _CustomProvider:
            def __init__(self, **kwargs):
                pass

            def register(self, entry):
                return entry

            def get(self, name):
                return None

            def list(self):
                return []

            def update(self, name, changes):
                return None

            def delete(self, name):
                return False

            def close(self):
                pass

        try:
            provider = get_model_provider("test-custom")
            assert provider is not None
        finally:
            # Clean up
            with _model_registry_lock:
                _model_registry.pop("test-custom", None)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_default_singleton_is_disabled(self):
        # Reset to ensure clean state
        set_model_manager(ModelManager(enabled=False))
        mgr = get_model_manager()
        assert not mgr.enabled

    def test_set_singleton(self):
        custom = ModelManager(enabled=True, provider="memory")
        set_model_manager(custom)
        assert get_model_manager() is custom
        # Reset
        set_model_manager(ModelManager(enabled=False))


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    def test_in_memory_is_model_provider(self):
        provider = InMemoryModelProvider()
        assert isinstance(provider, ModelProvider)

    def test_null_is_model_provider(self):
        provider = NullModelProvider()
        assert isinstance(provider, ModelProvider)
