"""Unit tests for the prompt template management service (core.prompt).

Covers:
- PromptTemplate dataclass (to_dict / from_dict)
- InMemoryPromptProvider CRUD (register / get / list / update / delete)
- NullPromptProvider (no-op behaviour)
- PromptManager (enabled / disabled / render / extract_variables)
- Registry (register_prompt_provider / get_prompt_provider / list_prompt_providers)
- Singleton (get_prompt_manager / set_prompt_manager)
- Concurrency (thread-safe operations)
- Protocol compliance
"""
from __future__ import annotations

import threading

import pytest

from agentbase.core.prompt import (
    InMemoryPromptProvider,
    NullPromptProvider,
    PromptManager,
    PromptProvider,
    PromptTemplate,
    _prompt_registry,
    _prompt_registry_lock,
    get_prompt_manager,
    get_prompt_provider,
    list_prompt_providers,
    register_prompt_provider,
    set_prompt_manager,
)


# ---------------------------------------------------------------------------
# PromptTemplate
# ---------------------------------------------------------------------------

class TestPromptTemplate:
    def test_to_dict_roundtrip(self):
        tpl = PromptTemplate(
            name="greeting",
            content="Hello {name}! You are a {role}.",
            variables=["name", "role"],
            description="Greeting prompt",
            category="chat",
            tags=["greeting", "chat"],
            version="1.0.0",
        )
        d = tpl.to_dict()
        assert d["name"] == "greeting"
        assert d["content"] == "Hello {name}! You are a {role}."
        assert d["variables"] == ["name", "role"]
        assert d["description"] == "Greeting prompt"
        assert d["category"] == "chat"
        assert d["tags"] == ["greeting", "chat"]
        assert d["version"] == "1.0.0"
        assert d["enabled"] is True
        assert "created_at" in d
        assert "updated_at" in d

    def test_from_dict_ignores_unknown_keys(self):
        data = {
            "name": "summary",
            "content": "Summarize: {text}",
            "variables": ["text"],
            "unknown_field": "should_be_ignored",
        }
        tpl = PromptTemplate.from_dict(data)
        assert tpl.name == "summary"
        assert tpl.content == "Summarize: {text}"
        assert not hasattr(tpl, "unknown_field")

    def test_defaults(self):
        tpl = PromptTemplate(name="test")
        assert tpl.content == ""
        assert tpl.variables == []
        assert tpl.description == ""
        assert tpl.category == ""
        assert tpl.tags == []
        assert tpl.version == "1.0.0"
        assert tpl.enabled is True

    def test_to_dict_and_from_dict_roundtrip(self):
        tpl = PromptTemplate(
            name="roundtrip",
            content="Hello {name}",
            variables=["name"],
            description="Test",
            category="test",
            tags=["unit"],
        )
        d = tpl.to_dict()
        restored = PromptTemplate.from_dict(d)
        assert restored.name == tpl.name
        assert restored.content == tpl.content
        assert restored.variables == tpl.variables
        assert restored.description == tpl.description
        assert restored.category == tpl.category
        assert restored.tags == tpl.tags
        assert restored.version == tpl.version
        assert restored.enabled == tpl.enabled


# ---------------------------------------------------------------------------
# InMemoryPromptProvider
# ---------------------------------------------------------------------------

class TestInMemoryPromptProvider:
    def test_register_and_get(self):
        provider = InMemoryPromptProvider()
        tpl = PromptTemplate(name="greeting", content="Hello {name}!", variables=["name"])
        stored = provider.register(tpl)
        assert stored.name == "greeting"
        retrieved = provider.get("greeting")
        assert retrieved is not None
        assert retrieved.content == "Hello {name}!"
        assert retrieved.variables == ["name"]

    def test_register_empty_name_raises(self):
        provider = InMemoryPromptProvider()
        with pytest.raises(ValueError, match="name cannot be empty"):
            provider.register(PromptTemplate(name="", content="test"))

    def test_register_replaces_existing(self):
        provider = InMemoryPromptProvider()
        provider.register(PromptTemplate(name="greeting", content="Hello {name}!"))
        provider.register(PromptTemplate(name="greeting", content="Hi {name}!"))
        retrieved = provider.get("greeting")
        assert retrieved is not None
        assert retrieved.content == "Hi {name}!"

    def test_register_preserves_created_at(self):
        provider = InMemoryPromptProvider()
        tpl1 = provider.register(PromptTemplate(name="greeting", content="Hello"))
        original_created = tpl1.created_at
        # Register again with different content
        tpl2 = provider.register(PromptTemplate(name="greeting", content="Hi"))
        # created_at should be preserved, updated_at should change
        assert tpl2.created_at == original_created
        assert tpl2.updated_at >= tpl2.created_at

    def test_get_nonexistent_returns_none(self):
        provider = InMemoryPromptProvider()
        assert provider.get("nonexistent") is None

    def test_list(self):
        provider = InMemoryPromptProvider()
        provider.register(PromptTemplate(name="tpl-a"))
        provider.register(PromptTemplate(name="tpl-b"))
        templates = provider.list()
        assert len(templates) == 2
        names = {t.name for t in templates}
        assert names == {"tpl-a", "tpl-b"}

    def test_list_empty(self):
        provider = InMemoryPromptProvider()
        assert provider.list() == []

    def test_update(self):
        provider = InMemoryPromptProvider()
        provider.register(PromptTemplate(
            name="greeting", content="Hello", description="Original",
        ))
        updated = provider.update("greeting", {
            "content": "Hello {name}!",
            "description": "Updated",
        })
        assert updated is not None
        assert updated.content == "Hello {name}!"
        assert updated.description == "Updated"

    def test_update_nonexistent_returns_none(self):
        provider = InMemoryPromptProvider()
        assert provider.update("nonexistent", {"content": "test"}) is None

    def test_update_ignores_name_and_created_at(self):
        provider = InMemoryPromptProvider()
        tpl = provider.register(PromptTemplate(name="greeting", content="Hello"))
        original_created = tpl.created_at
        # Try to update name and created_at — should be ignored
        updated = provider.update("greeting", {
            "name": "changed",
            "created_at": "2020-01-01",
        })
        assert updated.name == "greeting"  # name not changed
        assert updated.created_at == original_created  # created_at not changed

    def test_delete(self):
        provider = InMemoryPromptProvider()
        provider.register(PromptTemplate(name="greeting"))
        assert provider.delete("greeting") is True
        assert provider.get("greeting") is None

    def test_delete_nonexistent_returns_false(self):
        provider = InMemoryPromptProvider()
        assert provider.delete("nonexistent") is False

    def test_close(self):
        provider = InMemoryPromptProvider()
        provider.register(PromptTemplate(name="greeting"))
        provider.register(PromptTemplate(name="summary"))
        provider.close()
        assert provider.list() == []

    def test_close_empty(self):
        provider = InMemoryPromptProvider()
        provider.close()  # Should not raise

    def test_concurrent_register(self):
        provider = InMemoryPromptProvider()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                provider.register(PromptTemplate(
                    name=f"tpl-{i}",
                    content=f"Content {i}",
                ))
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
# NullPromptProvider
# ---------------------------------------------------------------------------

class TestNullPromptProvider:
    def test_register_returns_input(self):
        provider = NullPromptProvider()
        tpl = PromptTemplate(name="test", content="Hello")
        result = provider.register(tpl)
        assert result is tpl

    def test_get_returns_none(self):
        provider = NullPromptProvider()
        assert provider.get("anything") is None

    def test_list_returns_empty(self):
        provider = NullPromptProvider()
        assert provider.list() == []

    def test_update_returns_none(self):
        provider = NullPromptProvider()
        assert provider.update("anything", {"content": "test"}) is None

    def test_delete_returns_false(self):
        provider = NullPromptProvider()
        assert provider.delete("anything") is False

    def test_close_is_noop(self):
        provider = NullPromptProvider()
        provider.close()  # Should not raise


# ---------------------------------------------------------------------------
# PromptManager
# ---------------------------------------------------------------------------

class TestPromptManager:
    def test_disabled_uses_null_provider(self):
        mgr = PromptManager(enabled=False)
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullPromptProvider)

    def test_enabled_uses_memory_provider(self):
        mgr = PromptManager(provider="memory", enabled=True)
        assert mgr.enabled is True
        assert isinstance(mgr.provider, InMemoryPromptProvider)

    def test_disabled_operations_are_noop(self):
        mgr = PromptManager(enabled=False)
        # register returns the template but doesn't store
        tpl = mgr.register(PromptTemplate(name="test", content="Hello"))
        assert tpl.name == "test"
        # get returns None
        assert mgr.get("test") is None
        # list returns empty
        assert mgr.list() == []
        # update returns None
        assert mgr.update("test", {"content": "Hi"}) is None
        # delete returns False
        assert mgr.delete("test") is False

    def test_register_and_get(self):
        mgr = PromptManager(provider="memory", enabled=True)
        tpl = PromptTemplate(
            name="greeting",
            content="Hello {name}!",
            variables=["name"],
        )
        stored = mgr.register(tpl)
        assert stored.name == "greeting"
        retrieved = mgr.get("greeting")
        assert retrieved is not None
        assert retrieved.content == "Hello {name}!"

    def test_list(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="greeting"))
        mgr.register(PromptTemplate(name="summary"))
        templates = mgr.list()
        assert len(templates) == 2

    def test_update(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="greeting", content="Hello"))
        updated = mgr.update("greeting", {"content": "Hi {name}!"})
        assert updated is not None
        assert updated.content == "Hi {name}!"

    def test_update_nonexistent(self):
        mgr = PromptManager(provider="memory", enabled=True)
        assert mgr.update("nonexistent", {"content": "test"}) is None

    def test_delete(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="greeting"))
        assert mgr.delete("greeting") is True
        assert mgr.get("greeting") is None

    def test_delete_nonexistent(self):
        mgr = PromptManager(provider="memory", enabled=True)
        assert mgr.delete("nonexistent") is False

    def test_render(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(
            name="greeting",
            content="Hello {name}! You are a {role}.",
            variables=["name", "role"],
        ))
        rendered = mgr.render("greeting", name="Alice", role="engineer")
        assert rendered == "Hello Alice! You are a engineer."

    def test_render_missing_variable_raises_keyerror(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(
            name="greeting",
            content="Hello {name}!",
            variables=["name"],
        ))
        with pytest.raises(KeyError, match="Missing variable 'name'"):
            mgr.render("greeting")  # Missing 'name'

    def test_render_nonexistent_template_raises_valueerror(self):
        mgr = PromptManager(provider="memory", enabled=True)
        with pytest.raises(ValueError, match="not found"):
            mgr.render("nonexistent", name="Alice")

    def test_render_disabled_template_raises_valueerror(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="greeting", content="Hello", enabled=False))
        with pytest.raises(ValueError, match="disabled"):
            mgr.render("greeting")

    def test_extract_variables(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(
            name="complex",
            content="Hello {name}, your score is {score}. Role: {role}",
        ))
        variables = mgr.extract_variables("complex")
        assert set(variables) == {"name", "score", "role"}

    def test_extract_variables_no_variables(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="simple", content="No variables here."))
        variables = mgr.extract_variables("simple")
        assert variables == []

    def test_extract_variables_nonexistent_raises(self):
        mgr = PromptManager(provider="memory", enabled=True)
        with pytest.raises(ValueError, match="not found"):
            mgr.extract_variables("nonexistent")

    def test_render_with_extra_variables_ignored(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="simple", content="Hello {name}!"))
        # str.format() ignores extra kwargs
        rendered = mgr.render("simple", name="Alice", extra="ignored")
        assert rendered == "Hello Alice!"

    def test_render_empty_content(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="empty", content=""))
        rendered = mgr.render("empty")
        assert rendered == ""

    def test_close(self):
        mgr = PromptManager(provider="memory", enabled=True)
        mgr.register(PromptTemplate(name="greeting"))
        mgr.close()
        # Should not raise

    def test_unknown_provider_falls_back_to_null(self):
        mgr = PromptManager(provider="nonexistent_provider", enabled=True)
        assert isinstance(mgr.provider, NullPromptProvider)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_default_providers_registered(self):
        names = list_prompt_providers()
        assert "memory" in names
        assert "null" in names

    def test_get_memory_provider(self):
        provider = get_prompt_provider("memory")
        assert isinstance(provider, InMemoryPromptProvider)

    def test_get_null_provider(self):
        provider = get_prompt_provider("null")
        assert isinstance(provider, NullPromptProvider)

    def test_get_unknown_provider_raises(self):
        from agentbase.runtime.errors import RegistryError
        with pytest.raises(RegistryError, match="Unknown prompt provider"):
            get_prompt_provider("nonexistent_provider_name")

    def test_register_custom_provider(self):
        class CustomProvider:
            def register(self, template):
                return template

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

        register_prompt_provider("custom_test")(CustomProvider)
        try:
            provider = get_prompt_provider("custom_test")
            assert isinstance(provider, CustomProvider)
            assert "custom_test" in list_prompt_providers()
        finally:
            # Clean up registry
            with _prompt_registry_lock:
                _prompt_registry.pop("custom_test", None)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_default_manager_is_disabled(self):
        # Reset singleton to ensure fresh state
        set_prompt_manager(PromptManager(enabled=False))
        mgr = get_prompt_manager()
        assert mgr.enabled is False

    def test_set_and_get_manager(self):
        custom = PromptManager(provider="memory", enabled=True)
        set_prompt_manager(custom)
        assert get_prompt_manager() is custom
        # Reset
        set_prompt_manager(PromptManager(enabled=False))


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    def test_inmemory_provider_satisfies_protocol(self):
        provider = InMemoryPromptProvider()
        assert isinstance(provider, PromptProvider)

    def test_null_provider_satisfies_protocol(self):
        provider = NullPromptProvider()
        assert isinstance(provider, PromptProvider)

    def test_prompt_manager_provider_satisfies_protocol(self):
        mgr = PromptManager(provider="memory", enabled=True)
        assert isinstance(mgr.provider, PromptProvider)
