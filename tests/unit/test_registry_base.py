"""Unit tests for registry base — Registry class.

Tests cover:
- register (normal, override, empty name, duplicate)
- unregister (existing, non-existing)
- clear
- count
- get (existing, non-existing)
- has
- names
- items
- get_meta
- metas
- decorator
"""
from __future__ import annotations

import pytest

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.base import Registry
from agentbase.runtime.errors import RegistryError


class TestRegistryRegister:
    def test_register_normal(self):
        reg = Registry("test")
        item = reg.register("foo", "item1")
        assert item == "item1"
        assert reg.get("foo") == "item1"

    def test_register_with_meta(self):
        reg = Registry("test")
        meta = ExtensionMeta(name="foo", kind="test", description="desc")
        reg.register("foo", "item1", meta=meta)
        assert reg.get_meta("foo") is meta

    def test_register_strips_whitespace(self):
        reg = Registry("test")
        reg.register("  foo  ", "item1")
        assert reg.has("foo")
        assert reg.has("  foo  ")

    def test_register_empty_name_raises(self):
        reg = Registry("test")
        with pytest.raises(RegistryError, match="empty"):
            reg.register("   ", "item1")

    def test_register_duplicate_raises(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        with pytest.raises(RegistryError, match="already registered"):
            reg.register("foo", "item2")

    def test_register_override(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        reg.register("foo", "item2", override=True)
        assert reg.get("foo") == "item2"

    def test_register_meta_from_item_attribute(self):
        reg = Registry("test")

        class ItemWithMeta:
            __agentbase_meta__ = ExtensionMeta(name="bar", kind="test", description="auto")

        item = ItemWithMeta()
        reg.register("bar", item)
        assert reg.get_meta("bar") is item.__agentbase_meta__


class TestRegistryUnregister:
    def test_unregister_existing(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        assert reg.unregister("foo") is True
        assert not reg.has("foo")

    def test_unregister_non_existing(self):
        reg = Registry("test")
        assert reg.unregister("nonexistent") is False

    def test_unregister_removes_meta(self):
        reg = Registry("test")
        meta = ExtensionMeta(name="foo", kind="test", description="d")
        reg.register("foo", "item1", meta=meta)
        reg.unregister("foo")
        assert reg.get_meta("foo") is None


class TestRegistryClear:
    def test_clear_returns_count(self):
        reg = Registry("test")
        reg.register("a", 1)
        reg.register("b", 2)
        reg.register("c", 3)
        count = reg.clear()
        assert count == 3
        assert reg.count == 0

    def test_clear_empty(self):
        reg = Registry("test")
        assert reg.clear() == 0

    def test_clear_removes_metas(self):
        reg = Registry("test")
        meta = ExtensionMeta(name="a", kind="test", description="d")
        reg.register("a", 1, meta=meta)
        reg.clear()
        assert reg.metas() == {}


class TestRegistryProperties:
    def test_count(self):
        reg = Registry("test")
        reg.register("a", 1)
        reg.register("b", 2)
        assert reg.count == 2

    def test_names_sorted(self):
        reg = Registry("test")
        reg.register("c", 3)
        reg.register("a", 1)
        reg.register("b", 2)
        assert reg.names() == ["a", "b", "c"]

    def test_items_returns_copy(self):
        reg = Registry("test")
        reg.register("a", 1)
        items = reg.items()
        items["b"] = 2
        assert not reg.has("b")

    def test_metas_returns_copy(self):
        reg = Registry("test")
        meta = ExtensionMeta(name="a", kind="test", description="d")
        reg.register("a", 1, meta=meta)
        metas = reg.metas()
        metas["b"] = ExtensionMeta(name="b", kind="test", description="d")
        assert "b" not in reg.metas()


class TestRegistryGet:
    def test_get_existing(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        assert reg.get("foo") == "item1"

    def test_get_non_existing_raises(self):
        reg = Registry("test")
        reg.register("a", 1)
        with pytest.raises(RegistryError, match="Unknown test"):
            reg.get("nonexistent")

    def test_get_empty_registry_error_message(self):
        reg = Registry("test")
        with pytest.raises(RegistryError, match="<empty>"):
            reg.get("nonexistent")

    def test_get_strips_whitespace(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        assert reg.get("  foo  ") == "item1"


class TestRegistryHas:
    def test_has_existing(self):
        reg = Registry("test")
        reg.register("foo", "item1")
        assert reg.has("foo") is True

    def test_has_non_existing(self):
        reg = Registry("test")
        assert reg.has("nonexistent") is False


class TestRegistryDecorator:
    def test_decorator_registers(self):
        reg = Registry("test")

        @reg.decorator("my_item")
        def my_func():
            pass

        assert reg.has("my_item")
        assert reg.get("my_item") is my_func

    def test_decorator_with_meta(self):
        reg = Registry("test")
        meta = ExtensionMeta(name="my_item", kind="test", description="d")

        @reg.decorator("my_item", meta=meta)
        def my_func():
            pass

        assert reg.get_meta("my_item") is meta

    def test_decorator_override(self):
        reg = Registry("test")

        @reg.decorator("foo")
        def first():
            pass

        @reg.decorator("foo", override=True)
        def second():
            pass

        assert reg.get("foo") is second
