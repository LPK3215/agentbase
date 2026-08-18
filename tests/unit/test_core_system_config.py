"""Tests for the system config core service.

Covers:
- Data model (ConfigItem / Filter / Stats)
- Null provider (disabled no-op semantics)
- InMemory provider (CRUD, upsert versioning, FIFO eviction, filtering, stats)
- Registry (register / create / duplicate / unknown / unregister)
- Manager (validation, upsert, change callbacks, delete notifications)
- Singleton (get / set / reset)
- Concurrency (parallel set/get)
- Protocol compliance
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from agentbase.core.system_config import (
    ConfigItem,
    InMemorySystemConfigProvider,
    NullSystemConfigProvider,
    SystemConfigFilter,
    SystemConfigManager,
    SystemConfigProvider,
    SystemConfigRegistry,
    get_system_config_manager,
    register_system_config_provider,
    reset_system_config_manager,
    set_system_config_manager,
    system_config_registry,
)
from agentbase.runtime.errors import RegistryError


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TestConfigItem:
    def test_defaults(self):
        it = ConfigItem(key="a.b")
        assert it.value is None
        assert it.category == "general"
        assert it.description == ""
        assert it.is_public is False
        assert it.version == 1
        assert it.created_at
        assert it.updated_at

    def test_to_dict_roundtrip(self):
        it = ConfigItem(key="a.b", value={"x": 1}, category="feature", is_public=True)
        d = it.to_dict()
        assert d["key"] == "a.b"
        assert d["value"] == {"x": 1}
        assert d["category"] == "feature"
        assert d["is_public"] is True


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------


class TestNullProvider:
    def test_all_noop(self):
        p = NullSystemConfigProvider()
        item = p.set_item(ConfigItem(key="a.b", value=1))
        assert item.key == "a.b"
        assert p.get_item("a.b") is None
        assert p.list_items() == []
        assert p.delete_item("a.b") is False
        assert p.get_stats().total == 0
        p.close()


# ---------------------------------------------------------------------------
# InMemory provider
# ---------------------------------------------------------------------------


class TestInMemoryProvider:
    def test_set_and_get(self):
        p = InMemorySystemConfigProvider()
        stored = p.set_item(ConfigItem(key="a.b", value=42))
        assert stored.version == 1
        got = p.get_item("a.b")
        assert got is not None and got.value == 42

    def test_upsert_bumps_version_and_preserves_created_at(self):
        p = InMemorySystemConfigProvider()
        first = p.set_item(ConfigItem(key="a.b", value=1))
        second = p.set_item(ConfigItem(key="a.b", value=2))
        assert second.version == first.version + 1
        assert second.created_at == first.created_at

    def test_get_missing(self):
        assert InMemorySystemConfigProvider().get_item("nope") is None

    def test_delete(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.b", value=1))
        assert p.delete_item("a.b") is True
        assert p.get_item("a.b") is None
        assert p.delete_item("a.b") is False

    def test_fifo_eviction(self):
        p = InMemorySystemConfigProvider(max_items=3)
        for i in range(5):
            p.set_item(ConfigItem(key=f"k.{i}", value=i))
        keys = [it.key for it in p.list_items()]
        assert keys == ["k.2", "k.3", "k.4"]  # oldest two evicted

    def test_list_sorted_by_key(self):
        p = InMemorySystemConfigProvider()
        for k in ("c.x", "a.x", "b.x"):
            p.set_item(ConfigItem(key=k, value=1))
        assert [it.key for it in p.list_items()] == ["a.x", "b.x", "c.x"]

    def test_filter_category(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.x", value=1, category="feature"))
        p.set_item(ConfigItem(key="b.x", value=2, category="quota"))
        items = p.list_items(SystemConfigFilter(category="quota"))
        assert [it.key for it in items] == ["b.x"]

    def test_filter_key_prefix(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="feature.a", value=1))
        p.set_item(ConfigItem(key="quota.b", value=2))
        items = p.list_items(SystemConfigFilter(key_prefix="feature."))
        assert [it.key for it in items] == ["feature.a"]

    def test_filter_public_only(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.pub", value=1, is_public=True))
        p.set_item(ConfigItem(key="b.priv", value=2, is_public=False))
        items = p.list_items(SystemConfigFilter(public_only=True))
        assert [it.key for it in items] == ["a.pub"]

    def test_filter_updated_since(self):
        from datetime import UTC, datetime, timedelta

        p = InMemorySystemConfigProvider()
        old = ConfigItem(key="a.old", value=1)
        old.updated_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        p.set_item(old)
        p.set_item(ConfigItem(key="b.new", value=2))
        cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        items = p.list_items(SystemConfigFilter(updated_since=cutoff))
        assert [it.key for it in items] == ["b.new"]

    def test_filter_updated_since_invalid_ignored(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.x", value=1))
        # Invalid ISO string → treated as no filter
        items = p.list_items(SystemConfigFilter(updated_since="not-a-date"))
        assert len(items) == 1

    def test_pagination(self):
        p = InMemorySystemConfigProvider(max_items=100)
        for i in range(15):
            p.set_item(ConfigItem(key=f"k.{i:02d}", value=i))
        page = p.list_items(SystemConfigFilter(limit=5, offset=10))
        assert [it.key for it in page] == [f"k.{i:02d}" for i in range(10, 15)]

    def test_stats(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.x", value=1, category="feature", is_public=True))
        p.set_item(ConfigItem(key="b.y", value=2, category="feature"))
        p.set_item(ConfigItem(key="c.z", value=3, category="quota"))
        stats = p.get_stats()
        assert stats.total == 3
        assert stats.public_count == 1
        assert stats.by_category == {"feature": 2, "quota": 1}
        assert stats.recently_updated == 3  # all just written

    def test_close_clears(self):
        p = InMemorySystemConfigProvider()
        p.set_item(ConfigItem(key="a.b", value=1))
        p.close()
        assert p.get_item("a.b") is None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_defaults_registered(self):
        assert system_config_registry.has("memory")
        assert system_config_registry.has("null")
        assert system_config_registry.names() == ["memory", "null"]

    def test_create_memory(self):
        p = system_config_registry.create("memory", max_items=5)
        assert isinstance(p, InMemorySystemConfigProvider)

    def test_unknown_provider(self):
        with pytest.raises(RegistryError, match="Unknown system config provider"):
            system_config_registry.create("nope")

    def test_duplicate_registration(self):
        reg = SystemConfigRegistry()
        reg.register("a", NullSystemConfigProvider)
        with pytest.raises(RegistryError, match="already registered"):
            reg.register("a", NullSystemConfigProvider)

    def test_empty_name(self):
        with pytest.raises(RegistryError, match="empty"):
            SystemConfigRegistry().register("  ", NullSystemConfigProvider)

    def test_override(self):
        reg = SystemConfigRegistry()
        reg.register("a", NullSystemConfigProvider)
        reg.register("a", InMemorySystemConfigProvider, override=True)
        assert reg.create("a").__class__ is InMemorySystemConfigProvider

    def test_unregister(self):
        reg = SystemConfigRegistry()
        reg.register("a", NullSystemConfigProvider)
        assert reg.unregister("a") is True
        assert reg.unregister("a") is False

    def test_decorator(self):
        @register_system_config_provider("test_syscfg", override=True)
        class Custom:
            def set_item(self, item): return item
            def get_item(self, key): return None
            def list_items(self, filter=None): return []
            def delete_item(self, key): return False
            def get_stats(self): from agentbase.core.system_config import SystemConfigStats; return SystemConfigStats()
            def close(self): pass

        assert system_config_registry.has("test_syscfg")
        system_config_registry.unregister("test_syscfg")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TestManagerValidation:
    def test_disabled_defaults_to_null(self):
        mgr = SystemConfigManager()
        assert mgr.enabled is False
        assert isinstance(mgr.provider, NullSystemConfigProvider)

    def test_invalid_key_empty(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="key is required"):
            mgr.set("", 1)

    def test_invalid_key_charset(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        # uppercase is normalized to lowercase (case-insensitive keys)
        item = mgr.set("Feature.CASE", 1)
        assert item.key == "feature.case"
        with pytest.raises(RegistryError, match="Invalid config key"):
            mgr.set(".leading.dot", 1)
        with pytest.raises(RegistryError, match="Invalid config key"):
            mgr.set("has space", 1)
        with pytest.raises(RegistryError, match="Invalid config key"):
            mgr.set("bad!chars", 1)

    def test_invalid_key_too_long(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="too long"):
            mgr.set("a" * 129, 1)

    def test_key_normalized(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        item = mgr.set("  Feature.TEST  ", 1)
        assert item.key == "feature.test"

    def test_value_not_serializable(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="not JSON-serializable"):
            mgr.set("a.b", lambda: None)

    def test_value_too_large(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="too large"):
            mgr.set("a.b", "x" * 70_000)

    def test_metadata_limits(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        with pytest.raises(RegistryError, match="category too long"):
            mgr.set("a.b", 1, category="c" * 65)
        with pytest.raises(RegistryError, match="description too long"):
            mgr.set("a.b", 1, description="d" * 1_001)
        with pytest.raises(RegistryError, match="updated_by too long"):
            mgr.set("a.b", 1, updated_by="u" * 129)


class TestManagerCRUD:
    def test_set_get(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("feature.dark_mode", True, category="feature")
        assert mgr.get("feature.dark_mode") is True

    def test_get_default(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        assert mgr.get("missing.key") is None
        assert mgr.get("missing.key", default="off") == "off"

    def test_upsert_preserves_category_and_description(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("a.b", 1, category="feature", description="d1")
        item = mgr.set("a.b", 2)
        assert item.version == 2
        assert item.category == "feature"
        assert item.description == "d1"

    def test_exists_and_keys(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("feature.a", 1)
        mgr.set("feature.b", 2)
        mgr.set("quota.c", 3)
        assert mgr.exists("feature.a") is True
        assert mgr.exists("nope") is False
        assert mgr.keys() == ["feature.a", "feature.b", "quota.c"]
        assert mgr.keys(prefix="feature.") == ["feature.a", "feature.b"]

    def test_list_items(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("feature.a", 1, is_public=True)
        mgr.set("quota.b", 2)
        items = mgr.list_items(public_only=True)
        assert [it.key for it in items] == ["feature.a"]

    def test_delete(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("a.b", 1)
        assert mgr.delete("a.b") is True
        assert mgr.delete("a.b") is False

    def test_stats(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        mgr.set("a.b", 1)
        stats = mgr.get_stats()
        assert stats.total == 1

    def test_disabled_manager_noop(self):
        mgr = SystemConfigManager()  # disabled
        mgr.set("a.b", 1)
        assert mgr.get("a.b") is None
        assert mgr.keys() == []


class TestChangeCallbacks:
    def test_set_fires_callback(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        seen: list[tuple[str, Any, Any]] = []
        mgr.on_change(lambda k, old, new: seen.append((k, old, new)))
        mgr.set("a.b", 1)
        mgr.set("a.b", 2)
        assert seen == [("a.b", None, 1), ("a.b", 1, 2)]

    def test_delete_fires_callback(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        seen: list[tuple[str, Any, Any]] = []
        mgr.on_change(lambda k, old, new: seen.append((k, old, new)))
        mgr.set("a.b", 1)
        mgr.delete("a.b")
        assert seen[-1] == ("a.b", 1, None)

    def test_failing_callback_does_not_block(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)

        def bad(key, old, new):
            raise RuntimeError("boom")

        mgr.on_change(bad)
        mgr.set("a.b", 1)  # must not raise
        assert mgr.get("a.b") == 1


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_creates_disabled(self):
        reset_system_config_manager()
        mgr = get_system_config_manager()
        assert mgr.enabled is False

    def test_set_and_reset(self):
        mgr = SystemConfigManager(provider="memory", enabled=True)
        set_system_config_manager(mgr)
        assert get_system_config_manager() is mgr
        reset_system_config_manager()
        assert get_system_config_manager() is not mgr
        reset_system_config_manager()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_parallel_sets(self):
        mgr = SystemConfigManager(provider="memory", enabled=True, max_items=1000)
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(20):
                    mgr.set(f"k.{n}.{i}", i)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(mgr.keys()) == 160


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_inmemory_satisfies_protocol(self):
        assert isinstance(InMemorySystemConfigProvider(), SystemConfigProvider)

    def test_null_satisfies_protocol(self):
        assert isinstance(NullSystemConfigProvider(), SystemConfigProvider)
